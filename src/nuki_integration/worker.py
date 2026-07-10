from __future__ import annotations
import logging
import time
from .config import get_settings
from .db import Database
from .logging_setup import configure_logging
from .datetime_utils import now_utc
from .services import cleanup_orphaned_nuki_codes, deprovision_expired_codes, lock_if_no_active_sessions, sync_magicline_bookings
from .services.nuki_guardian import run_guardian_cycle
from .timewindow.rotation import run_timewindow_cycle

def run_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    db = Database(settings.database_url)
    db.open()
    db.ensure_schema()
    try:
        while True:
            now = now_utc()
            expired_db = db.expire_finished_windows(now)
            if expired_db > 0:
                lock_if_no_active_sessions(db, settings)
            deleted_nuki = deprovision_expired_codes(db, settings)
            orphans_removed = cleanup_orphaned_nuki_codes(db, settings)
            sync_result = sync_magicline_bookings(db, settings)
            # M2b: per-booking provisioning replaced by the time-window PIN model.
            tw = run_timewindow_cycle(db, settings)
            # Wächter-Fallback: reconciled relevante Buchungen auch ohne Webhook.
            # Rate-limit-sicher über denselben DB-Slot wie der Webhook-Trigger.
            guardian = run_guardian_cycle(db, settings)
            logger.info(
                "worker cycle: expired_db=%s deleted_nuki=%s orphans_removed=%s windows=%s "
                "tw_slots=%s tw_assigned=%s tw_no_code=%s tw_delivered=%s tw_blocked=%s tw_pushed=%s "
                "guardian_reconciled=%s guardian_repaired=%s dry_run=%s",
                expired_db, deleted_nuki, orphans_removed, sync_result["windows"],
                tw["rotation"].get("slots"), tw["assigned"], tw["no_code"],
                tw["delivered"], tw["blocked"], tw["pushed"],
                guardian.get("reconciled"), guardian.get("repaired", 0), tw["dry_run"],
            )
            time.sleep(settings.magicline_sync_interval_minutes * 60)
    finally:
        db.close()

def main() -> None:
    run_forever()

if __name__ == "__main__":
    main()
