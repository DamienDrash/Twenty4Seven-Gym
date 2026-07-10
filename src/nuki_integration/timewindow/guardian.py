"""Wächter — garantiert, dass der einem Mitglied zugestellte Keypad-Code für die
erste Stunde der Buchung tatsächlich materialisiert am Schloss hinterlegt ist.

Zwei Ebenen, beide um dieselbe Verifikation herum:

  * ``verify_for_send`` — SYNCHRON im Zustellpfad (``assign_and_deliver``). Bevor
    ein PIN gemailt wird, wird er gegen das echte Schloss geprüft. Trägt er nicht
    (nicht vorhanden / nicht materialisiert / falsches Zeitfenster), wird ein
    bereits materialisierter Geschwister-Code desselben Pools gewählt und DIESER
    gesendet; notfalls der PIN nachmaterialisiert. Es wird nie ein unverifizierter
    Code kommuniziert, ohne dass Alarm ausgelöst wird.

  * ``guardian_tick`` — PERIODISCH (Worker, ~60s). Für jede unmittelbar
    bevorstehende oder laufende Buchung (erste Stunde) wird der zugestellte Code
    erneut am Schloss verifiziert und bei Fehlen sofort nachmaterialisiert +
    alarmiert. Zusätzlich Health-Alarm, wenn das Nuki-Aktivitätslog veraltet ist
    (die Keypad-Event-Sicht der Web-API ist dann blind).

Warum kein "reagiere auf falsche Keypad-Eingabe": Die Nuki-Web-API exponiert
falsche PIN-Eingaben nicht, und ihr Aktivitätslog läuft stark verzögert. Der
Wächter verhindert den Lockout PROAKTIV, statt hinterher zu reagieren — nur die
ERSTE STUNDE der Buchung wird betrachtet (Betreiber-Vorgabe).
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, timedelta

from ..datetime_utils import now_utc, to_berlin_tz
from ..enums import AccessWindowStatus, AlertSeverity
from . import pin_pool, store

logger = logging.getLogger(__name__)


# ── Slot-Fenster (identisch zur Rotation) ─────────────────────────
_OFFPEAK_MASK: dict[int, int] | None = None


def _offpeak_mask(hour: int) -> int:
    global _OFFPEAK_MASK
    if _OFFPEAK_MASK is None:
        _OFFPEAK_MASK = {b.hour: b.weekday_mask for b in pin_pool.compute_offpeak_buckets()}
    return _OFFPEAK_MASK.get(hour, 127)


def slot_params(slot_hour: int) -> tuple[int, int, int]:
    """(allowed_from_time, allowed_until_time, weekday_mask) — identisch zur Rotation."""
    if slot_hour == pin_pool.FALLBACK_HOUR:
        return pin_pool.FALLBACK_FROM_MIN, pin_pool.FALLBACK_UNTIL_MIN, 127
    return slot_hour * 60, min((slot_hour + 1) * 60, 1439), _offpeak_mask(slot_hour)


def slot_name(slot_hour: int, pool_index: int) -> str:
    if slot_hour == pin_pool.FALLBACK_HOUR:
        return f"og-bh-p{pool_index}"
    return f"og-h{slot_hour:02d}-p{pool_index}"


def slot_kind(weekday: int, hour: int) -> int:
    """slot_hour für die Buchung: echte Stunde (off-peak) oder FALLBACK_HOUR (Geschäftszeit)."""
    return hour if pin_pool.needs_keypad_code(weekday, hour) else pin_pool.FALLBACK_HOUR


def pool_size(slot_hour: int) -> int:
    return pin_pool.FALLBACK_POOL if slot_hour == pin_pool.FALLBACK_HOUR else pin_pool.POOL_PER_HOUR


def booking_params(window: dict, tz_name: str) -> tuple[int, int, int, date]:
    """(weekday 0=Mo..6=So, hour, start_minute, on_date) der Buchung in Berlin-Zeit."""
    local = to_berlin_tz(window["starts_at"], tz_name)
    return local.weekday(), local.hour, local.hour * 60 + local.minute, local.date()


# ── Verifikation ──────────────────────────────────────────────────
def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def evaluate(auths: list[dict], *, code, weekday: int, start_minute: int, on_date: date) -> dict:
    """Ist ``code`` eine materialisierte type-13-Auth, die die erste Stunde abdeckt?

    Prüft: vorhanden, aktiviert, materialisiert (updateDate), Wochentag-Bit gesetzt,
    Tageszeit-Fenster enthält die Startminute, Datumsbereich gültig.
    """
    try:
        want = int(code)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "bad_code", "auth": None}
    match = next((a for a in auths if a.get("type") == 13 and a.get("code") == want), None)
    if match is None:
        return {"ok": False, "reason": "not_found", "auth": None}
    if not match.get("enabled", True):
        return {"ok": False, "reason": "disabled", "auth": match}
    if not match.get("updateDate"):
        return {"ok": False, "reason": "not_materialised", "auth": match}
    mask = match.get("allowedWeekDays", 127)
    if not (mask & pin_pool.WEEKDAY_BIT[weekday]):
        return {"ok": False, "reason": "weekday_excluded", "auth": match}
    ft, ut = match.get("allowedFromTime"), match.get("allowedUntilTime")
    if ft is not None and ut is not None and not (ft <= start_minute <= ut):
        return {"ok": False, "reason": "time_window", "auth": match}
    fd, ud = _parse_dt(match.get("allowedFromDate")), _parse_dt(match.get("allowedUntilDate"))
    if fd and on_date < fd.date():
        return {"ok": False, "reason": "date_before", "auth": match}
    if ud and on_date > ud.date():
        return {"ok": False, "reason": "date_after", "auth": match}
    return {"ok": True, "reason": "ok", "auth": match}


def _wait_code(nuki, code, *, timeout: float = 60.0, step: float = 4.0) -> bool:
    """Poll bis ``code`` materialisiert (updateDate) am Schloss erscheint."""
    want = int(code)
    t0 = _time.time()
    while _time.time() - t0 < timeout:
        try:
            if any(a.get("code") == want and a.get("updateDate") for a in nuki.list_keypad_codes()):
                return True
        except Exception:
            pass
        _time.sleep(step)
    return False


def _remediate(nuki, *, code, slot_hour: int, name: str, day: date, reason: str) -> bool:
    """Bringe ``code`` materialisiert ans Schloss. Bei not_materialised: force_sync +
    warten (kein Neuanlegen). Sonst: exakt diesen Code unter dem Slot-Namen anlegen
    (die nächste Rotation räumt Dubletten auf). True, wenn danach materialisiert."""
    try:
        if reason == "not_materialised":
            nuki.force_sync()
            return _wait_code(nuki, code, timeout=60.0)
        ft, ut, mask = slot_params(slot_hour)
        nuki.create_keypad_code(
            name=name, code=str(code),
            allowed_from=f"{day.isoformat()}T00:00:00Z",
            allowed_until=f"{(day + timedelta(days=30)).isoformat()}T23:59:59Z",
            allowed_week_days=mask, allowed_from_time=ft, allowed_until_time=ut,
        )
        return _wait_code(nuki, code, timeout=75.0)
    except Exception as exc:
        if "409" in str(exc):
            logger.error("guardian._remediate: 409 — Code (…%s) nicht anlegbar (tombstoned)", str(code)[-2:])
        else:
            logger.error("guardian._remediate(%s) failed: %s", name, exc)
        return False


# ── Wächter B: Verifikation vor dem Versand ───────────────────────
def verify_for_send(nuki, db, settings, *, smartlock_id: int, slot_hour: int,
                    pool_index: int, pin: str, weekday: int, start_minute: int,
                    on_date: date, day: date) -> dict:
    """Prüfe den zu sendenden PIN am Schloss. Trägt er nicht, wähle einen bereits
    materialisierten Geschwister-Code; notfalls materialisiere den PIN nach.

    Rückgabe: {pin, pool_index, verified, reason, switched}. ``pin``/``pool_index``
    sind der TATSÄCHLICH zu versendende (ggf. umgeschaltete) Code.
    """
    autofix = bool(getattr(settings, "guardian_autofix", True)) and not settings.nuki_dry_run
    try:
        auths = nuki.list_keypad_codes()
    except Exception as exc:
        logger.error("guardian.verify_for_send: list failed: %s", exc)
        return {"pin": pin, "pool_index": pool_index, "verified": False,
                "reason": "list_failed", "switched": False}

    res = evaluate(auths, code=pin, weekday=weekday, start_minute=start_minute, on_date=on_date)
    if res["ok"]:
        return {"pin": pin, "pool_index": pool_index, "verified": True,
                "reason": "ok", "switched": False}

    logger.warning("guardian: to-send pin slot=%s p%s not valid (%s) — searching sibling",
                   slot_hour, pool_index, res["reason"])
    for idx in range(pool_size(slot_hour)):
        if idx == pool_index:
            continue
        alt = store.get_todays_slot_pin(db, smartlock_id=smartlock_id, hour=slot_hour,
                                        pool_index=idx, rotation_date=day)
        if alt and evaluate(auths, code=alt, weekday=weekday, start_minute=start_minute,
                            on_date=on_date)["ok"]:
            logger.warning("guardian: switch to materialised sibling p%s (slot=%s)", idx, slot_hour)
            return {"pin": alt, "pool_index": idx, "verified": True,
                    "reason": "switched", "switched": True}

    if autofix and _remediate(nuki, code=pin, slot_hour=slot_hour,
                              name=slot_name(slot_hour, pool_index), day=day, reason=res["reason"]):
        return {"pin": pin, "pool_index": pool_index, "verified": True,
                "reason": "healed", "switched": False}

    return {"pin": pin, "pool_index": pool_index, "verified": False,
            "reason": res["reason"], "switched": False}


# ── Wächter A: periodischer Hintergrund-Check ─────────────────────
def _imminent_windows(db, since: datetime, until: datetime) -> list[dict]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT aw.id, aw.member_id, aw.starts_at, aw.ends_at,
                   m.email, m.first_name, m.last_name
            FROM access_windows aw JOIN members m ON m.id = aw.member_id
            WHERE aw.status IN (%s, %s) AND aw.starts_at >= %s AND aw.starts_at <= %s
            ORDER BY aw.starts_at ASC
            """,
            (AccessWindowStatus.ACTIVE, AccessWindowStatus.SCHEDULED, since, until),
        )
        return list(cur.fetchall())


def _cooldown_ok(db, key: str, minutes: int) -> bool:
    """True (und Zeitstempel gesetzt), wenn seit dem letzten Mal > ``minutes`` vergingen.
    Verhindert Alarm-/Heilungs-Spam. Fehler → True (lieber handeln als schweigen)."""
    skey = f"guardian_cd:{key}"
    now = now_utc()
    try:
        row = db.get_system_setting(skey)  # -> {"ts": "..."} oder None (Value direkt)
        if isinstance(row, dict):
            last = _parse_dt(row.get("ts"))
            if last and (now - last).total_seconds() < minutes * 60:
                return False
        db.set_system_setting(key=skey, value={"ts": now.isoformat()})
    except Exception:
        logger.debug("guardian cooldown check failed for %s", key, exc_info=True)
    return True


def _alert(db, settings, severity, kind: str, message: str, payload: dict | None = None) -> None:
    from ..services.alerts import create_operational_alert
    try:
        create_operational_alert(db=db, settings=settings, severity=severity,
                                 kind=kind, message=message, payload=payload)
    except Exception:
        logger.exception("guardian alert failed: %s", kind)


def _check_log_stale(db, settings, nuki) -> None:
    hours = int(getattr(settings, "nuki_log_stale_alert_hours", 48))
    try:
        log = nuki.get_log(limit=1)
    except Exception:
        return
    if not log:
        return
    newest = _parse_dt(log[0].get("date"))
    if not newest:
        return
    age_h = (now_utc() - newest).total_seconds() / 3600
    if age_h > hours and _cooldown_ok(db, "logstale", minutes=720):
        _alert(db, settings, AlertSeverity.WARNING, "nuki_log_stale",
               f"Nuki-Aktivitätslog {age_h:.0f}h alt (>{hours}h) — Keypad-Event-Sicht blind. "
               "Log-Upload/Bridge-Anbindung am Schloss prüfen.")


def guardian_tick(db, settings) -> dict:
    """Ein Wächter-Durchlauf: verifiziere alle bevorstehenden/laufenden Buchungen (erste
    Stunde) gegen das Schloss, heile Fehlende, alarmiere. Für den Worker-Loop."""
    if not bool(getattr(settings, "guardian_enabled", True)):
        return {"skipped": "disabled"}
    from ..nuki_client import NukiClient
    from ..services.settings import get_effective_nuki_config

    nuki_cfg = get_effective_nuki_config(db, settings)
    effective = settings.model_copy(update=nuki_cfg)
    if effective.nuki_dry_run:
        return {"skipped": "dry_run"}
    smartlock_id = int(nuki_cfg["nuki_smartlock_id"] or 0)
    nuki = NukiClient(effective)
    checked = healed = failed = 0
    autofix = bool(getattr(settings, "guardian_autofix", True))
    try:
        auths = nuki.list_keypad_codes()
        now = now_utc()
        lookahead = timedelta(minutes=int(getattr(settings, "guardian_lookahead_minutes", 90)))
        grace = int(getattr(settings, "guardian_grace_minutes", 20))
        rows = _imminent_windows(db, now - timedelta(minutes=60), now + lookahead)
        for w in rows:
            weekday, hour, start_min, on_date = booking_params(w, settings.timezone)
            slot_hour = slot_kind(weekday, hour)
            pidx = store.assigned_pool_index(db, member_ref=str(w["member_id"]),
                                             weekday=weekday, hour=slot_hour, assigned_date=on_date)
            if pidx is None:
                mins = (w["starts_at"] - now).total_seconds() / 60
                if 0 <= mins <= grace and _cooldown_ok(db, f"noassign:{w['id']}", minutes=30):
                    _alert(db, settings, AlertSeverity.WARNING, "guardian_no_assignment",
                           f"Buchung {w['id']} startet in {int(mins)} min, aber noch kein Code zugewiesen.")
                continue
            pin = store.get_todays_slot_pin(db, smartlock_id=smartlock_id, hour=slot_hour,
                                            pool_index=pidx, rotation_date=on_date)
            if not pin:
                continue
            checked += 1
            res = evaluate(auths, code=pin, weekday=weekday, start_minute=start_min, on_date=on_date)
            if res["ok"]:
                continue
            name = slot_name(slot_hour, pidx)
            if autofix and _cooldown_ok(db, f"heal:{w['id']}", minutes=10):
                if _remediate(nuki, code=pin, slot_hour=slot_hour, name=name,
                              day=on_date, reason=res["reason"]):
                    auths = nuki.list_keypad_codes()
                    healed += 1
                    _alert(db, settings, AlertSeverity.WARNING, "guardian_healed",
                           f"Code für Buchung {w['id']} ({name}) war '{res['reason']}' — "
                           "sofort nachmaterialisiert.")
                    continue
            failed += 1
            if _cooldown_ok(db, f"fail:{w['id']}", minutes=10):
                _alert(db, settings, AlertSeverity.ERROR, "guardian_unhealed",
                       f"Code für Buchung {w['id']} ({name}) NICHT am Schloss ('{res['reason']}') "
                       "und Heilung fehlgeschlagen — MANUELLER EINGRIFF nötig.")
        _check_log_stale(db, settings, nuki)
    finally:
        nuki.close()
    result = {"checked": checked, "healed": healed, "failed": failed}
    if checked or healed or failed:
        logger.info("guardian_tick: checked=%s healed=%s failed=%s", checked, healed, failed)
    return result
