"""House rules document management and acknowledgement tracking.

Provides CRUD for versioned house rules documents and
revision-safe acknowledgement recording for member check-ins.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from ..db import Database

logger = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """SHA-256 hash of the document text for revision-safe tracking."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Document CRUD ─────────────────────────────────────────────────

def get_active_house_rules(db: Database) -> dict[str, Any] | None:
    """Return the currently active house rules document."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, body_text, body_html, version, content_hash,
                   created_by, created_at
            FROM house_rules_documents
            WHERE is_active = TRUE
            ORDER BY version DESC LIMIT 1
            """
        )
        return cur.fetchone()


def get_house_rules_by_id(
    db: Database, document_id: int,
) -> dict[str, Any] | None:
    """Return a specific house rules document by ID."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, body_text, body_html, version, is_active,
                   content_hash, created_by, created_at
            FROM house_rules_documents WHERE id = %s
            """,
            (document_id,),
        )
        return cur.fetchone()


def list_house_rules_versions(
    db: Database, *, limit: int = 20,
) -> list[dict[str, Any]]:
    """List all house rules versions, newest first."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, version, is_active, content_hash,
                   created_by, created_at
            FROM house_rules_documents
            ORDER BY version DESC LIMIT %s
            """,
            (limit,),
        )
        return list(cur.fetchall())


def create_house_rules_version(
    db: Database,
    *,
    title: str,
    body_text: str,
    body_html: str | None,
    created_by: str,
) -> dict[str, Any]:
    """Create a new house rules version and set it as active.

    Previous versions are deactivated automatically.
    """
    content = _content_hash(body_text)
    with db.connection() as conn:
        with conn.cursor() as cur:
            # Get next version number
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_v "
                "FROM house_rules_documents"
            )
            next_version = int(cur.fetchone()["next_v"])

            # Deactivate all previous versions
            cur.execute(
                "UPDATE house_rules_documents SET is_active = FALSE"
            )

            # Insert new version
            cur.execute(
                """
                INSERT INTO house_rules_documents
                    (title, body_text, body_html, version, is_active,
                     content_hash, created_by)
                VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                RETURNING id, title, body_text, body_html, version,
                          is_active, content_hash, created_by, created_at
                """,
                (title, body_text, body_html, next_version,
                 content, created_by),
            )
            result = cur.fetchone()
        conn.commit()

    logger.info(
        "House rules v%d created by %s (hash=%s)",
        next_version, created_by, content[:12],
    )
    return result


# ── Acknowledgement Tracking ─────────────────────────────────────

def record_house_rules_acknowledgement(
    db: Database,
    *,
    member_id: int,
    document_id: int,
    access_window_id: int | None = None,
    submission_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Record a revision-safe house rules acknowledgement.

    Stores the document hash at the time of acknowledgement so the
    exact text the member confirmed can be proven later.
    """
    doc = get_house_rules_by_id(db, document_id)
    if not doc:
        raise ValueError("Hausordnungsdokument nicht gefunden.")

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO house_rules_acknowledgements
                    (member_id, document_id, access_window_id,
                     submission_id, document_hash, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, member_id, document_id, document_hash,
                          acknowledged_at
                """,
                (member_id, document_id, access_window_id,
                 submission_id, doc["content_hash"],
                 ip_address, user_agent),
            )
            result = cur.fetchone()
        conn.commit()

    logger.info(
        "House rules ack: member=%d doc=%d hash=%s",
        member_id, document_id, doc["content_hash"][:12],
    )
    return result


def get_member_acknowledgements(
    db: Database,
    *,
    member_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List all house rules acknowledgements for a member."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT hra.id, hra.document_id, hra.document_hash,
                   hra.acknowledged_at, hra.access_window_id,
                   hrd.title, hrd.version
            FROM house_rules_acknowledgements hra
            JOIN house_rules_documents hrd ON hrd.id = hra.document_id
            WHERE hra.member_id = %s
            ORDER BY hra.acknowledged_at DESC
            LIMIT %s
            """,
            (member_id, limit),
        )
        return list(cur.fetchall())


def get_latest_acknowledgement(
    db: Database,
    *,
    member_id: int,
) -> dict[str, Any] | None:
    """Return the most recent acknowledgement for a member."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT hra.id, hra.document_id, hra.document_hash,
                   hra.acknowledged_at, hrd.title, hrd.version
            FROM house_rules_acknowledgements hra
            JOIN house_rules_documents hrd ON hrd.id = hra.document_id
            WHERE hra.member_id = %s
            ORDER BY hra.acknowledged_at DESC LIMIT 1
            """,
            (member_id,),
        )
        return cur.fetchone()


def list_acknowledgements(
    db: Database,
    *,
    document_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List acknowledgements with member info, optionally filtered by document."""
    base = """
        SELECT hra.id, hra.member_id, hra.document_id, hra.document_hash,
               hra.acknowledged_at, hra.ip_address,
               m.email, m.first_name, m.last_name,
               hrd.title, hrd.version
        FROM house_rules_acknowledgements hra
        JOIN members m ON m.id = hra.member_id
        JOIN house_rules_documents hrd ON hrd.id = hra.document_id
    """
    params: list[Any] = []
    if document_id:
        base += " WHERE hra.document_id = %s"
        params.append(document_id)
    base += " ORDER BY hra.acknowledged_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(base, params)
        return list(cur.fetchall())
