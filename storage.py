"""Local persistence for idempotency, audit, and event lifecycle.

Design decisions
----------------
* **WAL mode** and ``busy_timeout`` are set on every connection to
  prevent concurrent readers/writers from blocking each other under
  FastAPI's thread-pool execution.
* The ``status`` column tracks the full event lifecycle:
  ``received → processing → processed | failed``.  This is critical
  because the original design had no way to distinguish "received and
  queued" from "business logic completed successfully."  A background
  recovery sweep can find stale ``processing`` rows and retry them.
* Two deduplication columns exist:
  - ``idempotency_key``: semantic (smartlockId:feature:timestamp)
  - ``raw_hash``: SHA-256 of exact payload bytes

For horizontal scaling, replace this module with a PostgreSQL-backed
implementation using advisory locks and ``ON CONFLICT``.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .enums import EventStatus
from .models import StoredEvent

logger = logging.getLogger(__name__)


class EventStore:
    """SQLite-backed event store with lifecycle tracking.

    Parameters
    ----------
    sqlite_path:
        Filesystem path to the database file.  Parent directories are
        created automatically.
    """

    def __init__(self, sqlite_path: str) -> None:
        self._path = sqlite_path
        self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with production-safe pragmas."""
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            # WAL allows concurrent readers during writes.
            conn.execute("PRAGMA journal_mode=WAL")
            # Wait up to 5 s for a write lock instead of failing immediately.
            conn.execute("PRAGMA busy_timeout=5000")
            # Enforce foreign keys (future-proofing).
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS webhook_events (
                    idempotency_key  TEXT    PRIMARY KEY,
                    raw_hash         TEXT    NOT NULL,
                    received_at      TEXT    NOT NULL,
                    status           TEXT    NOT NULL DEFAULT 'received',
                    feature          TEXT,
                    smartlock_id     INTEGER,
                    event_timestamp  TEXT,
                    error_detail     TEXT,
                    updated_at       TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_status
                ON webhook_events (status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_received
                ON webhook_events (received_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_raw_hash
                ON webhook_events (raw_hash)
            """)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def try_insert(self, record: StoredEvent) -> bool:
        """Attempt to insert a new event.

        Returns ``True`` if the row was inserted (new event),
        ``False`` if the idempotency key already existed (duplicate).
        Uses ``INSERT OR IGNORE`` which is atomic against the
        PRIMARY KEY constraint — no TOCTOU race.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO webhook_events (
                    idempotency_key, raw_hash, received_at, status,
                    feature, smartlock_id, event_timestamp, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.idempotency_key,
                    record.raw_hash,
                    record.received_at.isoformat(),
                    record.status,
                    record.feature,
                    record.smartlock_id,
                    record.event_timestamp.isoformat()
                    if record.event_timestamp else None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def is_raw_duplicate(self, raw_hash: str) -> bool:
        """Fast-path check for byte-identical payloads."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM webhook_events WHERE raw_hash = ? LIMIT 1",
                (raw_hash,),
            ).fetchone()
            return row is not None

    def mark_processing(self, idempotency_key: str) -> bool:
        """Transition ``received → processing``.

        Returns ``True`` if the transition succeeded.  Returns
        ``False`` if the event was already processing or completed
        (prevents double-execution).
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE webhook_events
                SET status = ?, updated_at = ?
                WHERE idempotency_key = ? AND status = ?
                """,
                (
                    EventStatus.PROCESSING,
                    datetime.now(UTC).isoformat(),
                    idempotency_key,
                    EventStatus.RECEIVED,
                ),
            )
            return cursor.rowcount > 0

    def mark_processed(self, idempotency_key: str) -> None:
        """Transition ``processing → processed``."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE webhook_events
                SET status = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    EventStatus.PROCESSED,
                    datetime.now(UTC).isoformat(),
                    idempotency_key,
                ),
            )

    def mark_failed(self, idempotency_key: str, error: str) -> None:
        """Transition to ``failed`` with error detail for diagnostics."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE webhook_events
                SET status = ?, error_detail = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (
                    EventStatus.FAILED,
                    error[:2000],  # truncate to avoid unbounded storage
                    datetime.now(UTC).isoformat(),
                    idempotency_key,
                ),
            )

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def find_stale_processing(self, older_than_seconds: int = 600) -> list[str]:
        """Find events stuck in ``processing`` beyond a threshold.

        These likely represent crashed background tasks and should be
        retried or escalated.
        """
        threshold = datetime.now(UTC).timestamp() - older_than_seconds
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT idempotency_key FROM webhook_events
                WHERE status = ?
                  AND strftime('%s', updated_at) < ?
                """,
                (EventStatus.PROCESSING, str(threshold)),
            ).fetchall()
            return [row["idempotency_key"] for row in rows]

    def reset_to_received(self, idempotency_key: str) -> None:
        """Reset a stale ``processing`` event back to ``received``."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE webhook_events
                SET status = ?, error_detail = NULL, updated_at = ?
                WHERE idempotency_key = ? AND status = ?
                """,
                (
                    EventStatus.RECEIVED,
                    datetime.now(UTC).isoformat(),
                    idempotency_key,
                    EventStatus.PROCESSING,
                ),
            )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune_older_than_days(self, days: int = 90) -> int:
        """Delete processed events older than ``days``.

        Failed events are intentionally kept for investigation.
        """
        threshold_iso = datetime.fromtimestamp(
            datetime.now(UTC).timestamp() - (days * 86400), tz=UTC,
        ).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM webhook_events
                WHERE status = ? AND received_at < ?
                """,
                (EventStatus.PROCESSED, threshold_iso),
            )
            deleted = cursor.rowcount
        if deleted:
            logger.info("Pruned %d processed events older than %d days", deleted, days)
        return deleted

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Verify database connectivity and writability.

        Performs a real write/read/delete cycle rather than just
        ``SELECT 1`` so that filesystem issues (full disk, permission
        errors after deployment) are caught.
        """
        sentinel = "__healthcheck__"
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO webhook_events (
                        idempotency_key, raw_hash, received_at, status
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (sentinel, sentinel, datetime.now(UTC).isoformat(), "healthcheck"),
                )
                conn.execute(
                    "DELETE FROM webhook_events WHERE idempotency_key = ?",
                    (sentinel,),
                )
            return True
        except Exception:
            logger.exception("Storage health check failed")
            return False
