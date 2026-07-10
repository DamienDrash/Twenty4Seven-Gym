from __future__ import annotations
import logging
import time
from .config import get_settings
from .db import Database
from .logging_setup import configure_logging
from .datetime_utils import now_utc
from .services import cleanup_orphaned_nuki_codes, deprovision_expired_codes, lock_if_no_active_sessions, sync_magicline_bookings
from .timewindow.rotation import run_timewindow_cycle

def run_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    db = Database(settings.database_url)
    db.open()
    db.ensure_schema()
    from .timewindow import guardian
    heavy_interval = max(60, settings.magicline_sync_interval_minutes * 60)
    tick = min(heavy_interval, max(15, int(getattr(settings, "guardian_interval_seconds", 60))))
    last_heavy = None
    try:
        while True:
            # Wächter jeden Tick — verhindert Lockouts proaktiv (nur erste Stunde).
            if getattr(settings, "guardian_enabled", True):
                try:
                    guardian.guardian_tick(db, settings)
                except Exception:
                    logger.exception("guardian_tick failed")
            # Schwerer Block (Magicline-Sync + Rotation/Zustellung): nur alle N Minuten.
            mono = time.monotonic()
            if last_heavy is None or (mono - last_heavy) >= heavy_interval:
                now = now_utc()
                expired_db = db.expire_finished_windows(now)
                if expired_db > 0:
                    lock_if_no_active_sessions(db, settings)
                deleted_nuki = deprovision_expired_codes(db, settings)
                orphans_removed = cleanup_orphaned_nuki_codes(db, settings)
                sync_result = sync_magicline_bookings(db, settings)
                tw = run_timewindow_cycle(db, settings)
                logger.info(
                    "worker cycle: expired_db=%s deleted_nuki=%s orphans_removed=%s windows=%s "
                    "tw_slots=%s tw_assigned=%s tw_no_code=%s tw_delivered=%s tw_pushed=%s dry_run=%s",
                    expired_db, deleted_nuki, orphans_removed, sync_result["windows"],
                    tw["rotation"].get("slots"), tw["assigned"], tw["no_code"],
                    tw["delivered"], tw["pushed"], tw["dry_run"],
                )
                last_heavy = time.monotonic()
            time.sleep(tick)
    finally:
        db.close()

def main() -> None:
    run_forever()

if __name__ == "__main__":
    main()
