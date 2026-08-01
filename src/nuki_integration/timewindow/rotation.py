"""Rotation + Zuweisung + Zustellung für das Zeitfenster-Modell (M2b).

Ersetzt das per-Buchung-Provisioning. Ablauf je Worker-Cycle:
  1. ``rotate_daily`` — 101 feste Slots (96 Off-Peak + 5 Business-Hours-Fallback)
     sicherstellen, Tages-PINs rotieren, jeden
     Slot als type-13-Auth mit Zeitfenster pushen (DRY-RUN → skip) + Materiali-
     sierung prüfen, in ``nuki_pin_history`` festhalten. Einmal pro Tag.
  2. Für jede fällige Off-Peak-Buchung ``assign_and_deliver`` — Mitglied→Slot
     (Anti-Repeat), Tages-PIN des Slots per Mailer (send_access_code) zustellen.
     Guard: Buchung innerhalb der Geschäftszeiten → kein Code.

Kollaborateure (``nuki``, ``email_service``) werden injiziert, damit die Logik
ohne echtes Nuki/SMTP testbar ist.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import date, datetime, timedelta

from ..datetime_utils import now_utc, to_berlin_tz
from ..services.formatting import fmt_dt_de
from . import pin_pool, store

logger = logging.getLogger(__name__)

BUFFER_DAYS = 30  # Vorauspush-Fenster (14–30 Tage)

# Throttle/backoff for Nuki writes (incident-fix: 96 simultaneous creates → 429).
WRITE_PAUSE_SECS = 0.6          # pause between every Nuki write call
RETRY_MAX = 5                   # retries on HTTP 429
BACKOFF_BASE_SECS = 2.0         # exponential backoff base (2,4,8,16s)
MATERIALISE_SETTLE_SECS = 4.0   # wait before the single materialisation GET


def _nuki_write_with_retry(fn, **kwargs):
    """Call a Nuki write (create/update); on 429 back off exponentially and retry."""
    for attempt in range(RETRY_MAX):
        try:
            return fn(**kwargs)
        except Exception as exc:  # NukiApiError carries "Nuki API 429: ..."
            if "429" in str(exc) and attempt < RETRY_MAX - 1:
                delay = BACKOFF_BASE_SECS * (2 ** attempt)
                logger.warning("Nuki 429 — backoff %.1fs (retry %d/%d)", delay, attempt + 1, RETRY_MAX)
                time.sleep(delay)
                continue
            raise


def _fresh_pin() -> str:
    """Neuer regelkonformer PIN fuer 409-Retries in der Rotation."""
    return pin_pool.gen_pin(random.Random(), set())


def berlin_weekday_hour(dt: datetime, tz_name: str = "Europe/Berlin") -> tuple[int, int]:
    """(weekday 0=Mo..6=So, hour) der Buchung in Berlin-Zeit."""
    local = to_berlin_tz(dt, tz_name)
    return local.weekday(), local.hour


# ── Tägliche Rotation ─────────────────────────────────────────────
ROTATION_START_HOUR = 10  # Berlin local hour the daily rotation may begin
ROTATION_BUSY_HORIZON_MIN = 20  # don't start if an appointment is active or begins within this


def _opengym_busy(db) -> bool:
    """True if an OpenGym appointment is in progress now or starts within the next
    ROTATION_BUSY_HORIZON_MIN minutes (so a ~16 min rotation won't collide with a
    session). On any DB error, returns True (fail-safe: defer rather than disturb)."""
    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM access_windows "
                "WHERE ends_at >= now() AND starts_at <= now() + (%s * interval '1 minute') LIMIT 1",
                (ROTATION_BUSY_HORIZON_MIN,),
            )
            return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("rotate_daily: _opengym_busy check failed (%s) — deferring to be safe", exc)
        return True


def _wait_present(nuki, name: str, code, timeout: float = 15.0, step: float = 3.0):
    """Poll until the auth (name+code) is PRESENT on the lock's auth list — i.e. the
    create/push landed — regardless of whether the device has echoed a confirmation
    (``updateDate``) back yet.

    Existence appears cloud-side within seconds of a create; device confirmation may lag
    for minutes on a degraded link and is unreliable, so waiting on it wasted ~75 s/slot
    for no gain (the code is already on the lock, and the old code was deleted after the
    timeout anyway). We therefore wait only for presence. Returns the matching auth dict
    (the caller reads ``updateDate`` for the health signal), or None if it never appears
    — a genuine create/push miss."""
    import time as _t
    t0 = _t.time()
    while _t.time() - t0 < timeout:
        try:
            for a in nuki.list_keypad_codes():
                if a.get("name") == name and str(a.get("code")) == str(code):
                    return a
        except Exception:
            pass
        _t.sleep(step)
    return None


def _wait_gone(nuki, auth_id, timeout: float = 8.0, step: float = 4.0) -> bool:
    """Poll until auth_id disappears from the lock. False if still present (tombstone)."""
    import time as _t
    t0 = _t.time()
    while _t.time() - t0 < timeout:
        try:
            if str(auth_id) not in {str(a.get("id")) for a in nuki.list_keypad_codes()}:
                return True
        except Exception:
            pass
        _t.sleep(step)
    return False


def rotate_daily(
    db,
    *,
    nuki,
    smartlock_id: int,
    day: date | None = None,
    dry_run: bool = True,
    buffer_days: int = BUFFER_DAYS,
    rng=None,
    force: bool = False,
) -> dict:
    """Daily CREATE-FIRST rotation of all 101 slots (96 time-window + 5 fallback).

    Trigger (live, non-force): once per calendar day, at/after ROTATION_START_HOUR
    Berlin, but only when NO OpenGym appointment is in progress / imminent — else it
    defers to a later worker cycle (i.e. rotates from the end of the running
    appointment). Per slot: create a fresh code -> wait until it is materialised on
    the device -> only THEN delete the old auth(s) -> tombstone a stuck delete. The
    old code stays valid until the new one is device-confirmed, so there is never an
    access gap, and create-first avoids the delete-then-create race + sync-jam.
    """
    day = day or now_utc().date()
    store.ensure_schema(db)

    expected = pin_pool.expected_slot_count()  # 96 Off-Peak + 5 Business-Hours-Fallback
    if not force and store.rotation_count_for_day(db, day) >= expected:
        logger.info("rotate_daily: rotation for %s already present — skipping", day)
        return {**store.rotation_status(db, day), "skipped": True}

    # Schedule gate (only for live, non-forced scheduled runs).
    if not force and not dry_run:
        now_b = to_berlin_tz(now_utc())
        if now_b.hour < ROTATION_START_HOUR:
            logger.info("rotate_daily: before %02d:00 Berlin (now %s) — waiting",
                        ROTATION_START_HOUR, now_b.strftime("%H:%M"))
            return {**store.rotation_status(db, day), "skipped": True, "deferred": "before-start"}
        if _opengym_busy(db):
            logger.info("rotate_daily: OpenGym appointment active/imminent — deferring rotation")
            return {**store.rotation_status(db, day), "skipped": True, "deferred": "appointment"}

    buckets = pin_pool.compute_offpeak_buckets()
    slots = pin_pool.build_slots(buckets) + pin_pool.build_fallback_slots()
    pin_pool.assert_within_budget(slots)
    pin_pool.rotate_pins(slots, rng)

    allowed_from = f"{day.isoformat()}T00:00:00Z"
    allowed_until = f"{(day + timedelta(days=buffer_days)).isoformat()}T23:59:59Z"

    # DRY-RUN: simulate (no network), record, return.
    if dry_run:
        materialised = 0
        for slot in slots:
            slot_id = store.upsert_slot(
                db, smartlock_id=smartlock_id, name=slot.name, hour=slot.bucket.hour,
                pool_index=slot.pool_index, weekday_mask=slot.bucket.weekday_mask,
                from_time=slot.bucket.from_time, until_time=slot.bucket.until_time)
            mat = nuki.verify_materialization(slot.code)
            m = bool(mat.get("materialised"))
            materialised += int(m)
            store.record_rotation(db, slot_id=slot_id, rotation_date=day, pin=slot.code,
                                  pushed=False, materialised=m, dry_run=True)
        logger.info("rotate_daily %s DRY-RUN: %d slots simulated", day, len(slots))
        return {"day": day.isoformat(), "slots": len(slots), "pushed": 0, "created": 0,
                "updated": 0, "materialised": materialised, "alerts": 0, "dry_run": True, "skipped": False}

    # LIVE: capture current auths per slot name (predecessors to delete after cutover).
    pre: dict[str, list] = {}
    try:
        for a in nuki.list_keypad_codes():
            nm = a.get("name") or ""
            # "og-" deckt sowohl die Off-Peak-Slots (og-hHH-pX) ALS AUCH die
            # Business-Hours-Fallback-Slots (og-bh-pX) ab. Ein engerer "og-h"-Filter
            # würde die 5 Fallback-Vorgänger nie erfassen → sie würden nie gelöscht
            # (Slot-Leak: stale-aber-gültige Codes + Nuki-200-Limit).
            if nm.startswith("og-") and a.get("id") is not None:
                pre.setdefault(nm, []).append(a["id"])
    except Exception as exc:
        logger.error("rotate_daily: could not fetch current auths (%s)", exc)

    created = 0
    materialised = 0
    alerts = 0
    tombstones = 0
    for slot in slots:
        slot_id = store.upsert_slot(
            db, smartlock_id=smartlock_id, name=slot.name, hour=slot.bucket.hour,
            pool_index=slot.pool_index, weekday_mask=slot.bucket.weekday_mask,
            from_time=slot.bucket.from_time, until_time=slot.bucket.until_time)

        # 1) CREATE new (fresh code; on 409 collision retry with a fresh pin)
        for _attempt in range(RETRY_MAX):
            try:
                _nuki_write_with_retry(
                    nuki.create_keypad_code, name=slot.name, code=slot.code,
                    allowed_from=allowed_from, allowed_until=allowed_until,
                    allowed_week_days=slot.bucket.weekday_mask,
                    allowed_from_time=slot.bucket.from_time, allowed_until_time=slot.bucket.until_time)
                break
            except Exception as exc:
                if "409" in str(exc) and _attempt < RETRY_MAX - 1:
                    logger.warning("rotate_daily: 409 on %s — retry fresh pin", slot.name)
                    slot.code = _fresh_pin()
                    continue
                raise
        created += 1

        # 2) VERIFY the new code is PRESENT on the lock before removing the predecessor.
        # We wait for existence (create/push landed) — fast + reliable — NOT for the
        # device confirmation (updateDate), which lags/is unreliable and would waste
        # ~75 s/slot. ``m`` (device-confirmed) is still recorded as a health signal for
        # the early-warning; the per-slot ALERT now fires only on a genuine create miss.
        new_auth = _wait_present(nuki, slot.name, slot.code)
        present = new_auth is not None
        m = bool(new_auth.get("updateDate")) if new_auth else False
        if m:
            materialised += 1
        if not present:
            alerts += 1
            logger.error("[ALERT] slot %s new code NOT present on lock (create/push failed)", slot.name)

        # 3) DELETE predecessor(s) — ONLY if the new code is actually PRESENT on the lock
        # (create/push landed). This is the real no-access-gap guarantee: never remove a
        # working code without a live successor. If the create did not land (present=False),
        # KEEP the old (working) code so members are never locked out by a rotation whose
        # creates are failing (the 2026-08-01 near-miss). A left-over predecessor is
        # harmless (it still opens the door) and gets cleaned up on the next healthy run.
        if not present:
            logger.error(
                "rotate_daily: slot %s new code NOT present — KEEPING predecessor(s), "
                "no delete without a live successor", slot.name)
        else:
            for aid in pre.get(slot.name, []):
                try:
                    _nuki_write_with_retry(nuki.delete_keypad_code, auth_id=aid)
                except Exception as exc:
                    logger.warning("rotate_daily: delete old %s failed: %s", slot.name, exc)
                if not _wait_gone(nuki, aid):
                    tombstones += 1
                    logger.warning("rotate_daily: old auth %s (%s) still visible — tombstoned", slot.name, aid)

        # 4) Persist DB (auth_id + today's pin) — delivery reads pin_history
        new_id = new_auth.get("id") if new_auth else None
        if new_id is None:
            try:
                for a in nuki.list_keypad_codes():
                    if a.get("name") == slot.name and str(a.get("code")) == str(slot.code):
                        new_id = a.get("id")
                        break
            except Exception:
                pass
        if new_id is not None:
            store.set_slot_auth_id(db, slot_id, str(new_id))
        store.record_rotation(db, slot_id=slot_id, rotation_date=day, pin=slot.code,
                              pushed=True, materialised=m, dry_run=False)
        time.sleep(WRITE_PAUSE_SECS)

    logger.info("rotate_daily %s: CREATE-FIRST done — %d slots, materialised=%d alerts=%d tombstones=%d",
                day, len(slots), materialised, alerts, tombstones)
    return {"day": day.isoformat(), "slots": len(slots), "pushed": created, "created": created,
            "updated": 0, "materialised": materialised, "alerts": alerts, "tombstones": tombstones,
            "dry_run": False, "skipped": False}


# ── Zuweisung + Zustellung je Buchung ─────────────────────────────
def _member_name(window: dict) -> str:
    name = f"{window.get('first_name') or ''} {window.get('last_name') or ''}".strip()
    return name or str(window.get("email") or f"Mitglied {window.get('member_id')}")


def assign_and_deliver(
    db,
    *,
    window: dict,
    email_service,
    smartlock_id: int,
    day: date | None = None,
    tz_name: str = "Europe/Berlin",
    mark_handled=None,
    nuki=None,
    settings=None,
    buffer_days: int = BUFFER_DAYS,
) -> dict:
    """Ordne eine Buchung einem Slot zu und stelle den Tages-PIN zu.

    Bei mehrstündigen Buchungen wird ausschließlich die ERSTE gebuchte Stunde
    (der ungepufferte ``booking_starts_at``, Fallback ``starts_at``) für
    Slot-Zuordnung UND Verifikation herangezogen.

    Wird ``nuki`` übergeben (Produktionspfad via :func:`run_timewindow_cycle`), gilt
    **fail closed**: der Code wird vor dem Versand über die Nuki-API verifiziert
    (materialisiert + gültig für die erste Stunde) und bei Bedarf einmalig
    repariert. Ohne bestätigte Materialisierung erfolgt KEIN Versand — stattdessen
    ein Operational Alert; die Buchung bleibt fällig und wird im nächsten Cycle
    erneut versucht. ``nuki=None`` überspringt die Geräteprüfung (reine
    Zuordnungslogik, für isolierte Unit-Tests).

    ``mark_handled(window_id)`` wird aufgerufen, um die Buchung als erledigt zu
    markieren (Standard: keine Markierung — für Tests). Rückgabe beschreibt den
    Ausgang.
    """
    day = day or now_utc().date()
    # Slot-Routing MUSS auf der TATSAECHLICHEN ersten gebuchten Stunde basieren, nicht
    # auf ``starts_at`` (= Buchungsstart - 15 min Vor-Puffer). Sonst waehlt eine
    # 09:00-Buchung faelschlich den 08:00-Slot (og-h08, gueltig 08:00-09:00).
    # ``due_access_windows`` liefert dazu ``booking_starts_at`` (bookings.start_at der
    # ersten Cluster-Buchung); Fallback auf ``starts_at`` nur im reinen Unit-Pfad.
    booking_start = window.get("booking_starts_at") or window["starts_at"]
    weekday, hour = berlin_weekday_hour(booking_start, tz_name)  # erste Stunde, ungepuffert
    member_ref = str(window["member_id"])

    if pin_pool.needs_keypad_code(weekday, hour):
        # Off-Peak: stundengenauer Slot (og-hHH-pX)
        slot_hour = hour
        recent = store.recent_pool_indices(
            db, member_ref=member_ref, weekday=weekday, hour=slot_hour, limit=pin_pool.ANTI_REPEAT_DEPTH
        )
        pool_index = pin_pool.choose_pool_index(recent)
        slot_name = f"og-h{hour:02d}-p{pool_index}"
        # Echtes Türfenster des Codes = die gebuchte Uhrstunde (Minuten seit
        # Mitternacht, wie Nuki allowedFromTime/UntilTime des og-hHH-Slots).
        door_from_min, door_until_min = hour * 60, min((hour + 1) * 60, 1439)
    else:
        # Geschäftszeit: IMMER einen der 5 Business-Hours-Fallback-Codes zustellen
        # (der Laden könnte trotz "Öffnungszeit" zu sein — Feiertag/Urlaub/krank).
        slot_hour = pin_pool.FALLBACK_HOUR
        recent = store.recent_pool_indices(
            db, member_ref=member_ref, weekday=weekday, hour=slot_hour, limit=pin_pool.FALLBACK_POOL
        )
        pool_index = pin_pool.choose_fallback_index(recent)
        slot_name = f"og-bh-p{pool_index}"
        # Business-Hours-Fallback-Code: 08:00–21:00-Fenster (og-bh-Slot).
        door_from_min, door_until_min = pin_pool.FALLBACK_FROM_MIN, pin_pool.FALLBACK_UNTIL_MIN

    pin = store.get_todays_slot_pin(
        db, smartlock_id=smartlock_id, hour=slot_hour, pool_index=pool_index, rotation_date=day
    )
    if pin is None:
        logger.warning("assign_and_deliver: no rotated PIN for %s on %s — skipping", slot_name, day)
        return {"window_id": window.get("id"), "no_code": False, "assigned": False,
                "delivered": False, "reason": "no-rotation"}

    # ── Fail-closed Geräte-Verifikation VOR Zuweisung UND Versand ──────────
    # Die Anti-Repeat-Zuweisung wird ERST NACH bestätigter Materialisierung
    # geschrieben: ein blockierter (unversendeter) Code darf nie als der zuletzt
    # dem Mitglied zugestellte gelten (sonst würde der Wächter den falschen Code
    # re-materialisieren). Ohne injizierten ``nuki`` (reiner Unit-Pfad) entfällt die
    # Geräteprüfung und die Zuweisung wird wie gehabt geschrieben.
    if nuki is not None:
        verify = verify_slot_code(
            db, nuki=nuki, smartlock_id=smartlock_id, slot_name=slot_name,
            slot_hour=slot_hour, pool_index=pool_index, code=pin,
            weekday=weekday, hour=hour, day=day, buffer_days=buffer_days,
        )
        if not verify.get("deliverable"):
            # Fail-closed ONLY when the code is genuinely not usable: not present on the
            # lock (create/push failed), wrong window after a repair, or the device/API
            # was unreachable. We never dispatch a code that isn't provisioned. A
            # transient unreachable is retried next cycle, not paged.
            if not verify.get("unreachable"):
                _alert_dispatch_blocked(db, settings, window, slot_name, verify)
            logger.error(
                "assign_and_deliver: FAIL-CLOSED window=%s slot=%s not dispatched "
                "(exists=%s covers=%s unreachable=%s)",
                window.get("id"), slot_name, verify.get("exists"),
                verify.get("covers_window"), verify.get("unreachable"),
            )
            return {
                "window_id": window.get("id"), "no_code": False, "assigned": False,
                "pool_index": pool_index, "slot_name": slot_name,
                "delivered": False, "verified": False,
                "reason": ("unreachable" if verify.get("unreachable")
                           else "not-present" if not verify.get("exists")
                           else "wrong-window"),
            }

        # Deliverable but not device-confirmed: the code IS on the lock and opens the
        # door (create/push is reliable), but the device→cloud confirmation (updateDate)
        # is lagging or broken. Deliver anyway — do NOT lock members out on a slow cloud
        # echo — and raise a deduped early-warning so the operator sees the degraded link
        # days before it becomes critical (instead of a silent total blackout).
        if not verify.get("materialised") and not verify.get("simulated"):
            _alert_delivery_unconfirmed(db, settings, window, slot_name)
            logger.warning(
                "assign_and_deliver: window=%s slot=%s delivered UNCONFIRMED "
                "(code present + window ok, device→cloud confirmation lagging)",
                window.get("id"), slot_name,
            )

    store.record_assignment(
        db, member_ref=member_ref, weekday=weekday, hour=slot_hour,
        pool_index=pool_index, assigned_date=day,
    )

    delivered = False
    if window.get("email"):
        # Gültigkeit = ECHTES Türfenster des ausgelieferten Codes (die gebuchte
        # Uhrstunde bzw. das Business-Hours-Fallback-Fenster), NICHT das gepufferte
        # access_window (starts_at −15 min / ends_at +30 min). Sonst verspricht die
        # Mail mehr Zutritt, als das Schloss öffnet (Vorfall 24.07.: Mail "bis 07:30",
        # der og-h06-Code sperrte aber schon 07:00).
        midnight = to_berlin_tz(booking_start, tz_name).replace(
            hour=0, minute=0, second=0, microsecond=0)
        valid_from = fmt_dt_de(midnight + timedelta(minutes=door_from_min))
        valid_until = fmt_dt_de(midnight + timedelta(minutes=door_until_min))
        member_name = _member_name(window)
        # Gebrandetes HTML-Template + Check-in-/Checks-Links wie der manuelle Versand
        # (services.access._send_code_email); nur möglich, wenn ``settings`` vorliegt
        # (Produktionspfad). Ohne settings (reine Unit-Zuordnung) bleibt es Plaintext.
        extra: dict = {}
        if settings is not None:
            from ..services.auth_tokens import build_check_in_link, build_checks_link
            from ..services.email_builder import build_access_code_email_html
            from ..services.settings import get_effective_check_in_settings
            checks_url = build_checks_link(
                checks_key=str(window["checks_key"]) if window.get("checks_key") else None,
                member_id=int(window["member_id"]), settings=settings,
            )
            check_in_enabled = bool(get_effective_check_in_settings(db, settings).get("enabled"))
            extra = {
                "checks_url": checks_url,
                "check_in_url": (
                    build_check_in_link(
                        access_window_id=int(window["id"]),
                        ends_at=window["ends_at"], settings=settings,
                    ) if check_in_enabled else None
                ),
                "html_body": build_access_code_email_html(
                    db, settings, member_name=member_name, code=pin,
                    valid_from=valid_from, valid_until=valid_until, checks_url=checks_url,
                ),
            }
        delivered = bool(email_service.send_access_code(
            to_email=str(window["email"]), member_name=member_name,
            code=pin, valid_from=valid_from, valid_until=valid_until, **extra,
        ))

    if mark_handled:
        mark_handled(int(window["id"]), pin=pin, delivered=delivered)

    logger.info(
        "assigned window=%s member=%s → slot %s (delivered=%s)",
        window.get("id"), member_ref, slot_name, delivered,
    )
    return {
        "window_id": window.get("id"), "no_code": False, "assigned": True,
        "pool_index": pool_index, "slot_name": slot_name, "delivered": delivered,
        "verified": True,
    }


def verify_slot_code(
    db, *, nuki, smartlock_id: int, slot_name: str, slot_hour: int, pool_index: int,
    code: str, weekday: int, hour: int, day: date, buffer_days: int = BUFFER_DAYS,
) -> dict:
    """Verify (and repair once) that ``code`` is materialised for the booked hour.

    Loads the slot's persisted Nuki time-window fields and delegates to
    :func:`services.nuki_verification.ensure_code_materialised`. Falls back to the
    pin_pool-derived window if the slot row is not (yet) persisted.
    """
    from ..services.nuki_verification import ensure_code_materialised

    slot = store.get_slot(db, smartlock_id=smartlock_id, hour=slot_hour, pool_index=pool_index)
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
    return ensure_code_materialised(
        nuki, slot_name=slot_name, code=code, weekday=weekday, hour=hour,
        weekday_mask=weekday_mask, from_time=from_time, until_time=until_time,
        allowed_from=allowed_from, allowed_until=allowed_until, auth_id=auth_id,
    )


def _alert_dispatch_blocked(db, settings, window: dict, slot_name: str, verify: dict) -> None:
    """Persist an operational alert when a dispatch is blocked (fail closed)."""
    if db is None or settings is None:
        return
    try:
        from ..enums import AlertSeverity
        from ..services import monitoring
        # Deduped (idempotent, cooldown) so a persistently-blocked window does not
        # re-alert every worker tick. Keyed by window id.
        monitoring.notify(
            db, settings, key=f"code-not-materialised:{window.get('id')}",
            severity=AlertSeverity.ERROR, kind="code-not-materialised",
            title=f"Access code for window {window.get('id')} (slot {slot_name}) not materialised — dispatch blocked (fail closed).",
            detail=f"member#{window.get('member_id')}",
            payload={
                "access_window_id": window.get("id"),
                "member_id": window.get("member_id"),
                "slot_name": slot_name,
                "materialised": verify.get("materialised"),
                "covers_window": verify.get("covers_window"),
                "repaired": verify.get("repaired"),
            },
            cooldown_secs=2 * 60 * 60,
        )
    except Exception:
        logger.exception("_alert_dispatch_blocked: failed to record alert")


def _alert_delivery_unconfirmed(db, settings, window: dict, slot_name: str) -> None:
    """Early-warning: a working code was delivered WITHOUT a device confirmation.

    Signals a degraded device→cloud link — codes reach the lock and open the door, but
    the device is not echoing the confirmation (``updateDate``) back to the cloud. Access
    still works; this is the heads-up to check the lock's cloud/Wi-Fi connection BEFORE it
    deteriorates into a full outage. Deduped with a long cooldown (6 h) so it is a health
    signal, not per-booking spam — keyed globally, not per-window.
    """
    if db is None or settings is None:
        return
    try:
        from ..enums import AlertSeverity
        from ..services import monitoring
        monitoring.notify(
            db, settings, key="delivery-unconfirmed",
            severity=AlertSeverity.WARNING, kind="delivery-unconfirmed",
            title="Zutrittscodes werden UNBESTÄTIGT zugestellt — Gerät→Cloud-Sync gestört.",
            detail=("Codes gelangen aufs Schloss und öffnen die Tür, aber das Gerät meldet "
                    "die Bestätigung (updateDate) nicht mehr an die Cloud zurück. Zutritt "
                    "funktioniert — Cloud-/WLAN-Anbindung des Nuki prüfen, bevor sie ganz abreißt."),
            payload={"example_window": window.get("id"), "slot_name": slot_name},
            cooldown_secs=6 * 60 * 60,
        )
    except Exception:
        logger.exception("_alert_delivery_unconfirmed: failed to record alert")


def _alert_rotation_unconfirmed(db, settings, rotation: dict) -> None:
    """Early-warning at rotation time: all freshly pushed codes are present on the lock
    but NONE are device-confirmed (updateDate) — the degraded device→cloud link. Fires
    even on days without bookings. Deduped 6 h."""
    if db is None or settings is None:
        return
    try:
        from ..enums import AlertSeverity
        from ..services import monitoring
        monitoring.notify(
            db, settings, key="rotation-unconfirmed",
            severity=AlertSeverity.WARNING, kind="rotation-unconfirmed",
            title=(f"Rotation: {rotation.get('pushed')} Codes am Lock, aber 0 gerätebestätigt "
                   "— Gerät→Cloud-Sync gestört."),
            detail=("Alle heutigen Codes liegen am Schloss (Zutritt funktioniert), aber das Gerät "
                    "meldet keine Bestätigung zurück. Cloud-/WLAN-Anbindung des Nuki prüfen, bevor "
                    "sie ganz abreißt."),
            payload={"pushed": rotation.get("pushed"), "materialised": rotation.get("materialised"),
                     "tombstones": rotation.get("tombstones")},
            cooldown_secs=6 * 60 * 60,
        )
    except Exception:
        logger.exception("_alert_rotation_unconfirmed: failed to record alert")


# ── Worker-Einstieg: ersetzt provision_due_codes ──────────────────
def run_timewindow_cycle(db, settings) -> dict:
    """Voller Zeitfenster-Cycle: Rotation + Zuweisung/Zustellung fälliger Buchungen.

    Ersetzt ``provision_due_codes``. Baut effektive Nuki-/SMTP-Config aus der App
    (DB-Overrides über Env) und respektiert DRY-RUN.
    """
    from ..enums import AccessCodeStatus
    from ..notifications import EmailService
    from ..nuki_client import NukiClient
    from ..services.settings import get_effective_nuki_config, get_effective_smtp_config

    nuki_cfg = get_effective_nuki_config(db, settings)
    effective = settings.model_copy(update=nuki_cfg)
    dry_run = bool(nuki_cfg["nuki_dry_run"])
    smartlock_id = int(nuki_cfg["nuki_smartlock_id"] or 0)
    nuki = NukiClient(effective)
    email_service = EmailService(settings, get_effective_smtp_config(db, settings))

    day = to_berlin_tz(now_utc(), settings.timezone).date()  # Berlin-Lokaldatum, nicht UTC

    def _mark_handled(window_id: int, *, pin: str | None = None, delivered: bool = False) -> None:
        if pin is None:
            # No-code (business hours): flip out of the SCHEDULED "due" set.
            with db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE access_windows SET status='active' WHERE id=%s AND status='scheduled'",
                        (window_id,),
                    )
                conn.commit()
            return
        status = AccessCodeStatus.EMAILED if delivered else AccessCodeStatus.PROVISIONED
        code_id = db.store_access_code(
            access_window_id=window_id, raw_code=pin, nuki_auth_id=None,
            status=status, expires_at=day + timedelta(days=1),
        )
        if delivered:
            db.mark_code_emailed(code_id)

    try:
        rotation = rotate_daily(db, nuki=nuki, smartlock_id=smartlock_id, day=day, dry_run=dry_run)
        # Aggregate early-warning: the rotation pushed codes to the lock but NONE are
        # device-confirmed → the device→cloud link is degraded. Access still works
        # (delivery keys off presence), and this fires even on days with no bookings, so
        # the operator sees the degradation before it becomes a hard problem. Deduped 6 h.
        if (not dry_run and not rotation.get("skipped")
                and rotation.get("pushed") and rotation.get("materialised") == 0):
            _alert_rotation_unconfirmed(db, settings, rotation)
        due = db.due_access_windows(now_utc())
        assigned = no_code = delivered = blocked = 0
        for window in due:
            r = assign_and_deliver(
                db, window=window, email_service=email_service,
                smartlock_id=smartlock_id, day=day, tz_name=settings.timezone,
                mark_handled=_mark_handled, nuki=nuki, settings=settings,
            )
            if r.get("no_code"):
                no_code += 1
            elif r.get("verified") is False:
                blocked += 1  # fail closed: not materialised, no dispatch
            elif r.get("assigned"):
                assigned += 1
                delivered += int(bool(r.get("delivered")))
    finally:
        nuki.close()

    result = {
        "rotation": rotation,
        "due_windows": len(due),
        "assigned": assigned,
        "no_code": no_code,
        "delivered": delivered,
        "blocked": blocked,
        "pushed": rotation.get("pushed", 0),
        "dry_run": dry_run,
    }
    logger.info(
        "timewindow cycle: slots=%s due=%s assigned=%s no_code=%s delivered=%s blocked=%s pushed=%s (dry_run=%s)",
        rotation.get("slots"), len(due), assigned, no_code, delivered, blocked,
        rotation.get("pushed", 0), dry_run,
    )
    return result
