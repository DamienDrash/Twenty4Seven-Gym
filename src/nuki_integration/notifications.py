from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    from_email: str


@dataclass(slots=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


class EmailService:
    def __init__(self, settings: Settings, smtp_config: SMTPConfig | None = None) -> None:
        self._settings = settings
        self._smtp_config = smtp_config or SMTPConfig(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            use_tls=settings.smtp_use_tls,
            from_email=settings.smtp_from_email,
        )

    def send_access_code(
        self,
        *,
        to_email: str,
        member_name: str,
        code: str,
        valid_from: str,
        valid_until: str,
        check_in_url: str | None = None,
    ) -> bool:
        if not self._smtp_config.host or not self._smtp_config.from_email:
            logger.warning("SMTP not configured, skipping access email for %s", to_email)
            return False

        message = EmailMessage()
        message["Subject"] = "Dein Zugangscode für Freies Training"
        message["From"] = self._smtp_config.from_email
        message["To"] = to_email
        check_in_text = (
            f"\nVor deinem Training bitte Hausregeln lesen und Check-in abschließen:\n"
            f"{check_in_url}\n"
            if check_in_url
            else ""
        )
        message.set_content(
            f"Hallo {member_name},\n\n"
            f"dein Zugangscode lautet: {code}\n"
            f"Gültig von: {valid_from}\n"
            f"Gültig bis: {valid_until}\n"
            f"{check_in_text}"
        )

        with smtplib.SMTP(self._smtp_config.host, self._smtp_config.port, timeout=20) as smtp:
            if self._smtp_config.use_tls:
                smtp.starttls()
            if self._smtp_config.username:
                smtp.login(self._smtp_config.username, self._smtp_config.password)
            smtp.send_message(message)
        return True

    def send_test_email(self, *, to_email: str) -> bool:
        if not self._smtp_config.host or not self._smtp_config.from_email:
            logger.warning("SMTP not configured, skipping test email for %s", to_email)
            return False

        message = EmailMessage()
        message["Subject"] = "OpenGym SMTP Test"
        message["From"] = self._smtp_config.from_email
        message["To"] = to_email
        message.set_content(
            "Dies ist eine Test-E-Mail von OpenGym.\n\n"
            "Wenn du diese Nachricht erhältst, funktioniert der SMTP-Versand."
        )

        with smtplib.SMTP(self._smtp_config.host, self._smtp_config.port, timeout=20) as smtp:
            if self._smtp_config.use_tls:
                smtp.starttls()
            if self._smtp_config.username:
                smtp.login(self._smtp_config.username, self._smtp_config.password)
            smtp.send_message(message)
        return True

    def send_password_reset_email(self, *, to_email: str, reset_url: str) -> bool:
        if not self._smtp_config.host or not self._smtp_config.from_email:
            logger.warning("SMTP not configured, skipping password reset email for %s", to_email)
            return False

        message = EmailMessage()
        message["Subject"] = "OpenGym Passwort zuruecksetzen"
        message["From"] = self._smtp_config.from_email
        message["To"] = to_email
        message.set_content(
            "Du hast ein neues Passwort fuer OpenGym angefordert.\n\n"
            f"Nutze diesen Link, um dein Passwort zu setzen:\n{reset_url}\n\n"
            "Der Link ist 60 Minuten gueltig."
        )

        with smtplib.SMTP(self._smtp_config.host, self._smtp_config.port, timeout=20) as smtp:
            if self._smtp_config.use_tls:
                smtp.starttls()
            if self._smtp_config.username:
                smtp.login(self._smtp_config.username, self._smtp_config.password)
            smtp.send_message(message)
        return True


class TelegramService:
    def __init__(self, config: TelegramConfig) -> None:
        self._config = config

    def is_configured(self) -> bool:
        return bool(self._config.bot_token and self._config.chat_id)

    def send_message(self, *, text: str) -> bool:
        if not self.is_configured():
            logger.warning("Telegram not configured, skipping admin message")
            return False
        response = httpx.post(
            f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage",
            json={"chat_id": self._config.chat_id, "text": text},
            timeout=20,
        )
        response.raise_for_status()
        return True
