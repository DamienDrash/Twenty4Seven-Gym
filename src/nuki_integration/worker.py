from __future__ import annotations

import logging
import time

from .config import get_settings
from .db import Database
from .logging_setup import configure_logging
from .services import provision_due_codes, sync_magicline_bookings


def run_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    db = Database(settings.database_url)
    db.open()
    db.ensure_schema()
    try:
        while True:
            sync_result = sync_magicline_bookings(db, settings)
            provisioned = provision_due_codes(db, settings)
            logger.info(
                "worker cycle complete members=%s bookings=%s windows=%s provisioned=%s",
                sync_result["members"],
                sync_result["bookings"],
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
