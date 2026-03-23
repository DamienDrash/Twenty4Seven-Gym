from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from .auth import hash_password
from .enums import AccessCodeStatus, AccessWindowStatus, UserRole

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS members (
    id BIGSERIAL PRIMARY KEY,
    magicline_customer_id BIGINT NOT NULL UNIQUE,
    email TEXT,
    first_name TEXT,
    last_name TEXT,
    status TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS member_entitlements (
    member_id BIGINT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    has_xxlarge BOOLEAN NOT NULL DEFAULT FALSE,
    has_free_training_product BOOLEAN NOT NULL DEFAULT FALSE,
    raw_source JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bookings (
    id BIGSERIAL PRIMARY KEY,
    magicline_booking_id BIGINT NOT NULL UNIQUE,
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    booking_status TEXT NOT NULL,
    appointment_status TEXT,
    participant_status TEXT,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    magicline_updated_at TIMESTAMPTZ,
    source_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_bookings_member_id ON bookings (member_id);
CREATE INDEX IF NOT EXISTS idx_bookings_start_at ON bookings (start_at);

CREATE TABLE IF NOT EXISTS access_windows (
    id BIGSERIAL PRIMARY KEY,
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    booking_id BIGINT NOT NULL UNIQUE REFERENCES bookings(id) ON DELETE CASCADE,
    booking_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    booking_count INTEGER NOT NULL DEFAULT 1,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    dispatch_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    access_reason TEXT NOT NULL,
    last_computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_windows_dispatch_at
ON access_windows (dispatch_at, status);

CREATE TABLE IF NOT EXISTS access_codes (
    id BIGSERIAL PRIMARY KEY,
    access_window_id BIGINT NOT NULL REFERENCES access_windows(id) ON DELETE CASCADE,
    nuki_auth_id BIGINT,
    code_hash TEXT NOT NULL,
    code_last4 TEXT NOT NULL,
    status TEXT NOT NULL,
    is_emergency BOOLEAN NOT NULL DEFAULT FALSE,
    emailed_at TIMESTAMPTZ,
    activated_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    replaced_by_code_id BIGINT REFERENCES access_codes(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_access_codes_window_active
ON access_codes (access_window_id)
WHERE status IN ('pending', 'provisioned', 'emailed');

CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    severity TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id BIGSERIAL PRIMARY KEY,
    actor_email TEXT NOT NULL,
    action TEXT NOT NULL,
    access_window_id BIGINT,
    access_code_id BIGINT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, event_id)
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS access_window_checkins (
    id BIGSERIAL PRIMARY KEY,
    access_window_id BIGINT NOT NULL UNIQUE REFERENCES access_windows(id) ON DELETE CASCADE,
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    rules_accepted BOOLEAN NOT NULL DEFAULT FALSE,
    checklist JSONB NOT NULL DEFAULT '[]'::jsonb,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_window_checkins_member_id
ON access_window_checkins (member_id, confirmed_at DESC);

CREATE TABLE IF NOT EXISTS access_window_checkouts (
    id BIGSERIAL PRIMARY KEY,
    access_window_id BIGINT NOT NULL UNIQUE REFERENCES access_windows(id) ON DELETE CASCADE,
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    checklist JSONB NOT NULL DEFAULT '[]'::jsonb,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_window_checkouts_member_id
ON access_window_checkouts (member_id, confirmed_at DESC);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS funnel_templates (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    funnel_type TEXT NOT NULL,
    description TEXT DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS funnel_steps (
    id BIGSERIAL PRIMARY KEY,
    template_id BIGINT NOT NULL REFERENCES funnel_templates(id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT DEFAULT NULL,
    image_path TEXT DEFAULT NULL,
    requires_note BOOLEAN NOT NULL DEFAULT FALSE,
    requires_photo BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (template_id, step_order)
);

CREATE TABLE IF NOT EXISTS funnel_submissions (
    id BIGSERIAL PRIMARY KEY,
    access_window_id BIGINT NOT NULL REFERENCES access_windows(id) ON DELETE CASCADE,
    template_id BIGINT NOT NULL REFERENCES funnel_templates(id) ON DELETE CASCADE,
    entry_source TEXT NOT NULL,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS funnel_step_events (
    id BIGSERIAL PRIMARY KEY,
    submission_id BIGINT NOT NULL REFERENCES funnel_submissions(id) ON DELETE CASCADE,
    step_id BIGINT NOT NULL REFERENCES funnel_steps(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    note TEXT,
    photo_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_id
ON password_reset_tokens (user_id, expires_at DESC);
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self._pool = ConnectionPool(
            conninfo=dsn,
            kwargs={"row_factory": dict_row},
            min_size=1,
            max_size=5,
            open=False,
        )

    def open(self) -> None:
        self._pool.open()

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            yield conn

    def ensure_schema(self) -> None:
        lock_id = 1234567890
        schema_needed = False
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.users')")
                    row = cur.fetchone()
                    schema_needed = row is None or row.get("to_regclass") is None
                if schema_needed:
                    with conn.cursor() as cur:
                        cur.execute(SCHEMA_SQL)
                        cur.execute(
                            """
                            ALTER TABLE access_windows
                            ADD COLUMN IF NOT EXISTS booking_ids JSONB NOT NULL DEFAULT '[]'::jsonb
                            """
                        )
                        cur.execute(
                            """
                            ALTER TABLE access_windows
                            ADD COLUMN IF NOT EXISTS booking_count INTEGER NOT NULL DEFAULT 1
                            """
                        )
                        cur.execute(
                            """
                            ALTER TABLE access_window_checkouts
                            ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'checks-funnel'
                            """
                        )
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
            conn.commit()

    def health_check(self) -> bool:
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            logger.exception("Database health check failed")
            return False

    def bootstrap_admin(self, email: str, password: str) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, role)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (email)
                    DO UPDATE SET
                        password_hash = EXCLUDED.password_hash,
                        role = EXCLUDED.role,
                        is_active = TRUE,
                        updated_at = NOW()
                    """,
                    (email.lower(), hash_password(password), UserRole.ADMIN),
                )
            conn.commit()

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, password_hash, role, is_active FROM users WHERE email = %s",
                (email.lower(),),
            )
            return cur.fetchone()

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, password_hash, role, is_active
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            return cur.fetchone()

    def list_users(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, email, role, is_active
                FROM users
                ORDER BY email ASC
                LIMIT %s OFFSET %s
                """
                ,
                (limit, offset),
            )
            return list(cur.fetchall())

    def create_user(
        self,
        *,
        email: str,
        password: str,
        role: UserRole,
        is_active: bool,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (email, password_hash, role, is_active)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, email, role, is_active
                    """,
                    (email.lower(), hash_password(password), role, is_active),
                )
                row = cur.fetchone()
            conn.commit()
            return row

    def update_user(
        self,
        *,
        user_id: int,
        role: UserRole,
        is_active: bool,
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET role = %s,
                        is_active = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, email, role, is_active
                    """,
                    (role, is_active, user_id),
                )
                row = cur.fetchone()
            conn.commit()
            return row

    def set_user_password(self, *, user_id: int, password: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET password_hash = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, email, role, is_active
                    """,
                    (hash_password(password), user_id),
                )
                row = cur.fetchone()
            conn.commit()
            return row

    def list_members(
        self,
        *,
        email_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            if email_filter:
                cur.execute(
                    """
                    SELECT id, magicline_customer_id, email, first_name,
                           last_name, status, last_synced_at
                    FROM members
                    WHERE lower(email) = lower(%s)
                    ORDER BY last_synced_at DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (email_filter, limit, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT id, magicline_customer_id, email, first_name,
                           last_name, status, last_synced_at
                    FROM members
                    ORDER BY last_synced_at DESC, id DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
            return list(cur.fetchall())

    def get_member_by_id(self, *, member_id: int) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, magicline_customer_id, email, first_name, last_name,
                       status, last_synced_at
                FROM members
                WHERE id = %s
                LIMIT 1
                """,
                (member_id,),
            )
            return cur.fetchone()

    def list_member_bookings(self, *, member_id: int) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, magicline_booking_id, title, booking_status, appointment_status,
                       participant_status, start_at, end_at, source_received_at
                FROM bookings
                WHERE member_id = %s
                ORDER BY start_at DESC, id DESC
                LIMIT 100
                """,
                (member_id,),
            )
            return list(cur.fetchall())

    def list_member_access_windows(self, *, member_id: int) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT aw.id, aw.member_id, aw.booking_id, aw.booking_ids,
                       aw.booking_count, aw.starts_at,
                       aw.ends_at, aw.dispatch_at, aw.status, aw.access_reason,
                       TRUE AS check_in_required,
                       awc.confirmed_at AS check_in_confirmed_at,
                       awc.source AS check_in_source,
                       COALESCE(awc.checklist, '[]'::jsonb) AS check_in_checklist
                FROM access_windows aw
                LEFT JOIN access_window_checkins awc
                    ON awc.access_window_id = aw.id
                WHERE aw.member_id = %s
                ORDER BY aw.starts_at DESC, aw.id DESC
                LIMIT 100
                """,
                (member_id,),
            )
            return list(cur.fetchall())

    def list_member_access_codes(self, *, member_id: int) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ac.id, ac.access_window_id, ac.nuki_auth_id, ac.code_last4, ac.status,
                       ac.is_emergency, ac.emailed_at, ac.activated_at, ac.expires_at,
                       ac.replaced_by_code_id, ac.created_at
                FROM access_codes ac
                JOIN access_windows aw ON aw.id = ac.access_window_id
                WHERE aw.member_id = %s
                ORDER BY ac.created_at DESC, ac.id DESC
                LIMIT 100
                """,
                (member_id,),
            )
            return list(cur.fetchall())

    def list_access_windows(
        self,
        *,
        status_filter: str | None = None,
        member_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            if status_filter and member_id is not None:
                cur.execute(
                    """
                    SELECT aw.id, aw.member_id, aw.booking_id, aw.booking_count,
                           aw.starts_at, aw.ends_at,
                           aw.dispatch_at, aw.status, aw.access_reason,
                           TRUE AS check_in_required,
                           awc.confirmed_at AS check_in_confirmed_at,
                           awc.source AS check_in_source
                    FROM access_windows aw
                    LEFT JOIN access_window_checkins awc
                        ON awc.access_window_id = aw.id
                    WHERE aw.status = %s AND aw.member_id = %s
                    ORDER BY aw.dispatch_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (status_filter, member_id, limit, offset),
                )
            elif status_filter:
                cur.execute(
                    """
                    SELECT aw.id, aw.member_id, aw.booking_id, aw.booking_count,
                           aw.starts_at, aw.ends_at,
                           aw.dispatch_at, aw.status, aw.access_reason,
                           TRUE AS check_in_required,
                           awc.confirmed_at AS check_in_confirmed_at,
                           awc.source AS check_in_source
                    FROM access_windows aw
                    LEFT JOIN access_window_checkins awc
                        ON awc.access_window_id = aw.id
                    WHERE aw.status = %s
                    ORDER BY aw.dispatch_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (status_filter, limit, offset),
                )
            elif member_id is not None:
                cur.execute(
                    """
                    SELECT aw.id, aw.member_id, aw.booking_id, aw.booking_count,
                           aw.starts_at, aw.ends_at,
                           aw.dispatch_at, aw.status, aw.access_reason,
                           TRUE AS check_in_required,
                           awc.confirmed_at AS check_in_confirmed_at,
                           awc.source AS check_in_source
                    FROM access_windows aw
                    LEFT JOIN access_window_checkins awc
                        ON awc.access_window_id = aw.id
                    WHERE aw.member_id = %s
                    ORDER BY aw.dispatch_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (member_id, limit, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT aw.id, aw.member_id, aw.booking_id, aw.booking_count,
                           aw.starts_at, aw.ends_at,
                           aw.dispatch_at, aw.status, aw.access_reason,
                           TRUE AS check_in_required,
                           awc.confirmed_at AS check_in_confirmed_at,
                           awc.source AS check_in_source
                    FROM access_windows aw
                    LEFT JOIN access_window_checkins awc
                        ON awc.access_window_id = aw.id
                    ORDER BY aw.dispatch_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
            return list(cur.fetchall())

    def list_alerts(
        self,
        *,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            if severity:
                cur.execute(
                    """
                    SELECT id, severity, kind, message, created_at
                    FROM alerts
                    WHERE severity = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (severity, limit, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT id, severity, kind, message, created_at
                    FROM alerts
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
            return list(cur.fetchall())

    def create_admin_action(
        self,
        *,
        actor_email: str,
        action: str,
        access_window_id: int | None = None,
        access_code_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO admin_actions (
                        actor_email, action, access_window_id, access_code_id, payload
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (actor_email, action, access_window_id, access_code_id, Json(payload or {})),
                )
            conn.commit()

    def list_admin_actions(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, actor_email, action, access_window_id,
                       access_code_id, payload, created_at
                FROM admin_actions
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            return list(cur.fetchall())

    def list_lock_events(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, actor_email, action, access_window_id,
                       access_code_id, payload, created_at
                FROM admin_actions
                WHERE action IN (
                    'remote-open',
                    'resend-access-code',
                    'issue-emergency-code',
                    'deactivate-access-window'
                )
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            return list(cur.fetchall())

    def create_alert(
        self,
        *,
        severity: str,
        kind: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts (severity, kind, message, payload)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (severity, kind, message, Json(payload or {})),
                )
            conn.commit()

    def record_webhook_event(
        self,
        *,
        provider: str,
        event_id: str,
        event_type: str | None,
        payload: dict[str, Any],
    ) -> bool:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO webhook_events (provider, event_id, event_type, payload)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (provider, event_id) DO NOTHING
                    RETURNING id
                    """,
                    (provider, event_id, event_type, Json(payload)),
                )
                created = cur.fetchone() is not None
            conn.commit()
            return created

    def get_system_setting(self, key: str) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM system_settings WHERE key = %s", (key,))
            row = cur.fetchone()
            if not row:
                return None
            return row["value"]

    def set_system_setting(self, *, key: str, value: dict[str, Any]) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key)
                    DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = NOW()
                    """,
                    (key, Json(value)),
                )
            conn.commit()

    def create_password_reset_token(
        self,
        *,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE password_reset_tokens
                    SET used_at = NOW()
                    WHERE user_id = %s
                      AND used_at IS NULL
                    """,
                    (user_id,),
                )
                cur.execute(
                    """
                    INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, token_hash, expires_at),
                )
            conn.commit()

    def consume_password_reset_token(
        self,
        *,
        token_hash: str,
        password: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT prt.user_id
                    FROM password_reset_tokens prt
                    WHERE prt.token_hash = %s
                      AND prt.used_at IS NULL
                      AND prt.expires_at >= %s
                    LIMIT 1
                    """,
                    (token_hash, now),
                )
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    return None
                user_id = int(row["user_id"])
                cur.execute(
                    """
                    UPDATE password_reset_tokens
                    SET used_at = NOW()
                    WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                cur.execute(
                    """
                    UPDATE users
                    SET password_hash = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, email, role, is_active
                    """,
                    (hash_password(password), user_id),
                )
                user = cur.fetchone()
            conn.commit()
            return user

    def upsert_member(self, customer: dict[str, Any]) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO members (
                        magicline_customer_id, email, first_name, last_name,
                        status, raw_payload, last_synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (magicline_customer_id)
                    DO UPDATE SET
                        email = EXCLUDED.email,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name,
                        status = EXCLUDED.status,
                        raw_payload = EXCLUDED.raw_payload,
                        last_synced_at = NOW()
                    RETURNING id
                    """,
                    (
                        customer["id"],
                        customer.get("email"),
                        customer.get("first_name"),
                        customer.get("last_name"),
                        customer.get("status"),
                        Json(customer),
                    ),
                )
                member_id = int(cur.fetchone()["id"])
            conn.commit()
            return member_id

    def upsert_entitlement(
        self,
        *,
        member_id: int,
        has_xxlarge: bool,
        has_free_training_product: bool,
        raw_source: dict[str, Any],
    ) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO member_entitlements (
                        member_id, has_xxlarge, has_free_training_product,
                        raw_source, last_synced_at
                    )
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (member_id)
                    DO UPDATE SET
                        has_xxlarge = EXCLUDED.has_xxlarge,
                        has_free_training_product = EXCLUDED.has_free_training_product,
                        raw_source = EXCLUDED.raw_source,
                        last_synced_at = NOW()
                    """,
                    (member_id, has_xxlarge, has_free_training_product, Json(raw_source)),
                )
            conn.commit()

    def upsert_booking(
        self,
        *,
        member_id: int,
        booking: dict[str, Any],
        source_received_at: datetime,
    ) -> int:
        magicline_updated_at = booking.get("updatedAt") or booking.get("lastModifiedAt")
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bookings (
                        magicline_booking_id, member_id, title, booking_status,
                        appointment_status, participant_status, start_at, end_at,
                        magicline_updated_at, source_received_at, raw_payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (magicline_booking_id)
                    DO UPDATE SET
                        member_id = EXCLUDED.member_id,
                        title = EXCLUDED.title,
                        booking_status = EXCLUDED.booking_status,
                        appointment_status = EXCLUDED.appointment_status,
                        participant_status = EXCLUDED.participant_status,
                        start_at = EXCLUDED.start_at,
                        end_at = EXCLUDED.end_at,
                        magicline_updated_at = EXCLUDED.magicline_updated_at,
                        source_received_at = EXCLUDED.source_received_at,
                        raw_payload = EXCLUDED.raw_payload
                    RETURNING id
                    """,
                    (
                        booking["booking_id"],
                        member_id,
                        booking["title"],
                        booking["booking_status"],
                        booking.get("appointment_status"),
                        booking.get("participant_status"),
                        booking["start_date_time"],
                        booking["end_date_time"],
                        magicline_updated_at,
                        source_received_at,
                        Json(booking),
                    ),
                )
                booking_id = int(cur.fetchone()["id"])
            conn.commit()
            return booking_id

    def list_member_access_bookings(self, *, member_id: int, title: str) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, member_id, title, booking_status, start_at, end_at
                FROM bookings
                WHERE member_id = %s
                  AND title = %s
                  AND end_at >= NOW()
                  AND booking_status = 'BOOKED'
                ORDER BY start_at ASC, id ASC
                """,
                (member_id, title),
            )
            return list(cur.fetchall())

    def upsert_access_window(
        self,
        *,
        member_id: int,
        booking_id: int,
        booking_ids: list[int],
        booking_count: int,
        starts_at: datetime,
        ends_at: datetime,
        dispatch_at: datetime,
        status: str,
        access_reason: str,
    ) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO access_windows (
                        member_id, booking_id, booking_ids, booking_count,
                        starts_at, ends_at, dispatch_at,
                        status, access_reason, last_computed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (booking_id)
                    DO UPDATE SET
                        member_id = EXCLUDED.member_id,
                        booking_ids = EXCLUDED.booking_ids,
                        booking_count = EXCLUDED.booking_count,
                        starts_at = EXCLUDED.starts_at,
                        ends_at = EXCLUDED.ends_at,
                        dispatch_at = EXCLUDED.dispatch_at,
                        status = EXCLUDED.status,
                        access_reason = EXCLUDED.access_reason,
                        last_computed_at = NOW()
                    RETURNING id
                    """,
                    (
                        member_id,
                        booking_id,
                        Json(booking_ids),
                        booking_count,
                        starts_at,
                        ends_at,
                        dispatch_at,
                        status,
                        access_reason,
                    ),
                )
                window_id = int(cur.fetchone()["id"])
            conn.commit()
            return window_id

    def prune_member_windows(self, *, member_id: int, keep_booking_ids: list[int]) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                if keep_booking_ids:
                    cur.execute(
                        """
                        DELETE FROM access_windows aw
                        WHERE aw.member_id = %s
                          AND aw.status IN (%s, %s, %s)
                          AND aw.booking_id <> ALL(%s)
                        """,
                        (
                            member_id,
                            AccessWindowStatus.SCHEDULED,
                            AccessWindowStatus.CANCELED,
                            AccessWindowStatus.FLAGGED,
                            keep_booking_ids,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        DELETE FROM access_windows aw
                        WHERE aw.member_id = %s
                          AND aw.status IN (%s, %s, %s)
                        """,
                        (
                            member_id,
                            AccessWindowStatus.SCHEDULED,
                            AccessWindowStatus.CANCELED,
                            AccessWindowStatus.FLAGGED,
                        ),
                    )
                deleted = cur.rowcount
            conn.commit()
            return deleted

    def due_access_windows(self, now: datetime) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT aw.id, aw.member_id, aw.booking_id, aw.starts_at,
                       aw.booking_count, aw.ends_at, aw.dispatch_at, m.email, m.first_name,
                       m.last_name
                FROM access_windows aw
                JOIN members m ON m.id = aw.member_id
                LEFT JOIN access_codes ac
                    ON ac.access_window_id = aw.id
                   AND ac.status IN (%s, %s, %s)
                WHERE aw.status = %s
                  AND aw.dispatch_at <= %s
                  AND ac.id IS NULL
                ORDER BY aw.dispatch_at ASC
                """,
                (
                    AccessCodeStatus.PENDING,
                    AccessCodeStatus.PROVISIONED,
                    AccessCodeStatus.EMAILED,
                    AccessWindowStatus.SCHEDULED,
                    now,
                ),
            )
            return list(cur.fetchall())

    def get_access_window_detail(self, *, access_window_id: int) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT aw.id, aw.member_id, aw.booking_id, aw.booking_ids, aw.booking_count,
                       aw.starts_at, aw.ends_at, aw.dispatch_at, aw.status, aw.access_reason,
                       m.email, m.first_name, m.last_name,
                       TRUE AS check_in_required,
                       awc.confirmed_at AS check_in_confirmed_at,
                       awc.source AS check_in_source,
                       COALESCE(awc.checklist, '[]'::jsonb) AS check_in_checklist
                FROM access_windows aw
                JOIN members m ON m.id = aw.member_id
                LEFT JOIN access_window_checkins awc
                    ON awc.access_window_id = aw.id
                WHERE aw.id = %s
                LIMIT 1
                """,
                (access_window_id,),
            )
            return cur.fetchone()

    def verify_member_access_code(
        self,
        *,
        email: str,
        raw_code: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        from .auth import verify_password

        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT aw.id AS access_window_id, aw.member_id, aw.starts_at, aw.ends_at,
                       aw.status, m.first_name, m.email,
                       awc.confirmed_at, awc.source, ac.code_hash
                FROM access_windows aw
                JOIN members m ON m.id = aw.member_id
                JOIN access_codes ac ON ac.access_window_id = aw.id
                LEFT JOIN access_window_checkins awc
                    ON awc.access_window_id = aw.id
                WHERE lower(m.email) = lower(%s)
                  AND aw.ends_at >= %s
                  AND aw.status IN (%s, %s)
                  AND ac.status IN (%s, %s)
                ORDER BY aw.starts_at ASC, aw.id ASC, ac.created_at DESC
                """,
                (
                    email,
                    now,
                    AccessWindowStatus.SCHEDULED,
                    AccessWindowStatus.ACTIVE,
                    AccessCodeStatus.PROVISIONED,
                    AccessCodeStatus.EMAILED,
                ),
            )
            for row in cur.fetchall():
                if verify_password(raw_code, row["code_hash"]):
                    row.pop("code_hash", None)
                    row["is_confirmed"] = row.get("confirmed_at") is not None
                    return row
            return None

    def get_check_in_window(self, *, access_window_id: int) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT aw.id AS access_window_id, aw.member_id, aw.starts_at, aw.ends_at,
                       aw.status, m.first_name, m.email,
                       awc.confirmed_at, awc.source,
                       CASE
                           WHEN awc.confirmed_at IS NOT NULL THEN TRUE
                           ELSE FALSE
                       END AS is_confirmed
                FROM access_windows aw
                JOIN members m ON m.id = aw.member_id
                LEFT JOIN access_window_checkins awc
                    ON awc.access_window_id = aw.id
                WHERE aw.id = %s
                LIMIT 1
                """,
                (access_window_id,),
            )
            return cur.fetchone()

    def upsert_access_window_checkin(
        self,
        *,
        access_window_id: int,
        member_id: int,
        source: str,
        rules_accepted: bool,
        checklist: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO access_window_checkins (
                        access_window_id, member_id, source, rules_accepted, checklist,
                        confirmed_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (access_window_id)
                    DO UPDATE SET
                        source = EXCLUDED.source,
                        rules_accepted = EXCLUDED.rules_accepted,
                        checklist = EXCLUDED.checklist,
                        confirmed_at = NOW(),
                        updated_at = NOW()
                    RETURNING access_window_id, confirmed_at, source, rules_accepted, checklist
                    """,
                    (access_window_id, member_id, source, rules_accepted, Json(checklist)),
                )
                row = cur.fetchone()
            conn.commit()
            return row

    def store_access_code(
        self,
        *,
        access_window_id: int,
        raw_code: str,
        nuki_auth_id: int | None,
        status: str,
        expires_at: datetime,
        is_emergency: bool = False,
    ) -> int:
        code_hash = hash_password(raw_code)
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO access_codes (
                        access_window_id, nuki_auth_id, code_hash, code_last4,
                        status, expires_at, is_emergency
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        access_window_id,
                        nuki_auth_id,
                        code_hash,
                        raw_code[-4:],
                        status,
                        expires_at,
                        is_emergency,
                    ),
                )
                code_id = int(cur.fetchone()["id"])
                cur.execute(
                    """
                    UPDATE access_windows
                    SET status = %s
                    WHERE id = %s
                    """,
                    (AccessWindowStatus.ACTIVE, access_window_id),
                )
            conn.commit()
            return code_id

    def mark_code_replaced(
        self,
        *,
        code_id: int,
        replaced_by_code_id: int | None = None,
    ) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE access_codes
                    SET status = %s, replaced_by_code_id = %s
                    WHERE id = %s
                    """,
                    (AccessCodeStatus.REPLACED, replaced_by_code_id, code_id),
                )
            conn.commit()

    def sync_window_code_expiry(self, *, access_window_id: int, expires_at: datetime) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE access_codes
                    SET expires_at = %s
                    WHERE access_window_id = %s
                      AND status IN (%s, %s)
                    """,
                    (
                        expires_at,
                        access_window_id,
                        AccessCodeStatus.PROVISIONED,
                        AccessCodeStatus.EMAILED,
                    ),
                )
                updated = cur.rowcount
            conn.commit()
            return updated

    def get_active_code_for_window(self, *, access_window_id: int) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, access_window_id, nuki_auth_id, status, expires_at
                FROM access_codes
                WHERE access_window_id = %s
                  AND status IN (%s, %s)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    access_window_id,
                    AccessCodeStatus.PROVISIONED,
                    AccessCodeStatus.EMAILED,
                ),
            )
            return cur.fetchone()

    def cancel_access_window(self, *, access_window_id: int) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE access_windows
                    SET status = %s
                    WHERE id = %s
                    """,
                    (AccessWindowStatus.CANCELED, access_window_id),
                )
                updated = cur.rowcount
            conn.commit()
            return updated

    def mark_code_emailed(self, code_id: int) -> None:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE access_codes
                    SET status = %s, emailed_at = NOW(), activated_at = NOW()
                    WHERE id = %s
                    """,
                    (AccessCodeStatus.EMAILED, code_id),
                )
            conn.commit()

    def expire_finished_windows(self, now: datetime) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE access_windows
                    SET status = %s
                    WHERE status IN (%s, %s) AND ends_at < %s
                    """,
                    (
                        AccessWindowStatus.EXPIRED,
                        AccessWindowStatus.SCHEDULED,
                        AccessWindowStatus.ACTIVE,
                        now,
                    ),
                )
                window_count = cur.rowcount
                cur.execute(
                    """
                    UPDATE access_codes
                    SET status = %s
                    WHERE status IN (%s, %s) AND expires_at < %s
                    """,
                    (
                        AccessCodeStatus.EXPIRED,
                        AccessCodeStatus.PROVISIONED,
                        AccessCodeStatus.EMAILED,
                        now,
                    ),
                )
            conn.commit()
            return window_count

    def list_funnel_templates(self) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, slug, funnel_type
                FROM funnel_templates
                ORDER BY funnel_type, name
                """,
            )
            return list(cur.fetchall())

    def get_funnel_template_detail(self, template_id: int) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, slug, funnel_type, description
                FROM funnel_templates
                WHERE id = %s
                """,
                (template_id,),
            )
            template = cur.fetchone()
            if not template:
                return None
            cur.execute(
                """
                SELECT id, template_id, step_order, title, body, image_path,
                       requires_note, requires_photo
                FROM funnel_steps
                WHERE template_id = %s
                ORDER BY step_order
                """,
                (template_id,),
            )
            template["steps"] = list(cur.fetchall())
            return template

    def upsert_funnel_template(
        self,
        *,
        template_id: int | None,
        name: str,
        slug: str,
        funnel_type: str,
        description: str | None,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                if template_id:
                    cur.execute(
                        """
                        UPDATE funnel_templates
                        SET name = %s, slug = %s, funnel_type = %s,
                            description = %s, updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, name, slug, funnel_type, description
                        """,
                        (name, slug, funnel_type, description, template_id),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO funnel_templates (name, slug, funnel_type, description)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id, name, slug, funnel_type, description
                        """,
                        (name, slug, funnel_type, description),
                    )
                result = cur.fetchone()
            conn.commit()
            return result

    def upsert_funnel_step(
        self,
        *,
        step_id: int | None,
        template_id: int,
        step_order: int,
        title: str,
        body: str | None,
        image_path: str | None,
        requires_note: bool,
        requires_photo: bool,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                if step_id:
                    cur.execute(
                        """
                        UPDATE funnel_steps
                        SET step_order = %s, title = %s, body = %s,
                            image_path = %s, requires_note = %s,
                            requires_photo = %s, updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, template_id, step_order, title, body,
                                  image_path, requires_note, requires_photo
                        """,
                        (
                            step_order,
                            title,
                            body,
                            image_path,
                            requires_note,
                            requires_photo,
                            step_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO funnel_steps (template_id, step_order, title, body,
                                                  image_path, requires_note, requires_photo)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, template_id, step_order, title, body,
                                  image_path, requires_note, requires_photo
                        """,
                        (
                            template_id,
                            step_order,
                            title,
                            body,
                            image_path,
                            requires_note,
                            requires_photo,
                        ),
                    )
                result = cur.fetchone()
            conn.commit()
            return result

    def delete_funnel_step(self, *, step_id: int) -> None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM funnel_steps WHERE id = %s", (step_id,))
        conn.commit()

    def create_funnel_submission(
        self,
        *,
        access_window_id: int,
        template_id: int,
        entry_source: str,
        success: bool,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO funnel_submissions (
                        access_window_id, template_id, entry_source, success
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, access_window_id, template_id, entry_source, success, created_at
                    """,
                    (access_window_id, template_id, entry_source, success),
                )
                row = cur.fetchone()
            conn.commit()
            return row

    def create_funnel_step_event(
        self,
        *,
        submission_id: int,
        step_id: int,
        status: str,
        note: str | None,
        photo_path: str | None,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO funnel_step_events (
                        submission_id, step_id, status, note, photo_path
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, submission_id, step_id, status, note, photo_path, created_at
                    """,
                    (submission_id, step_id, status, note, photo_path),
                )
                row = cur.fetchone()
            conn.commit()
            return row

    def get_funnel_by_type(self, funnel_type: str) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, slug, funnel_type, description
                FROM funnel_templates
                WHERE funnel_type = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (funnel_type,),
            )
            template = cur.fetchone()
            if not template:
                return None
            cur.execute(
                """
                SELECT id, template_id, step_order, title, body, image_path,
                       requires_note, requires_photo
                FROM funnel_steps
                WHERE template_id = %s
                ORDER BY step_order ASC
                """,
                (template["id"],),
            )
            template["steps"] = list(cur.fetchall())
            return template

    def list_member_windows_with_status(
        self,
        *,
        member_id: int,
        from_dt: datetime,
    ) -> list[dict[str, Any]]:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    aw.id,
                    aw.starts_at,
                    aw.ends_at,
                    aw.status,
                    aw.booking_count,
                    aw.access_reason,
                    awci.confirmed_at AS checkin_confirmed_at,
                    awco.confirmed_at AS checkout_confirmed_at
                FROM access_windows aw
                LEFT JOIN access_window_checkins awci
                    ON awci.access_window_id = aw.id
                LEFT JOIN access_window_checkouts awco
                    ON awco.access_window_id = aw.id
                WHERE aw.member_id = %s
                  AND aw.ends_at >= %s
                  AND aw.status IN ('scheduled', 'active')
                ORDER BY aw.starts_at ASC
                LIMIT 20
                """,
                (member_id, from_dt),
            )
            return list(cur.fetchall())

    def upsert_window_checkout(
        self,
        *,
        access_window_id: int,
        member_id: int,
        source: str,
        checklist: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO access_window_checkouts (
                        access_window_id, member_id, source, checklist,
                        confirmed_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (access_window_id)
                    DO UPDATE SET
                        source = EXCLUDED.source,
                        checklist = EXCLUDED.checklist,
                        confirmed_at = NOW(),
                        updated_at = NOW()
                    RETURNING access_window_id, confirmed_at, source, checklist
                    """,
                    (access_window_id, member_id, source, Json(checklist)),
                )
                row = cur.fetchone()
            conn.commit()
            return row

    def get_window_checkout(self, *, access_window_id: int) -> dict[str, Any] | None:
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT access_window_id, confirmed_at, source, checklist
                FROM access_window_checkouts
                WHERE access_window_id = %s
                """,
                (access_window_id,),
            )
            return cur.fetchone()
