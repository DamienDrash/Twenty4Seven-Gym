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


def notify_email(
    *,
    db: Database,
    settings: Settings,
    severity: str,
    kind: str,
    message: str,
) -> bool:
    from ..notifications import EmailService
    from .settings import get_effective_smtp_config

    to = (settings.alert_email or settings.smtp_from_email
          or settings.bootstrap_admin_email or "").strip()
    if not to:
        logger.warning("No alert email recipient configured (ALERT_EMAIL/SMTP_FROM_EMAIL).")
        return False
    email = EmailService(settings, get_effective_smtp_config(db, settings))
    return email.send_alert(
        to_email=to,
        subject=f"[Twenty4Seven-Gym] {str(severity).upper()} {kind}",
        text=message,
    )


def create_operational_alert(
    *,
    db: Database,
    settings: Settings,
    severity: str,
    kind: str,
    message: str,
    payload: dict[str, Any] | None = None,
    send_telegram: bool = True,
    send_email: bool = True,
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
    if send_email and severity in {AlertSeverity.ERROR, AlertSeverity.WARNING}:
        try:
            notify_email(db=db, settings=settings, severity=severity, kind=kind, message=message)
        except Exception:
            logger.exception("Failed to send alert email kind=%s", kind)
