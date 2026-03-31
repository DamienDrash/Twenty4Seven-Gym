"""Create alerts and optionally forward them to Telegram."""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from ..db import Database
from ..enums import AlertSeverity
from ..notifications import TelegramService
from .settings import get_effective_telegram_config

logger = logging.getLogger(__name__)


def notify_telegram(
    *,
    db: Database,
    settings: Settings,
    text: str,
) -> bool:
    telegram = TelegramService(get_effective_telegram_config(db, settings))
    return telegram.send_message(text=text)


def create_operational_alert(
    *,
    db: Database,
    settings: Settings,
    severity: str,
    kind: str,
    message: str,
    payload: dict[str, Any] | None = None,
    send_telegram: bool = True,
) -> None:
    db.create_alert(severity=severity, kind=kind, message=message, payload=payload)
    if send_telegram and severity in {AlertSeverity.ERROR, AlertSeverity.WARNING}:
        try:
            notify_telegram(
                db=db,
                settings=settings,
                text=f"[Twenty4Seven-Gym] {severity.upper()} {kind}\n{message}",
            )
        except Exception:
            logger.exception("Failed to send Telegram alert kind=%s", kind)
