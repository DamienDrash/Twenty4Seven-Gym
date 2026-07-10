"""Nuki error-event guardian: reconcile keypad codes on rejected/wrong-code events.

Trigger
-------
Nuki Advanced-API webhooks (DEVICE_LOGS feature) report smartlock log entries,
including *failed* keypad attempts. **What these events actually contain** (verified
against the Nuki Web API smartlock-log schema and this repo's existing
``/webhooks/nuki`` receiver): a log entry carries ``smartlockId``, ``deviceType``,
``name``, ``action``, ``trigger``, ``state``/``stateName``, ``source`` and a
timestamp. For security reasons Nuki does **not** deliver the actual PIN a person
typed on the keypad — a wrong code is only surfaced as a failed/unauthorised log
entry, never as the entered digits.

Consequence
-----------
Because the wrong code is never provided (and must not be invented), the guardian
does **not** try to identify "the offending code", and it does **not** depend on
recognising unknown numeric Nuki error codes. Instead, **every successfully
authenticated webhook is treated as a safe reconcile trigger**: for every
currently-relevant booking it re-materialises the code that was already
assigned/sent to that member for their FIRST booked hour, so a legitimate member
whose code silently fell off the keypad regains access. Classified error keywords
are recorded as an audit marker only — never a precondition. The same reconcile
also runs on a periodic worker fallback (:func:`run_guardian_cycle`) so it works
even when no webhook is delivered.

Safety properties
-----------------
- **Authenticated**: a configurable shared secret. Preferred transport is a bearer/
  query/path token (``Authorization: Bearer``, ``?token=`` or ``X-Webhook-Token``),
  compared constant-time. Optionally a Nuki HMAC-SHA256 signature over the raw body
  in ``X-Nuki-Signature-SHA256`` — documented format: lowercase **hex** digest,
  optionally ``sha256=`` prefixed. No secret is ever logged. Fail closed: no
  secret configured, or an invalid one → the event is rejected (HTTP 401).
- **Idempotent**: webhook events are de-duplicated (``webhook_events`` unique key);
  the reconcile itself is verify-then-maybe-repair, so re-running is a no-op.
- **Rate-limit safe**: at most one reconcile per cooldown window (atomic DB slot),
  shared by webhook trigger and worker fallback, and Nuki writes stay bounded (one
  verify + at most one repair per booking).
- **Audited**: every trigger and its result is persisted to ``nuki_guardian_audit``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from ..datetime_utils import now_utc, to_berlin_tz
from ..enums import AlertSeverity
from ..timewindow import pin_pool, store
from .alerts import create_operational_alert
from .nuki_verification import ensure_code_materialised
from .settings import get_effective_nuki_config

logger = logging.getLogger(__name__)

RECONCILE_LEAD_SECONDS = 7200  # 2h: bookings in progress or starting soon
BUFFER_DAYS = 30

GUARDIAN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nuki_guardian_runs (
    id           SMALLINT PRIMARY KEY DEFAULT 1,
    last_run_at  TIMESTAMPTZ NOT NULL,
    CONSTRAINT nuki_guardian_runs_single CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS nuki_guardian_audit (
    id                BIGSERIAL PRIMARY KEY,
    event_id          TEXT,
    trigger_kind      TEXT NOT NULL,
    verified          BOOLEAN NOT NULL DEFAULT FALSE,
    rate_limited      BOOLEAN NOT NULL DEFAULT FALSE,
    relevant_windows  INTEGER NOT NULL DEFAULT 0,
    repaired          INTEGER NOT NULL DEFAULT 0,
    failures          INTEGER NOT NULL DEFAULT 0,
    detail            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nuki_guardian_audit_created ON nuki_guardian_audit(created_at DESC);
"""


def ensure_schema(db) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(GUARDIAN_SCHEMA_SQL)
        conn.commit()


# ── Webhook authentication (pure, constant-time, never logs the secret) ────
def verify_webhook(
    *, secret: str, raw_body: bytes, signature: str | None = None, token: str | None = None
) -> bool:
    """Return True iff the webhook is authentic. Fail closed on missing secret.

    Preferred: a shared-secret ``token`` (bearer / query / header), compared
    constant-time against ``secret``. Optional: a Nuki HMAC-SHA256 ``signature``
    over the raw body in ``X-Nuki-Signature-SHA256`` — documented format is a
    lowercase **hex** digest, optionally ``sha256=`` prefixed. Both comparisons are
    constant-time. Empty ``secret`` (or neither credential supplied) → False.
    """
    if not secret:
        return False
    if signature:
        expected = hmac.new(secret.encode(), raw_body or b"", hashlib.sha256).hexdigest()
        provided = signature.split("=", 1)[1] if "=" in signature else signature
        return hmac.compare_digest(expected, provided.strip().lower())
    if token:
        return hmac.compare_digest(secret, token)
    return False


# ── Error-event detection (pure; never fabricates an entered code) ─────────
_ERROR_KEYWORDS = (
    "wrong", "invalid", "unauthor", "denied", "reject", "fail",
    "falsch", "ungültig", "ungueltig", "verweigert",
)


def _flatten_strings(entry: dict[str, Any]) -> str:
    return " ".join(str(v) for v in entry.values() if isinstance(v, (str, int, float))).lower()


def _looks_like_error(entry: dict[str, Any]) -> bool:
    haystack = _flatten_strings(entry)
    if any(kw in haystack for kw in _ERROR_KEYWORDS):
        return True
    # Explicit boolean success flag set to False.
    if entry.get("success") is False:
        return True
    return False


def _iter_entries(payload: Any):
    if isinstance(payload, list):
        for e in payload:
            yield e
        return
    if isinstance(payload, dict):
        found_child = False
        for key in ("logs", "events", "smartlockLogs", "data", "log"):
            value = payload.get(key)
            if isinstance(value, list):
                found_child = True
                for e in value:
                    yield e
            elif isinstance(value, dict):
                found_child = True
                yield value
        if not found_child:
            yield payload


def extract_error_events(payload: Any) -> list[dict[str, Any]]:
    """Return the subset of webhook entries that signal a rejected/failed access.

    Defensive across the shapes a Nuki DEVICE_LOGS webhook may take (single event,
    ``{"logs": [...]}``, or a bare list). Only *failure* signals are returned — never
    an entered PIN (Nuki does not provide it).
    """
    return [e for e in _iter_entries(payload) if isinstance(e, dict) and _looks_like_error(e)]


# ── Rate limiting ──────────────────────────────────────────────────────────
def is_rate_limited(last_run_at, now, cooldown_seconds: int) -> bool:
    """Pure decision: is ``now`` still inside the cooldown after ``last_run_at``?"""
    if last_run_at is None:
        return False
    return (now - last_run_at).total_seconds() < cooldown_seconds


def try_acquire_run_slot(db, *, now, cooldown_seconds: int) -> bool:
    """Atomically claim the reconcile slot. False if still within the cooldown.

    Race-safe: the conditional upsert either updates+returns (acquired) or does
    nothing (rate-limited), so concurrent webhooks can never both reconcile.
    """
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nuki_guardian_runs (id, last_run_at) VALUES (1, %s)
                ON CONFLICT (id) DO UPDATE SET last_run_at = EXCLUDED.last_run_at
                WHERE nuki_guardian_runs.last_run_at <= %s - (%s * interval '1 second')
                RETURNING id
                """,
                (now, now, cooldown_seconds),
            )
            acquired = cur.fetchone() is not None
        conn.commit()
        return acquired


# ── Audit persistence ──────────────────────────────────────────────────────
def record_audit(
    db, *, event_id: str | None, trigger_kind: str, verified: bool,
    rate_limited: bool = False, relevant_windows: int = 0, repaired: int = 0,
    failures: int = 0, detail: dict[str, Any] | None = None,
) -> None:
    from psycopg.types.json import Json
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nuki_guardian_audit
                    (event_id, trigger_kind, verified, rate_limited,
                     relevant_windows, repaired, failures, detail)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (event_id, trigger_kind, verified, rate_limited,
                 relevant_windows, repaired, failures, Json(detail or {})),
            )
        conn.commit()


def list_guardian_audit(db, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, event_id, trigger_kind, verified, rate_limited,
                   relevant_windows, repaired, failures, detail, created_at
            FROM nuki_guardian_audit ORDER BY created_at DESC LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
        return list(cur.fetchall())


# ── Reconciliation ─────────────────────────────────────────────────────────
def _slot_window_fields(slot, slot_hour: int, weekday: int, day, buffer_days: int):
    """Resolve the Nuki time-window fields + existing auth_id for a slot."""
    from datetime import timedelta
    if slot is not None:
        weekday_mask = int(slot["weekday_mask"])
        from_time = int(slot["from_time"])
        until_time = int(slot["until_time"])
        auth_id = slot.get("nuki_auth_id")
    else:
        weekday_mask = pin_pool.WEEKDAY_BIT[weekday]
        if slot_hour == pin_pool.FALLBACK_HOUR:
            from_time, until_time = pin_pool.FALLBACK_FROM_MIN, pin_pool.FALLBACK_UNTIL_MIN
        else:
            from_time, until_time = slot_hour * 60, min((slot_hour + 1) * 60, 1439)
        auth_id = None
    allowed_from = f"{day.isoformat()}T00:00:00Z"
    allowed_until = f"{(day + timedelta(days=buffer_days)).isoformat()}T23:59:59Z"
    return weekday_mask, from_time, until_time, auth_id, allowed_from, allowed_until


def reconcile_relevant_bookings(
    db, *, nuki, smartlock_id: int, now=None, tz_name: str = "Europe/Berlin",
    day=None, lead_seconds: int = RECONCILE_LEAD_SECONDS, buffer_days: int = BUFFER_DAYS,
) -> dict[str, Any]:
    """Re-materialise each relevant member's already-assigned code (first hour).

    Idempotent: for every currently-relevant booking it verifies the member's
    previously-assigned slot code and repairs it once only if it is missing / not
    valid for the FIRST booked hour. Never invents a code.
    """
    from ..timewindow.rotation import berlin_weekday_hour
    now = now or now_utc()
    day = day or to_berlin_tz(now, tz_name).date()  # Berlin-Lokaldatum, nicht UTC

    windows = store.relevant_windows(db, now=now, lead_seconds=lead_seconds)
    checked = repaired = failures = skipped = 0
    details: list[dict[str, Any]] = []

    for window in windows:
        weekday, hour = berlin_weekday_hour(window["starts_at"], tz_name)  # erste Stunde
        member_ref = str(window["member_id"])
        if pin_pool.needs_keypad_code(weekday, hour):
            slot_hour = hour
            slot_name_tpl = f"og-h{hour:02d}-p{{}}"
        else:
            slot_hour = pin_pool.FALLBACK_HOUR
            slot_name_tpl = "og-bh-p{}"

        recent = store.recent_pool_indices(
            db, member_ref=member_ref, weekday=weekday, hour=slot_hour, limit=pin_pool.POOL_PER_HOUR
        )
        if not recent:
            skipped += 1  # nothing was ever sent to this member for this slot
            continue
        pool_index = recent[-1]  # the code most recently assigned/sent to the member
        pin = store.get_todays_slot_pin(
            db, smartlock_id=smartlock_id, hour=slot_hour, pool_index=pool_index, rotation_date=day
        )
        if pin is None:
            skipped += 1
            continue

        slot_name = slot_name_tpl.format(pool_index)
        slot = store.get_slot(db, smartlock_id=smartlock_id, hour=slot_hour, pool_index=pool_index)
        weekday_mask, from_time, until_time, auth_id, allowed_from, allowed_until = _slot_window_fields(
            slot, slot_hour, weekday, day, buffer_days
        )
        verify = ensure_code_materialised(
            nuki, slot_name=slot_name, code=pin, weekday=weekday, hour=hour,
            weekday_mask=weekday_mask, from_time=from_time, until_time=until_time,
            allowed_from=allowed_from, allowed_until=allowed_until, auth_id=auth_id,
        )
        checked += 1
        if verify.get("repaired") and verify.get("valid"):
            repaired += 1
        if not verify.get("valid"):
            failures += 1
        details.append({
            "window_id": window.get("id"), "member_id": window.get("member_id"),
            "slot_name": slot_name, "valid": verify.get("valid"),
            "repaired": verify.get("repaired"),
        })

    return {
        "relevant_windows": len(windows), "checked": checked, "repaired": repaired,
        "failures": failures, "skipped": skipped, "details": details,
    }


# ── Top-level webhook handler ──────────────────────────────────────────────
def _derive_event_id(raw_body: bytes, headers: dict[str, str] | None) -> str:
    if headers:
        for key in ("X-Nuki-Delivery-Id", "X-Request-Id", "X-Nuki-Event-Id"):
            val = headers.get(key)
            if val:
                return f"nuki-{val}"
    return "nuki-" + hashlib.sha256(raw_body or b"").hexdigest()[:32]


def handle_nuki_webhook(
    db, settings, *, raw_body: bytes, signature: str | None = None,
    token: str | None = None, headers: dict[str, str] | None = None, now=None,
) -> dict[str, Any]:
    """Authenticate → dedupe → rate-limit → reconcile. Returns a result summary.

    ``accepted=False`` means the request must be rejected (HTTP 401). Everything
    else is a 200-ack with a description of what (if anything) was reconciled.
    """
    ensure_schema(db)
    nuki_cfg = get_effective_nuki_config(db, settings)
    secret = str(nuki_cfg.get("nuki_webhook_secret") or "")

    if not verify_webhook(secret=secret, raw_body=raw_body, signature=signature, token=token):
        logger.warning("Nuki webhook rejected: authentication failed")
        return {"accepted": False, "reason": "unauthorized"}

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (ValueError, UnicodeDecodeError):
        payload = {}

    event_id = _derive_event_id(raw_body, headers)
    fresh = db.record_webhook_event(
        provider="nuki", event_id=event_id, event_type="guardian",
        payload=payload if isinstance(payload, dict) else {"payload": payload},
    )
    if not fresh:
        logger.info("Nuki webhook %s already processed — idempotent skip", event_id)
        return {"accepted": True, "duplicate": True, "reconciled": False}

    # Klassifizierte Fehler-Events sind nur ein optionaler Marker fürs Audit — NIE
    # Voraussetzung: jeder erfolgreich authentifizierte Webhook ist ein sicherer,
    # rate-limitierter Reconcile-Trigger. So hängt der Wächter nicht an unbekannten
    # numerischen Nuki-Fehlercodes (die im Log nicht als Text auftauchen).
    error_events = extract_error_events(payload)
    trigger_kind = "keypad-error" if error_events else "webhook"

    cooldown = int(getattr(settings, "nuki_guardian_cooldown_seconds", 60))
    now = now or now_utc()
    if not try_acquire_run_slot(db, now=now, cooldown_seconds=cooldown):
        record_audit(db, event_id=event_id, trigger_kind=trigger_kind, verified=True,
                     rate_limited=True, detail={"error_events": len(error_events)})
        logger.info("Nuki guardian rate-limited (cooldown %ss)", cooldown)
        return {"accepted": True, "reconciled": False, "rate_limited": True,
                "error_events": len(error_events)}

    result = _run_reconcile(db, settings, nuki_cfg, now=now)

    record_audit(
        db, event_id=event_id, trigger_kind=trigger_kind, verified=True,
        relevant_windows=result["relevant_windows"], repaired=result["repaired"],
        failures=result["failures"],
        detail={"error_events": len(error_events), "checked": result["checked"],
                "skipped": result["skipped"]},
    )
    if result["failures"]:
        create_operational_alert(
            db=db, settings=settings, severity=AlertSeverity.ERROR,
            kind="guardian-reconcile-failed",
            message=(
                f"Nuki-Wächter: {result['failures']} Buchung(en) konnten nach "
                f"Webhook-Trigger nicht materialisiert werden."
            ),
            payload={"repaired": result["repaired"], "failures": result["failures"]},
        )
    return {"accepted": True, "reconciled": True, "error_events": len(error_events), **result}


def _run_reconcile(db, settings, nuki_cfg: dict[str, Any], *, now) -> dict[str, Any]:
    """Baue einen (DRY-RUN-fähigen) NukiClient und reconcile relevante Buchungen.

    Gemeinsame Innenschleife von Webhook-Trigger und Worker-Fallback; nutzt das
    Berlin-Lokaldatum für den Tages-PIN-Lookup.
    """
    from ..nuki_client import NukiClient

    effective = settings.model_copy(update=nuki_cfg)
    smartlock_id = int(nuki_cfg.get("nuki_smartlock_id") or 0)
    tz_name = getattr(settings, "timezone", "Europe/Berlin")
    nuki = NukiClient(effective)
    try:
        return reconcile_relevant_bookings(
            db, nuki=nuki, smartlock_id=smartlock_id, now=now, tz_name=tz_name,
            day=to_berlin_tz(now, tz_name).date(),
        )
    finally:
        nuki.close()


def run_guardian_cycle(db, settings, *, now=None) -> dict[str, Any]:
    """Periodischer Worker-Fallback: reconciled relevante Buchungen ohne Webhook.

    Läuft rate-limit-/idempotenz-sicher über denselben atomaren ``nuki_guardian_runs``
    -Slot wie der Webhook-Trigger — höchstens einmal pro
    ``nuki_guardian_fallback_interval_seconds``. Ein kurz zuvor per Webhook
    ausgelöster Reconcile teilt sich ``last_run_at`` und unterdrückt den Fallback,
    sodass nie doppelt reconciled wird. Der Reconcile selbst ist verify-then-repair
    (idempotent).
    """
    ensure_schema(db)
    now = now or now_utc()
    interval = int(getattr(settings, "nuki_guardian_fallback_interval_seconds", 900))
    if not try_acquire_run_slot(db, now=now, cooldown_seconds=interval):
        logger.info("Nuki guardian worker-fallback: within cooldown (%ss) — skipping", interval)
        return {"reconciled": False, "rate_limited": True}

    nuki_cfg = get_effective_nuki_config(db, settings)
    result = _run_reconcile(db, settings, nuki_cfg, now=now)
    record_audit(
        db, event_id=None, trigger_kind="worker-fallback", verified=True,
        relevant_windows=result["relevant_windows"], repaired=result["repaired"],
        failures=result["failures"],
        detail={"checked": result["checked"], "skipped": result["skipped"]},
    )
    if result["failures"]:
        create_operational_alert(
            db=db, settings=settings, severity=AlertSeverity.ERROR,
            kind="guardian-reconcile-failed",
            message=(
                f"Nuki-Wächter (Worker-Fallback): {result['failures']} Buchung(en) "
                f"konnten nicht materialisiert werden."
            ),
            payload={"repaired": result["repaired"], "failures": result["failures"]},
        )
    return {"reconciled": True, **result}
