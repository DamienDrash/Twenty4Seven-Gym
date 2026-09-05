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
  6. Broken link in the chain server → studio internet → NAS/HA → hub → lock
                                     -> ``check_studio_link`` / ``check_nuki_link``
  7. DB pins and the codes physically on the keypad drifting apart
                                     -> ``check_code_sync``
  8. An already delivered code that no longer matches its slot
                                     -> ``check_delivered_codes``

Alert kinds (all pushed to ntfy when configured):

  ``studio-internet-down``    Internet/Router im Studio weg (oder NAS stromlos)
  ``nas-offline``             NAS nicht erreichbar, Leitung steht
  ``home-assistant-offline``  NAS da, HA antwortet nicht
  ``nuki-hub-offline``        Hub antwortet nicht auf einen Round-Trip
  ``nuki-lock-unreachable``   Hub lebt, Schloss nicht am BLE
  ``nuki-battery-low``        Schloss-Akku ≤20 % oder kritisch
  ``codes-out-of-sync``       DB-Pin ≠ Keypad-Code
  ``wrong-code-delivered``    verschickter Code passt nicht mehr zum Slot
  ``booking-no-access-code``  fällige Buchung ohne Code (Zustellung ausgeblieben)
  ``code-not-materialised``   Zustellung fail-closed blockiert
  ``keypad-code-rejected``    Fehlversuch an der Tür (falscher Code/Zeitfenster)
  ``worker-heartbeat-stale``  Worker steht (vom Service unabhängig erkannt)
  ``guardian-reconcile-failed``/``delivery-unconfirmed``/``nuki-rotation-paused-long``

Bekannte Grenze: Stirbt der ganze Server, kann von hier niemand mehr alarmieren —
dafür bräuchte es einen externen Dead-Man's-Switch (z. B. healthchecks.io), der
das Ausbleiben des Heartbeats von außen bemerkt.

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
        sev = str(severity).lower()
        prio = {"error": "urgent", "warning": "high"}.get(sev, "default")
        # Emoji tag = what the alert is about, readable on a lock screen without
        # opening the notification.
        tag = {"error": "rotating_light", "warning": "warning"}.get(sev, "information_source")
        by_kind = {
            "nuki-hub-offline": "electric_plug", "nuki-lock-unreachable": "lock",
            "nuki-battery-low": "battery", "studio-internet-down": "satellite",
            "nas-offline": "floppy_disk", "home-assistant-offline": "house",
            "codes-out-of-sync": "twisted_rightwards_arrows",
            "wrong-code-delivered": "no_entry", "keypad-code-rejected": "no_entry_sign",
            "booking-no-access-code": "email", "worker-heartbeat-stale": "heartbeat",
        }
        tags = ",".join(t for t in (tag, by_kind.get(kind)) if t)
        httpx.post(f"{url.rstrip('/')}/{topic}", content=(f"{title}\n{detail}").encode("utf-8"),
                   headers={"Title": f"OpenGym {severity.upper()} {kind}"[:120],
                            "Priority": prio, "Tags": tags},
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
KEYPAD_TRIGGER = 255            # Nuki Web API: keypad-sourced entries (best-known)
KEYPAD_REJECT_STATES = frozenset()  # Web API: reject values never observed live
# Nuki Hub log (MQTT): what a *successful* keypad action reports. Anything else in
# ``completionStatus`` is a failed attempt — wrong code, code outside its time
# window, motor blocked, timeout. Verified against the live log on 2026-09-05.
KEYPAD_TRIGGERS = frozenset({"code", "fingerprint", "keypad"})
KEYPAD_OK_STATES = frozenset({"success"})


def classify_keypad_event(entry: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Pure. Returns (category|None, context). category in
    {'keypad-accepted','keypad-rejected'} or None if not a keypad event.

    Speaks both log dialects. The Nuki **Hub** log is the one that actually
    carries rejections: ``type='KeypadAction'``, ``trigger='code'|'fingerprint'``
    and a ``completionStatus`` that is anything but ``success`` when the attempt
    failed — a wrong code, or a right code outside its time window ("falscher
    Slot"), both land here. The Web API log (legacy) only exposed ``state``,
    whose reject values were never observed, which is why the old detector could
    not fire at all.
    """
    name = str(entry.get("name") or entry.get("authorizationName") or "")
    trigger = entry.get("trigger")
    entry_type = str(entry.get("type") or "")
    is_keypad = (
        entry_type == "KeypadAction"
        or trigger in KEYPAD_TRIGGERS
        or trigger == KEYPAD_TRIGGER
        or name.startswith("og-")
    )
    if not is_keypad:
        return None, {}
    status = entry.get("completionStatus")
    ctx = {"auth_name": name[:24] or "unbekannt", "date": entry.get("date"),
           "action": entry.get("action"), "state": entry.get("state"),
           "completion_status": status, "trigger": trigger,
           "code_id": entry.get("codeId"),
           "log_id": entry.get("id", entry.get("index"))}
    if status is not None:
        return ("keypad-accepted" if str(status) in KEYPAD_OK_STATES
                else "keypad-rejected"), ctx
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
        # Transport-agnostic: the hub client serves the log from MQTT and has no
        # ``_request``. Prefer the public accessor, fall back to the Web API call.
        if hasattr(nuki, "get_log"):
            entries = nuki.get_log(limit=limit)
        else:
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


# ── Freeze-Wächter (cat 6) ────────────────────────────────────────────────
def check_freeze_watch(db, settings: Settings, nuki_client=None, *, now: datetime | None = None) -> dict:
    """Alert after 24h of active NUKI_ROTATION_PAUSED and alert when lock becomes reachable again."""
    now = now or now_utc()
    paused = getattr(settings, "nuki_rotation_paused", False)
    if not paused:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM monitoring_cursor WHERE name='freeze_start_at'")
        conn.commit()
        resolve(db, key="nuki-rotation-paused-24h", now=now)
        resolve(db, key="nuki-lock-reachable-unfreeze-ready", now=now)
        return {"paused": False, "alerted_24h": False, "alerted_reachable": False}

    # Track when freeze started
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT value, updated_at FROM monitoring_cursor WHERE name='freeze_start_at'")
        row = cur.fetchone()
        if not row:
            cur.execute(
                "INSERT INTO monitoring_cursor (name, value, updated_at) VALUES ('freeze_start_at', %s, %s) "
                "ON CONFLICT (name) DO NOTHING",
                (now.isoformat(), now),
            )
            conn.commit()
            freeze_start = now
        else:
            try:
                freeze_start = datetime.fromisoformat(row["value"])
                if freeze_start.tzinfo is None:
                    freeze_start = freeze_start.replace(tzinfo=timezone.utc)
            except Exception:
                freeze_start = row["updated_at"]

    age_hours = (now - freeze_start).total_seconds() / 3600.0
    alerted_24h = False
    if age_hours >= 24.0:
        alerted_24h = notify(
            db, settings, key="nuki-rotation-paused-24h", severity=AlertSeverity.WARNING,
            kind="nuki-rotation-paused-long",
            title="NUKI_ROTATION_PAUSED is active for over 24 hours",
            detail=f"Lock rotation has been frozen for {int(age_hours)} hours. Verify studio internet and lock status.",
            payload={"freeze_start": freeze_start.isoformat(), "age_hours": int(age_hours)},
            cooldown_secs=12 * 3600, now=now,
        )

    alerted_reachable = False
    if nuki_client:
        try:
            status = nuki_client.get_lock_status()
            if status:
                alerted_reachable = notify(
                    db, settings, key="nuki-lock-reachable-unfreeze-ready", severity=AlertSeverity.INFO,
                    kind="nuki-unfreeze-ready",
                    title="Nuki lock is reachable — NUKI_ROTATION_PAUSED can be reset",
                    detail="Lock API responded successfully. If studio internet is restored, Damien can approve un-freezing NUKI_ROTATION_PAUSED.",
                    payload={"lock_status": status},
                    cooldown_secs=6 * 3600, now=now,
                )
        except Exception:
            logger.debug("Freeze reachability check failed (lock offline)")

    return {"paused": True, "age_hours": int(age_hours), "alerted_24h": alerted_24h, "alerted_reachable": alerted_reachable}


# ── Konnektivität: Nuki-Hub/Schloss, NAS/Home Assistant, Studio-Internet ──
# Warum eigene Checks statt "der Worker meldet sich schon": Der Ausfall vom
# 02.–05.09.2026 blieb fünf Tage still, weil jede Ebene für sich "gesund" aussah.
# Diese Wächter prüfen die Kette Server → Studio-Internet → NAS/HA → Hub → Schloss
# Glied für Glied und melden das ERSTE gerissene, nicht die Folgefehler.


def _tcp_open(host: str, port: int, timeout: float = 5.0) -> bool:
    """Reine TCP-Erreichbarkeit (kein Protokoll) — bewusst ohne Abhängigkeiten."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, *, headers: dict[str, str] | None = None, timeout: float = 8.0) -> bool:
    try:
        import httpx
        resp = httpx.get(url, headers=headers or {}, timeout=timeout, verify=False)
        return resp.status_code < 500
    except Exception:
        return False


def classify_studio_link(*, nas_tailscale: bool, public_endpoint: bool, home_assistant: bool) -> tuple[str | None, str]:
    """Pure: welches Kettenglied ist gerissen? → (kind|None, Begründung).

    Tailscale läuft über dieselbe WAN-Leitung wie alles andere: ist die NAS über
    Tailscale erreichbar, steht das Studio-Internet zwangsläufig. Antwortet
    zusätzlich der öffentliche Endpunkt (Router-Portfreigabe) nicht, während
    Tailscale tot ist, dann ist die Leitung/der Router weg — nicht nur die NAS.
    """
    if nas_tailscale and home_assistant:
        return None, "Studio-Kette vollständig erreichbar."
    if nas_tailscale and not home_assistant:
        return "home-assistant-offline", ("NAS erreichbar (Tailscale), aber Home Assistant "
                                          "antwortet nicht — HA-Container prüfen.")
    if not nas_tailscale and public_endpoint:
        return "nas-offline", ("Studio-Internet steht (öffentlicher Endpunkt antwortet), aber die "
                               "NAS ist über Tailscale nicht erreichbar — NAS aus oder "
                               "Tailscale-Daemon tot.")
    return "studio-internet-down", ("Weder Tailscale-NAS noch öffentlicher Endpunkt erreichbar — "
                                    "Internet/Router im Studio ausgefallen oder NAS stromlos.")


def check_studio_link(db, settings: Settings, *, now: datetime | None = None) -> dict:
    """Studio-Internet, NAS und Home Assistant."""
    now = now or now_utc()
    host = (getattr(settings, "nuki_mqtt_host", "") or "").strip()
    ha_url = (getattr(settings, "ha_url", "") or "").strip()
    ha_token = (getattr(settings, "ha_token", "") or "").strip()
    if not host:
        return {"checked": False}
    nas_tailscale = _tcp_open(host, int(getattr(settings, "nuki_mqtt_port", 1883) or 1883))
    public_endpoint = _http_ok(ha_url) if ha_url else False
    home_assistant = False
    if ha_url and ha_token:
        home_assistant = _http_ok(f"{ha_url.rstrip('/')}/api/",
                                  headers={"Authorization": f"Bearer {ha_token}"})
    elif nas_tailscale:
        home_assistant = True  # ohne Token nicht prüfbar → nicht fälschlich alarmieren
    kind, reason = classify_studio_link(nas_tailscale=nas_tailscale, public_endpoint=public_endpoint,
                                        home_assistant=home_assistant)
    state = {"nas_tailscale": nas_tailscale, "public_endpoint": public_endpoint,
             "home_assistant": home_assistant, "kind": kind}
    for k in ("studio-internet-down", "nas-offline", "home-assistant-offline"):
        if k != kind:
            resolve(db, key=k, now=now)
    if kind is None:
        return {**state, "alerted": False}
    severity = AlertSeverity.ERROR if kind == "studio-internet-down" else AlertSeverity.WARNING
    alerted = notify(
        db, settings, key=kind, severity=severity, kind=kind,
        title={"studio-internet-down": "Studio offline — Internet/Router oder NAS ausgefallen",
               "nas-offline": "NAS nicht erreichbar",
               "home-assistant-offline": "Home Assistant antwortet nicht"}[kind],
        detail=reason, payload=state, cooldown_secs=30 * 60, now=now,
    )
    return {**state, "alerted": alerted}


def check_nuki_link(db, settings: Settings, nuki=None, *, now: datetime | None = None) -> dict:
    """Hub erreichbar? Schloss am Hub? Akku? Meldet `nuki-hub-offline` /
    `nuki-lock-unreachable`.

    Wichtig: ``hub_health`` erzwingt einen Round-Trip. Alle Hub-Topics sind
    retained — ein toter Hub „antwortet" sonst stundenlang mit Altdaten
    (genau so blieb sein Ausfall am 05.09. unbemerkt).
    """
    now = now or now_utc()
    if nuki is None or not hasattr(nuki, "hub_health"):
        return {"checked": False}
    health = nuki.hub_health()
    alerted = 0
    if not health.get("responsive"):
        if notify(
            db, settings, key="nuki-hub-offline", severity=AlertSeverity.ERROR,
            kind="nuki-hub-offline",
            title="Nuki Hub offline — keine Antwort über MQTT",
            detail=("Der Hub (ESP32) reagiert nicht auf eine Statusabfrage. Türcodes am Keypad "
                    "funktionieren weiter, aber Rotation und Code-Verifikation sind blind. "
                    f"Letzter bekannter Zustand: MQTT={health.get('mqtt_connected')}, "
                    f"Schloss={health.get('lock_available')}, Uptime={health.get('uptime')}s."),
            payload=health, cooldown_secs=30 * 60, now=now,
        ):
            alerted += 1
        return {**health, "alerted": alerted}
    resolve(db, key="nuki-hub-offline", now=now)

    if health.get("lock_available") is False or health.get("hybrid_connected") is False:
        if notify(
            db, settings, key="nuki-lock-unreachable", severity=AlertSeverity.ERROR,
            kind="nuki-lock-unreachable",
            title="Nuki Schloss für den Hub nicht erreichbar",
            detail=(f"Hub lebt, aber das Schloss antwortet nicht (availability="
                    f"{health.get('lock_available')}, hybrid={health.get('hybrid_connected')}, "
                    f"BLE-RSSI={health.get('ble_rssi')}). BLE-Reichweite/Akku prüfen."),
            payload=health, cooldown_secs=30 * 60, now=now,
        ):
            alerted += 1
    else:
        resolve(db, key="nuki-lock-unreachable", now=now)

    level = health.get("battery_level")
    if health.get("battery_critical") or (isinstance(level, int) and level <= 20):
        if notify(
            db, settings, key="nuki-battery-low", severity=AlertSeverity.WARNING,
            kind="nuki-battery-low", title="Nuki Schloss-Akku schwach",
            detail=f"Akkustand {level}% (kritisch={health.get('battery_critical')}).",
            payload=health, cooldown_secs=12 * 3600, now=now,
        ):
            alerted += 1
    return {**health, "alerted": alerted}


# ── Konsistenz: DB-Pins ↔ tatsächliche Keypad-Codes ───────────────────────
def diff_db_vs_device(db_pins: dict[str, str], device_codes: dict[str, str]) -> dict[str, Any]:
    """Pure: vergleicht Slot→PIN (DB) mit Slot→Code (Gerät).

    ``mismatched`` = Slot existiert auf beiden Seiten mit UNTERSCHIEDLICHEM Code
    (der gefährliche Fall: die DB verschickt einen Code, den die Tür nicht kennt).
    ``invisible`` = Slot, den das Gerät gerade nicht meldet — kein Fehler, nur
    ungeprüft (die Hub-Firmware deckelt ihre Code-Liste).
    """
    mismatched, matched, invisible = [], [], []
    for name, pin in sorted(db_pins.items()):
        code = device_codes.get(name)
        if code is None:
            invisible.append(name)
        elif str(code) == str(pin):
            matched.append(name)
        else:
            mismatched.append(name)
    return {"mismatched": mismatched, "matched": matched, "invisible": invisible,
            "total": len(db_pins)}


def check_code_sync(db, settings: Settings, nuki=None, *, smartlock_id: int = 0,
                    now: datetime | None = None) -> dict:
    """Meldet `codes-out-of-sync`, wenn DB-Pins und Keypad-Codes auseinanderlaufen."""
    now = now or now_utc()
    if nuki is None:
        return {"checked": False}
    try:
        device = {}
        # Kein BLE-Refresh: der Drift-Check darf mit dem letzten Gerätestand arbeiten.
        # Ein erzwungener Keypad-Read alle 5 Minuten belastet den Schloss-Akku ohne
        # Mehrwert — die Zustellung selbst liest ohnehin frisch.
        read = getattr(nuki, "list_keypad_codes", None)
        try:
            codes = read(refresh=False, cache_seconds=120.0)
        except TypeError:      # Web-API-Client kennt die Parameter nicht
            codes = read()
        for auth in codes:
            name = str(auth.get("name") or "")
            if name.startswith("og-") and auth.get("code") is not None:
                device.setdefault(name, str(auth["code"]))
        if not device:
            return {"checked": False, "reason": "keine Gerätecodes lesbar"}
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (s.name) s.name, h.pin
                FROM nuki_pin_history h JOIN nuki_slots s ON s.id = h.slot_id
                WHERE h.rotation_date <= CURRENT_DATE
                ORDER BY s.name, h.rotation_date DESC
                """
            )
            db_pins = {r["name"]: str(r["pin"]) for r in cur.fetchall()}
    except Exception as exc:
        logger.warning("check_code_sync: Vergleich fehlgeschlagen: %s", exc)
        return {"checked": False, "error": str(exc)}

    result = diff_db_vs_device(db_pins, device)
    if result["mismatched"]:
        result["alerted"] = notify(
            db, settings, key="codes-out-of-sync", severity=AlertSeverity.ERROR,
            kind="codes-out-of-sync",
            title="Codes zwischen Datenbank und Nuki asynchron",
            detail=(f"{len(result['mismatched'])} Slot(s) haben in der DB einen anderen Code als am "
                    f"Keypad — verschickte Codes öffnen dort nicht. Betroffen: "
                    f"{', '.join(result['mismatched'][:12])}"
                    f"{' …' if len(result['mismatched']) > 12 else ''}. "
                    f"Übereinstimmend: {len(result['matched'])}, ungeprüft: {len(result['invisible'])}."),
            payload={"mismatched": result["mismatched"][:30], "matched": len(result["matched"]),
                     "invisible": len(result["invisible"])},
            cooldown_secs=6 * 3600, now=now,
        )
    else:
        resolve(db, key="codes-out-of-sync", now=now)
        result["alerted"] = False
    return result


def check_delivered_codes(db, settings: Settings, nuki=None, *, smartlock_id: int = 0,
                          now: datetime | None = None) -> dict:
    """Meldet `wrong-code-delivered`: ein bereits verschickter Code passt nicht
    mehr zu dem, was für diesen Slot am Schloss hängt.

    Der Fall aus der Praxis: das Mitglied hat die Mail, aber zwischen Versand und
    Termin wurde rotiert/desynchronisiert — der Code öffnet nicht mehr. Bisher fiel
    das erst an der Tür auf. Die Klartext-Codes stehen nirgends in der DB (nur
    Hashes), also wird der heutige Slot-Pin gegen den gespeicherten Hash geprüft.
    """
    now = now or now_utc()
    if nuki is None:
        return {"checked": False}
    from ..auth import verify_password  # lokal: vermeidet Import-Zyklus
    from ..timewindow import store

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT aw.id, aw.member_id, aw.starts_at, ac.code_hash, ac.code_last4,
                   na.hour AS slot_hour, na.pool_index
            FROM access_windows aw
            JOIN access_codes ac ON ac.access_window_id = aw.id AND ac.status = 'emailed'
            JOIN bookings b ON b.id = aw.booking_id
            -- Der Slot muss zu GENAU dieser Buchung passen: gleicher Wochentag und
            -- gebuchte Uhrstunde (bzw. 24 = Business-Hours-Fallback). Die zuletzt
            -- irgendwann vergebene Zuweisung des Mitglieds wäre der falsche Vergleich
            -- und würde Fehlalarme erzeugen.
            LEFT JOIN LATERAL (
                SELECT hour, pool_index FROM nuki_assignments
                WHERE member_ref = aw.member_id::text
                  AND weekday = EXTRACT(ISODOW FROM (b.start_at AT TIME ZONE 'Europe/Berlin'))::int - 1
                  AND hour IN (EXTRACT(HOUR FROM (b.start_at AT TIME ZONE 'Europe/Berlin'))::int, 24)
                ORDER BY created_at DESC LIMIT 1
            ) na ON TRUE
            WHERE aw.status IN ('scheduled','active')
              AND aw.ends_at > %s
              AND aw.starts_at < %s + interval '24 hours'
            """,
            (now, now),
        )
        rows = cur.fetchall()

    stale, checked = [], 0
    for row in rows:
        if row.get("slot_hour") is None:
            continue
        pin = store.get_todays_slot_pin(
            db, smartlock_id=smartlock_id, hour=int(row["slot_hour"]),
            pool_index=int(row["pool_index"]), rotation_date=now.date(),
        )
        if not pin:
            continue
        checked += 1
        try:
            if not verify_password(pin, row["code_hash"]):
                stale.append({"window_id": row["id"], "member_id": row["member_id"],
                              "starts_at": row["starts_at"].isoformat(),
                              "code_last4": row.get("code_last4")})
        except Exception:
            continue

    alerted = 0
    for item in stale:
        if notify(
            db, settings, key=f"wrong-code:{item['window_id']}", severity=AlertSeverity.ERROR,
            kind="wrong-code-delivered",
            title="Verschickter Zugangscode passt nicht mehr zum Schloss",
            detail=(f"Mitglied #{item['member_id']} hat für den Termin {item['starts_at']} einen "
                    f"Code erhalten (endet auf {item['code_last4']}), der nicht mehr dem aktuellen "
                    f"Slot-Pin entspricht. Das Mitglied kommt damit nicht rein — neu zustellen."),
            payload=item, cooldown_secs=60 * 60, now=now,
        ):
            alerted += 1
    return {"checked": checked, "stale": len(stale), "alerted": alerted}


# ── Orchestrators ─────────────────────────────────────────────────────────
def run_worker_monitoring(db, settings: Settings) -> dict:
    """Called each worker tick: write heartbeat + run the periodic fallback
    detectors (overdue dispatch + keypad-rejection poll + freeze watch) so a missed webhook can
    never fail silently."""
    ensure_schema(db)
    interval = int(getattr(settings, "magicline_sync_interval_minutes", 5)) * 60
    heartbeat(db, name=WORKER_HEARTBEAT, interval_secs=interval,
              meta={"at": now_utc().isoformat()})
    overdue = check_overdue_dispatch(db, settings)
    keypad = {"keypad": 0, "rejected": 0, "alerted": 0}
    freeze = {"paused": False, "alerted_24h": False, "alerted_reachable": False}
    link = sync = delivered = studio = {"checked": False}
    try:
        studio = check_studio_link(db, settings)
    except Exception:
        logger.exception("run_worker_monitoring: studio link check failed")
    try:
        from ..nuki_client import build_nuki_client
        from .settings import get_effective_nuki_config
        cfg = get_effective_nuki_config(db, settings)
        nuki_inst = None
        if not cfg["nuki_dry_run"] and cfg["nuki_smartlock_id"]:
            nuki_inst = build_nuki_client(settings.model_copy(update=cfg))
        try:
            if nuki_inst:
                smartlock_id = int(cfg["nuki_smartlock_id"])
                # Order matters: establish whether the hub is even alive before
                # reading anything from it — otherwise a dead link shows up as a
                # dozen downstream "code missing" alerts instead of one cause.
                link = check_nuki_link(db, settings, nuki_inst)
                if link.get("responsive", True):
                    keypad = poll_keypad_events(db, settings, nuki_inst, smartlock_id=smartlock_id)
                    sync = check_code_sync(db, settings, nuki_inst, smartlock_id=smartlock_id)
                    delivered = check_delivered_codes(db, settings, nuki_inst, smartlock_id=smartlock_id)
            freeze = check_freeze_watch(db, settings, nuki_client=nuki_inst)
        finally:
            if nuki_inst:
                nuki_inst.close()
    except Exception:
        logger.exception("run_worker_monitoring: nuki monitoring checks failed")
    return {"overdue": overdue, "keypad": keypad, "freeze": freeze,
            "studio": studio, "nuki_link": link, "code_sync": sync, "delivered": delivered}


def run_service_monitoring(db, settings: Settings) -> dict:
    """Called periodically by the *service* (independent of the worker): detect a
    stale/crashed worker heartbeat."""
    ensure_schema(db)
    return {"heartbeat": check_worker_heartbeat(db, settings)}
