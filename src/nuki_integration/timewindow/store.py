"""Persistenz für das Zeitfenster-PIN-Modell (opengym-DB, ``nuki_*`` Namespace).

Nutzt die vorhandene ``Database.connection()`` (psycopg-Pool, dict_row). Alle
Objekte werden per ``CREATE TABLE IF NOT EXISTS`` angelegt und fassen keine
Bestandstabellen an.

Tabellen
--------
- ``nuki_slots``        — die 96 festen Keypad-Slots (je Bucket × Pool-Index).
- ``nuki_pin_history``  — eine Zeile je Slot & Rotationstag (Tages-PIN + Status).
- ``nuki_assignments``  — Mitglied→Pool-Index-Historie für Anti-Repeat.

Hinweis PIN-Speicherung: der Tages-PIN wird im Klartext gehalten, weil das
Zeitfenster-Modell denselben Code über den Tag an mehrere Mitglieder zustellen
und aufs Keypad materialisieren muss (kein Passwort, sondern ein rotierender
Zutrittscode). Er wird beim nächsten Rotationstag überschrieben.
"""
from __future__ import annotations

from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nuki_slots (
    id            BIGSERIAL PRIMARY KEY,
    smartlock_id  BIGINT NOT NULL,
    name          TEXT NOT NULL,
    hour          SMALLINT NOT NULL,
    pool_index    SMALLINT NOT NULL,
    weekday_mask  INTEGER NOT NULL,
    from_time     INTEGER NOT NULL,
    until_time    INTEGER NOT NULL,
    nuki_auth_id  TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (smartlock_id, hour, pool_index)
);

CREATE TABLE IF NOT EXISTS nuki_pin_history (
    id             BIGSERIAL PRIMARY KEY,
    slot_id        BIGINT NOT NULL REFERENCES nuki_slots(id) ON DELETE CASCADE,
    rotation_date  DATE NOT NULL,
    pin            TEXT NOT NULL,
    pushed         BOOLEAN NOT NULL DEFAULT FALSE,
    materialised   BOOLEAN NOT NULL DEFAULT FALSE,
    dry_run        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (slot_id, rotation_date)
);

CREATE TABLE IF NOT EXISTS nuki_assignments (
    id             BIGSERIAL PRIMARY KEY,
    member_ref     TEXT NOT NULL,
    weekday        SMALLINT NOT NULL,
    hour           SMALLINT NOT NULL,
    pool_index     SMALLINT NOT NULL,
    assigned_date  DATE NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nuki_pin_history_slot ON nuki_pin_history(slot_id, rotation_date);
CREATE INDEX IF NOT EXISTS idx_nuki_assignments_member ON nuki_assignments(member_ref, weekday, hour, created_at);
"""


def ensure_schema(db) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


# ── Slots ─────────────────────────────────────────────────────────
def upsert_slot(db, *, smartlock_id: int, name: str, hour: int, pool_index: int,
                weekday_mask: int, from_time: int, until_time: int) -> int:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nuki_slots
                    (smartlock_id, name, hour, pool_index, weekday_mask, from_time, until_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (smartlock_id, hour, pool_index) DO UPDATE
                    SET name = EXCLUDED.name,
                        weekday_mask = EXCLUDED.weekday_mask,
                        from_time = EXCLUDED.from_time,
                        until_time = EXCLUDED.until_time,
                        updated_at = NOW()
                RETURNING id
                """,
                (smartlock_id, name, hour, pool_index, weekday_mask, from_time, until_time),
            )
            slot_id = int(cur.fetchone()["id"])
        conn.commit()
        return slot_id


def set_slot_auth_id(db, slot_id: int, nuki_auth_id: str | None) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE nuki_slots SET nuki_auth_id=%s, updated_at=NOW() WHERE id=%s",
                (nuki_auth_id, slot_id),
            )
        conn.commit()


def count_slots(db, smartlock_id: int) -> int:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM nuki_slots WHERE smartlock_id=%s", (smartlock_id,))
        return int(cur.fetchone()["n"])


# ── PIN-Historie ──────────────────────────────────────────────────
def record_rotation(db, *, slot_id: int, rotation_date, pin: str,
                    pushed: bool, materialised: bool, dry_run: bool) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nuki_pin_history
                    (slot_id, rotation_date, pin, pushed, materialised, dry_run)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (slot_id, rotation_date) DO UPDATE
                    SET pin = EXCLUDED.pin,
                        pushed = EXCLUDED.pushed,
                        materialised = EXCLUDED.materialised,
                        dry_run = EXCLUDED.dry_run
                """,
                (slot_id, rotation_date, pin, pushed, materialised, dry_run),
            )
        conn.commit()


def rotation_count_for_day(db, rotation_date) -> int:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM nuki_pin_history WHERE rotation_date=%s", (rotation_date,))
        return int(cur.fetchone()["n"])


def get_todays_slot_pin(db, *, smartlock_id: int, hour: int, pool_index: int, rotation_date) -> str | None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT h.pin
            FROM nuki_pin_history h
            JOIN nuki_slots s ON s.id = h.slot_id
            WHERE s.smartlock_id=%s AND s.hour=%s AND s.pool_index=%s AND h.rotation_date <= %s
            ORDER BY h.rotation_date DESC
            LIMIT 1
            """,
            (smartlock_id, hour, pool_index, rotation_date),
        )
        row = cur.fetchone()
        return row["pin"] if row else None


def rotation_status(db, rotation_date) -> dict[str, Any]:
    """Kompakter Status für die Admin-UI."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM nuki_slots")
        slots = int(cur.fetchone()["n"])
        cur.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE pushed) AS pushed,
                   COUNT(*) FILTER (WHERE materialised) AS materialised,
                   COUNT(*) FILTER (WHERE dry_run) AS dry_run
            FROM nuki_pin_history WHERE rotation_date=%s
            """,
            (rotation_date,),
        )
        r = cur.fetchone() or {}
        cur.execute(
            "SELECT COUNT(*) AS n FROM nuki_assignments WHERE assigned_date=%s", (rotation_date,)
        )
        assignments = int(cur.fetchone()["n"])
    return {
        "rotation_date": str(rotation_date),
        "slots": slots,
        "rotated_today": int(r.get("total") or 0),
        "pushed": int(r.get("pushed") or 0),
        "materialised": int(r.get("materialised") or 0),
        "dry_run": int(r.get("dry_run") or 0),
        "assignments_today": assignments,
    }


# ── Assignments (Anti-Repeat) ─────────────────────────────────────
def recent_pool_indices(db, *, member_ref: str, weekday: int, hour: int, limit: int = 4) -> list[int]:
    """Zuletzt vergebene Pool-Indizes für (Mitglied, Wochentag, Stunde), alt→neu."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT pool_index FROM nuki_assignments
            WHERE member_ref=%s AND weekday=%s AND hour=%s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (member_ref, weekday, hour, limit),
        )
        rows = [int(r["pool_index"]) for r in cur.fetchall()]
        return list(reversed(rows))


def record_assignment(db, *, member_ref: str, weekday: int, hour: int,
                      pool_index: int, assigned_date) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nuki_assignments (member_ref, weekday, hour, pool_index, assigned_date)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (member_ref, weekday, hour, pool_index, assigned_date),
            )
        conn.commit()
