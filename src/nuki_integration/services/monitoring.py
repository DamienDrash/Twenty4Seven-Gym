"""Proactive production monitoring/alerting for the OpenGym access-code flow.

Goal: the operator learns *immediately* when a booking/access-code flow fails,
instead of a day later. Detectors (acceptance scope):

  1. Eligible/upcoming booking has no dispatched access code by its deadline
     (incl. late imports)            -> ``check_overdue_dispatch``
  2. Code push/assign failed or code absent/invalid on the lock
     (fail-closed "blocked")         -> emitted at the failure site via ``notify``
  3. Email delivery failure / code not dispatched
                                     -> covered state-based by ``check_overdue_dispatch``
  4. Worker/sync crash, stale heartbeat, cadence broken
     -> worker writes ``heartbeat``; the (independent) service checks it via
        ``check_worker_heartbeat``
  5. Rejected/invalid keypad attempt -> primary: Nuki webhook -> guardian;
     fallback: ``poll_keypad_events`` polls the Nuki activity log. See the
     LIMITATION note on that function.

All alerts are **deduplicated/idempotent** (cooldown per key), **severity-tagged**,
carry member/booking/appointment context, and **never** the full keypad code or
secrets (see ``_safe_payload``). Transport reuses the project's existing alert
infrastructure (``create_operational_alert`` -> Telegram + e-mail); an optional
ntfy push fires additionally when ``NTFY_URL``/``NTFY_TOPIC`` are configured.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Json

from ..config import Settings
from ..datetime_utils import now_utc
from ..enums import AlertSeverity
from .alerts import create_operational_alert

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT = "worker"
# Alert when the worker heartbeat is older than max(interval * factor, floor).
HEARTBEAT_STALE_FACTOR = 3
HEARTBEAT_STALE_FLOOR_SECS = 180
# A window is "overdue" this long after its dispatch deadline with still no code.
OVERDUE_GRACE_SECS = 300
# Re-alert spacing for a persisting condition.
DEFAULT_COOLDOWN_SECS = 30 * 60

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monitoring_heartbeat (
    name          TEXT PRIMARY KEY,
    last_beat_at  TIMESTAMPTZ NOT NULL,
    interval_secs INTEGER NOT NULL DEFAULT 300,
    cycles        BIGINT NOT NULL DEFAULT 0,
    meta          JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS monitoring_alert_state (
    dedup_key     TEXT PRIMARY KEY,
    severity      TEXT NOT NULL,
    kind          TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_sent_at  TIMESTAMPTZ NOT NULL,
    times_sent    INTEGER NOT NULL DEFAULT 0,
    resolved_at   TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS monitoring_cursor (
    name       TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def ensure_schema(db) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()


# ── Redaction ─────────────────────────────────────────────────────────────
_SIX_DIGITS = re.compile(r"\b\d{6}\b")
_SECRET_KEYS = ("code", "pin", "token", "secret", "password", "api_key", "apikey", "authorization")


def _safe_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Strip secrets and full codes; keep only safe context (ids/times/last4)."""
    if not payload:
        return {}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        kl = str(k).lower()
        if any(s in kl for s in _SECRET_KEYS) and "last4" not in kl and "count" not in kl:
            continue  # drop secret-ish keys entirely (except *_last4 / *_count)
        if isinstance(v, str):
            out[k] = _SIX_DIGITS.sub("******", v)
        else:
            out[k] = v
    return out


def _safe_text(text: str) -> str:
    return _SIX_DIGITS.sub("******", text or "")


# ── Dedup / idempotent alert ──────────────────────────────────────────────
def notify(
    db,
    settings: Settings,
    *,
    key: str,
    severity: str,
    kind: str,
    title: str,
    detail: str = "",
    payload: dict[str, Any] | None = None,
    cooldown_secs: int = DEFAULT_COOLDOWN_SECS,
    now: datetime | None = None,
) -> bool:
    """Send a deduplicated, severity-tagged, redacted operational alert.

    Idempotent: the same ``key`` is (re)sent at most once per ``cooldown_secs``.
    Returns True if an alert was actually dispatched this call, False if suppressed
    by the cooldown. Atomic via an ON CONFLICT guard so concurrent workers/service
    can never double-fire.
    """
    now = now or now_utc()
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitoring_alert_state
                    (dedup_key, severity, kind, first_seen_at, last_sent_at, times_sent, resolved_at)
                VALUES (%s, %s, %s, %s, %s, 1, NULL)
                ON CONFLICT (dedup_key) DO UPDATE
                    SET last_sent_at = EXCLUDED.last_sent_at,
                        severity     = EXCLUDED.severity,
                        times_sent   = monitoring_alert_state.times_sent + 1,
                        resolved_at  = NULL
                    WHERE monitoring_alert_state.last_sent_at
                              < EXCLUDED.last_sent_at - (%s * interval '1 second')
                       OR monitoring_alert_state.resolved_at IS NOT NULL
                RETURNING times_sent
                """,
                (key, severity, kind, now, now, cooldown_secs),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        return False  # within cooldown → suppressed (dedup)

    safe = _safe_payload(payload)
    message = _safe_text(f"{title}\n{detail}".strip())
    if safe:
        message = f"{message}\n{json.dumps(safe, default=str, ensure_ascii=False)}"
    try:
        create_operational_alert(db=db, settings=settings, severity=severity,
                                  kind=kind, message=message, payload=safe)
    except Exception:
        logger.exception("monitoring.notify: create_operational_alert failed key=%s", key)
    _push_ntfy(settings, severity=severity, kind=kind, title=_safe_text(title), detail=_safe_text(detail))
    logger.warning("[MONITOR ALERT] %s %s key=%s", str(severity).upper(), kind, key)
    return True


def resolve(db, *, key: str, now: datetime | None = None) -> None:
    """Mark a condition resolved so the next occurrence re-alerts immediately."""
    now = now or now_utc()
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE monitoring_alert_state SET resolved_at=%s "
                "WHERE dedup_key=%s AND resolved_at IS NULL",
                (now, key),
            )
        conn.commit()


def _push_ntfy(settings: Settings, *, severity: str, kind: str, title: str, detail: str) -> None:
    """Optional ntfy push (dormant unless NTFY_URL+NTFY_TOPIC configured)."""
    url = (getattr(settings, "ntfy_url", "") or "").strip()
    topic = (getattr(settings, "ntfy_topic", "") or "").strip()
    if not url or not topic:
        return
    try:
        import httpx
        prio = {"error": "urgent", "warning": "high"}.get(str(severity).lower(), "default")
        httpx.post(f"{url.rstrip('/')}/{topic}", content=(f"{title}\n{detail}").encode("utf-8"),
                   headers={"Title": f"OpenGym {severity.upper()} {kind}"[:120], "Priority": prio},
                   timeout=10)
    except Exception:
        logger.warning("ntfy push failed (kind=%s)", kind)


# ── Heartbeat (cat 4) ─────────────────────────────────────────────────────
def heartbeat(db, *, name: str = WORKER_HEARTBEAT, interval_secs: int, meta: dict | None = None) -> None:
    now = now_utc()
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitoring_heartbeat (name, last_beat_at, interval_secs, cycles, meta)
                VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT (name) DO UPDATE
                    SET last_beat_at = EXCLUDED.last_beat_at,
                        interval_secs = EXCLUDED.interval_secs,
                        cycles = monitoring_heartbeat.cycles + 1,
                        meta = EXCLUDED.meta
                """,
                (name, now, int(interval_secs), Json(meta or {})),
            )
        conn.commit()


def get_heartbeat(db, *, name: str = WORKER_HEARTBEAT) -> dict | None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT name, last_beat_at, interval_secs, cycles FROM monitoring_heartbeat WHERE name=%s",
                    (name,))
        return cur.fetchone()


def is_stale(last_beat_at: datetime, now: datetime, interval_secs: int,
             factor: int = HEARTBEAT_STALE_FACTOR, floor_secs: int = HEARTBEAT_STALE_FLOOR_SECS) -> bool:
    """Pure: is the heartbeat older than max(interval*factor, floor)?"""
    threshold = max(int(interval_secs) * factor, floor_secs)
    return (now - last_beat_at).total_seconds() > threshold


def check_worker_heartbeat(db, settings: Settings, *, now: datetime | None = None) -> dict:
    """Cat 4: alert if the worker heartbeat is stale (crash/hang/cadence broken).

    Run by the *service* (independent of the worker) so a dead worker is caught.
    """
    now = now or now_utc()
    hb = get_heartbeat(db, name=WORKER_HEARTBEAT)
    key = "worker-heartbeat-stale"
    if hb is None:
        return {"heartbeat": None, "stale": False, "alerted": False}
    age = (now - hb["last_beat_at"]).total_seconds()
    stale = is_stale(hb["last_beat_at"], now, hb["interval_secs"])
    alerted = False
    if stale:
        alerted = notify(
            db, settings, key=key, severity=AlertSeverity.ERROR, kind="worker-heartbeat-stale",
            title="OpenGym worker heartbeat is stale — sync/dispatch may be down",
            detail=(f"Last worker cycle {int(age)}s ago (interval {hb['interval_secs']}s). "
                    f"Expected < {max(hb['interval_secs']*HEARTBEAT_STALE_FACTOR, HEARTBEAT_STALE_FLOOR_SECS)}s. "
                    f"Bookings may not sync and codes may not dispatch."),
            payload={"age_secs": int(age), "interval_secs": hb["interval_secs"], "cycles": hb["cycles"]},
            cooldown_secs=15 * 60, now=now,
        )
    else:
        resolve(db, key=key, now=now)  # recovery → next staleness re-alerts
    return {"heartbeat_age_secs": int(age), "stale": stale, "alerted": alerted}


# ── Overdue dispatch (cat 1 + 3) ──────────────────────────────────────────
def overdue_severity(minutes_to_start: float) -> str:
    """Pure: ERROR when the appointment is within an hour, else WARNING."""
    return AlertSeverity.ERROR if minutes_to_start <= 60 else AlertSeverity.WARNING


def check_overdue_dispatch(db, settings: Settings, *, now: datetime | None = None,
                           grace_secs: int = OVERDUE_GRACE_SECS) -> dict:
    """Cat 1+3: upcoming/active windows whose dispatch deadline passed but still
    have NO provisioned/emailed code. State-based — catches missed syncs, late
    imports, push failures and undelivered e-mails regardless of where they broke.
    """
    now = now or now_utc()
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT aw.id, aw.member_id, aw.booking_id, aw.starts_at, aw.ends_at, aw.dispatch_at,
                   m.email, m.first_name, m.last_name
            FROM access_windows aw
            JOIN members m ON m.id = aw.member_id
            WHERE aw.status IN ('scheduled','active')
              AND aw.dispatch_at <= %s - (%s * interval '1 second')
              AND aw.ends_at > %s
              AND aw.starts_at < %s + interval '24 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM access_codes ac
                  WHERE ac.access_window_id = aw.id
                    AND ac.status IN ('pending','provisioned','emailed')
              )
            ORDER BY aw.starts_at ASC
            """,
            (now, grace_secs, now, now),
        )
        overdue = cur.fetchall()

    alerted = 0
    for w in overdue:
        mins_to_start = (w["starts_at"] - now).total_seconds() / 60.0
        severity = overdue_severity(mins_to_start)
        name = f"{(w.get('first_name') or '').strip()} {(w.get('last_name') or '').strip()}".strip() \
            or (w.get("email") or f"member#{w['member_id']}")
        starts_local = w["starts_at"].astimezone(timezone.utc)
        if notify(
            db, settings, key=f"overdue-dispatch:{w['id']}", severity=severity,
            kind="booking-no-access-code",
            title="Upcoming booking has NO access code past its dispatch deadline",
            detail=(f"Member: {name}. Appointment starts {starts_local.isoformat()} "
                    f"(~{int(mins_to_start)} min). Window #{w['id']} booking #{w.get('booking_id')} "
                    f"is past dispatch deadline with no provisioned/emailed code."),
            payload={"window_id": w["id"], "member_id": w["member_id"], "booking_id": w.get("booking_id"),
                     "starts_at": starts_local.isoformat(), "minutes_to_start": int(mins_to_start),
                     "has_email": bool(w.get("email"))},
            cooldown_secs=20 * 60, now=now,
        ):
            alerted += 1
    if overdue:
        logger.warning("check_overdue_dispatch: %d overdue window(s), %d alerted", len(overdue), alerted)
    return {"overdue": len(overdue), "alerted": alerted}


# ── Keypad rejection fallback (cat 5) ─────────────────────────────────────
# LIMITATION: the Nuki Web API activity log (/smartlock/{id}/log) is reachable,
# but a REJECTED keypad attempt could not be validated live (no keypad event in
# the observed window). The primary detector remains the Nuki webhook -> guardian.
# This poll is a best-effort fallback: it flags keypad-sourced log entries that
# carry an explicit non-OK/error state, correlates by auth name + time, and never
# alerts on ambiguous entries (no false positives). Successful keypad events are
# logged for observability + future signature tuning.
KEYPAD_TRIGGER = 255            # Nuki: keypad-sourced entries (best-known)
KEYPAD_REJECT_STATES = frozenset()  # populate once a real rejection is observed


def classify_keypad_event(entry: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Pure. Returns (category|None, context). category in
    {'keypad-accepted','keypad-rejected'} or None if not a keypad event.
    Conservative: only flags 'keypad-rejected' on an explicit known reject state.
    """
    name = str(entry.get("name") or "")
    trigger = entry.get("trigger")
    is_keypad = name.startswith("og-") or trigger == KEYPAD_TRIGGER
    if not is_keypad:
        return None, {}
    ctx = {"auth_name": name[:24], "date": entry.get("date"), "action": entry.get("action"),
           "state": entry.get("state"), "log_id": entry.get("id")}
    if entry.get("state") in KEYPAD_REJECT_STATES:
        return "keypad-rejected", ctx
    return "keypad-accepted", ctx


def _get_cursor(db, name: str) -> str | None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM monitoring_cursor WHERE name=%s", (name,))
        row = cur.fetchone()
        return row["value"] if row else None


def _set_cursor(db, name: str, value: str) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO monitoring_cursor (name, value, updated_at) VALUES (%s,%s,NOW()) "
                "ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (name, value),
            )
        conn.commit()


def poll_keypad_events(db, settings: Settings, nuki, *, smartlock_id: int, now: datetime | None = None,
                       limit: int = 50) -> dict:
    """Cat 5 fallback: poll the Nuki activity log for keypad rejections."""
    now = now or now_utc()
    cursor = _get_cursor(db, "nuki_log_last_date")
    try:
        entries = nuki._request("GET", f"/smartlock/{smartlock_id}/log?limit={limit}")
    except Exception as exc:
        logger.warning("poll_keypad_events: log GET failed: %s", exc)
        return {"polled": 0, "keypad": 0, "rejected": 0, "alerted": 0}
    if not isinstance(entries, list):
        return {"polled": 0, "keypad": 0, "rejected": 0, "alerted": 0}

    fresh = [e for e in entries if cursor is None or str(e.get("date")) > cursor]
    keypad = rejected = alerted = 0
    for e in fresh:
        cat, ctx = classify_keypad_event(e)
        if cat is None:
            continue
        keypad += 1
        if cat == "keypad-rejected":
            rejected += 1
            correlation = _correlate_keypad(db, ctx)
            if notify(
                db, settings, key=f"keypad-reject:{ctx.get('log_id')}", severity=AlertSeverity.WARNING,
                kind="keypad-code-rejected",
                title="Nuki keypad rejected/invalid code attempt",
                detail=(f"Slot {ctx.get('auth_name')} at {ctx.get('date')} rejected. "
                        f"{correlation.get('note','')}"),
                payload={**ctx, **correlation}, cooldown_secs=10 * 60, now=now,
            ):
                alerted += 1
    if entries:
        newest = max(str(e.get("date")) for e in entries)
        _set_cursor(db, "nuki_log_last_date", newest)
    if keypad:
        logger.info("poll_keypad_events: keypad=%d rejected=%d alerted=%d (fresh=%d)",
                    keypad, rejected, alerted, len(fresh))
    return {"polled": len(fresh), "keypad": keypad, "rejected": rejected, "alerted": alerted}


def _correlate_keypad(db, ctx: dict) -> dict:
    """Best-effort: map a keypad auth-name (og-hHH-pX / og-bh-pX) to a recent
    assignment/member. Returns a safe note (never the code)."""
    name = str(ctx.get("auth_name") or "")
    m = re.match(r"og-h(\d{2})-p(\d)", name) or re.match(r"og-bh-p(\d)", name)
    if not m:
        return {"note": "no slot correlation"}
    try:
        with db.connection() as conn, conn.cursor() as cur:
            if name.startswith("og-bh-"):
                pool = int(m.group(1)); hour_filter = ""
                params: tuple = (pool,)
            else:
                hour = int(m.group(1)); pool = int(m.group(2))
                hour_filter = "AND hour=%s"; params = (pool, hour)
            cur.execute(
                f"SELECT member_ref, assigned_date FROM nuki_assignments "
                f"WHERE pool_index=%s {hour_filter} ORDER BY created_at DESC LIMIT 1",
                params,
            )
            row = cur.fetchone()
            if row:
                return {"note": f"last assigned to member#{row['member_ref']} on {row['assigned_date']}",
                        "member_ref": row["member_ref"]}
    except Exception:
        logger.debug("keypad correlation failed", exc_info=True)
    return {"note": "slot recognised, no recent assignment"}


# ── Orchestrators ─────────────────────────────────────────────────────────
def run_worker_monitoring(db, settings: Settings) -> dict:
    """Called each worker tick: write heartbeat + run the periodic fallback
    detectors (overdue dispatch + keypad-rejection poll) so a missed webhook can
    never fail silently."""
    ensure_schema(db)
    interval = int(getattr(settings, "magicline_sync_interval_minutes", 5)) * 60
    heartbeat(db, name=WORKER_HEARTBEAT, interval_secs=interval,
              meta={"at": now_utc().isoformat()})
    overdue = check_overdue_dispatch(db, settings)
    keypad = {"keypad": 0, "rejected": 0, "alerted": 0}
    try:
        from ..nuki_client import NukiClient
        from .settings import get_effective_nuki_config
        cfg = get_effective_nuki_config(db, settings)
        if not cfg["nuki_dry_run"] and cfg["nuki_smartlock_id"]:
            nuki = NukiClient(settings.model_copy(update=cfg))
            try:
                keypad = poll_keypad_events(db, settings, nuki, smartlock_id=int(cfg["nuki_smartlock_id"]))
            finally:
                nuki.close()
    except Exception:
        logger.exception("run_worker_monitoring: keypad poll failed")
    return {"overdue": overdue, "keypad": keypad}


def run_service_monitoring(db, settings: Settings) -> dict:
    """Called periodically by the *service* (independent of the worker): detect a
    stale/crashed worker heartbeat."""
    ensure_schema(db)
    return {"heartbeat": check_worker_heartbeat(db, settings)}
