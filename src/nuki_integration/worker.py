from __future__ import annotations

import logging
import time

from .config import get_settings
from .db import Database
from .logging_setup import configure_logging
from .datetime_utils import now_utc
from .services import (
    deprovision_expired_codes,
    provision_due_codes,
    sync_magicline_bookings,
)


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
            # 1. Expire old windows and codes in DB
            expired_db_count = db.expire_finished_windows(now)
            
            # 2. Delete expired codes from Nuki
            deleted_nuki_count = deprovision_expired_codes(db, settings)
            
            # 3. Sync from Magicline
            sync_result = sync_magicline_bookings(db, settings)
            
            # 4. Create new Nuki codes
            provisioned = provision_due_codes(db, settings)
            
            logger.info(
                "worker cycle complete: expired_db=%s deleted_nuki=%s windows=%s provisioned=%s",
                expired_db_count,
                deleted_nuki_count,
                sync_result["windows"],
                provisioned,
            )
            time.sleep(settings.magicline_sync_interval_minutes * 60)
    finally:
        db.close()


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
