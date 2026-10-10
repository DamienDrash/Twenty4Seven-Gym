from __future__ import annotations
import logging
import time
from .config import get_settings
from .db import Database
from .logging_setup import configure_logging
from .datetime_utils import now_utc
from .services import cleanup_orphaned_nuki_codes, deprovision_expired_codes, lock_if_no_active_sessions, sync_magicline_bookings
from .services.nuki_guardian import run_guardian_cycle
from .services import deadman, monitoring
from .timewindow.rotation import run_timewindow_cycle
from .exceptions import MagiclineApiError

# Erst nach so vielen Zyklen in Folge ohne Magicline-Sync meldet der
# Dead-Man's-Switch "fail" (bei 5-Minuten-Takt also nach ~15 min). Ein einzelner
# Aussetzer ist kein Ausfall: Codes für bereits synchronisierte Buchungen werden
# trotzdem zugestellt, und die Tür-Überwachung läuft weiter.
SYNC_FAIL_ALERT_AFTER = 3

def run_cycle(db, settings, logger) -> dict:
    """One worker tick.

    Order matters: Magicline bookings are SYNCED first, then time-window
    processing runs in the SAME tick — so any newly-due access windows/codes are
    assigned + dispatched immediately, not on a later tick. With the 5-minute
    interval this bounds post-sync dispatch latency to a single short cycle.
    """
    now = now_utc()
    expired_db = db.expire_finished_windows(now)
    if expired_db > 0:
        lock_if_no_active_sessions(db, settings)
    deleted_nuki = deprovision_expired_codes(db, settings)
    orphans_removed = cleanup_orphaned_nuki_codes(db, settings)
    # Magicline-Ausfall darf den Rest des Zyklus nicht mitreißen (10.10.2026: ein
    # einzelnes "Network is unreachable" ließ Zustellung, Wächter und die
    # Keypad-Überwachung einen ganzen Zyklus ausfallen). Bereits synchronisierte
    # Buchungen stehen in der DB und werden unten normal bedient.
    try:
        sync_result = sync_magicline_bookings(db, settings)
    except MagiclineApiError as exc:
        logger.warning("run_cycle: Magicline sync failed, continuing with DB state: %s", exc)
        sync_result = {"members": 0, "bookings": 0, "windows": 0, "error": str(exc)[:300]}
    # M2b: per-booking provisioning replaced by the time-window PIN model.
    # Runs right after the sync so freshly-synced due windows dispatch now.
    tw = run_timewindow_cycle(db, settings)
    # Wächter-Fallback: reconciled relevante Buchungen auch ohne Webhook.
    # Rate-limit-sicher über denselben DB-Slot wie der Webhook-Trigger.
    guardian = run_guardian_cycle(db, settings)
    # Proactive monitoring: heartbeat + periodic fallback detectors (overdue
    # dispatch, keypad-rejection poll) so a missed webhook cannot fail silently.
    try:
        monitoring.run_worker_monitoring(db, settings)
    except Exception:
        logger.exception("run_cycle: monitoring failed")
    logger.info(
        "worker cycle: expired_db=%s deleted_nuki=%s orphans_removed=%s windows=%s "
        "tw_slots=%s tw_assigned=%s tw_no_code=%s tw_delivered=%s tw_blocked=%s tw_pushed=%s "
        "guardian_reconciled=%s guardian_repaired=%s dry_run=%s",
        expired_db, deleted_nuki, orphans_removed, sync_result["windows"],
        tw["rotation"].get("slots"), tw["assigned"], tw["no_code"],
        tw["delivered"], tw["blocked"], tw["pushed"],
        guardian.get("reconciled"), guardian.get("repaired", 0), tw["dry_run"],
    )
    return {"expired_db": expired_db, "deleted_nuki": deleted_nuki,
            "orphans_removed": orphans_removed, "sync": sync_result,
            "tw": tw, "guardian": guardian}


def _deadman_summary(result: dict) -> str:
    """Kurzfassung des Zyklus für die healthchecks.io-Historie (dort einsehbar,
    wenn man nach einem Alarm wissen will, was der letzte gute Lauf getan hat)."""
    tw = result.get("tw", {}) or {}
    return (f"windows={result.get('sync', {}).get('windows')} "
            f"assigned={tw.get('assigned')} delivered={tw.get('delivered')} "
            f"blocked={tw.get('blocked')}"
            + (" sync_error=1" if (result.get("sync") or {}).get("error") else ""))


def run_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    db = Database(settings.database_url)
    db.open()
    db.ensure_schema()
    sync_failures = 0
    try:
        while True:
            # A single transient failure (e.g. a Nuki API TimeoutError on the degraded
            # cloud link) must NEVER crash the worker into a restart loop — it just skips
            # this cycle and retries on the next. Only truly fatal signals propagate.
            try:
                deadman.ping(settings, suffix="start")
                result = run_cycle(db, settings, logger)
                # Erst NACH einem sauber beendeten Zyklus melden. Ein Ping am
                # Schleifenanfang würde auch dann grün melden, wenn jeder Zyklus
                # in der Mitte abbricht — und damit genau den Fall verschleiern,
                # für den der Schalter da ist.
                sync_error = (result.get("sync") or {}).get("error")
                sync_failures = sync_failures + 1 if sync_error else 0
                if sync_failures < SYNC_FAIL_ALERT_AFTER:
                    deadman.ping(settings, payload=_deadman_summary(result))
                else:
                    deadman.ping(settings, suffix="fail",
                                 payload=f"Magicline-Sync {sync_failures}x in Folge fehlgeschlagen: {sync_error}")
            except Exception:
                logger.exception("worker cycle failed — continuing to next cycle")
                deadman.ping(settings, suffix="fail",
                             payload="worker cycle raised — siehe Container-Log")
            time.sleep(settings.magicline_sync_interval_minutes * 60)
    finally:
        db.close()

def main() -> None:
    run_forever()

if __name__ == "__main__":
    main()
