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
        self._smtp = smtp_config or SMTPConfig(
            host=settings.smtp_host, port=settings.smtp_port,
            username=settings.smtp_username, password=settings.smtp_password,
            use_tls=settings.smtp_use_tls, from_email=settings.smtp_from_email,
        )

    def _send(self, message: EmailMessage) -> bool:
        if not self._smtp.host or not self._smtp.from_email:
            logger.warning("SMTP not configured")
            return False
        with smtplib.SMTP(self._smtp.host, self._smtp.port, timeout=20) as smtp:
            if self._smtp.use_tls:
                smtp.starttls()
            if self._smtp.username:
                smtp.login(self._smtp.username, self._smtp.password)
            smtp.send_message(message)
        return True

    def send_access_code(self, *, to_email: str, member_name: str, code: str,
                         valid_from: str, valid_until: str,
                         check_in_url: str | None = None,
                         checks_url: str | None = None,
                         html_body: str | None = None) -> bool:
        msg = EmailMessage()
        msg["Subject"] = "Dein Zugangscode für Freies Training"
        msg["From"] = self._smtp.from_email
        msg["To"] = to_email
        checks = f"\nCheck-In / Check-Out:\n{checks_url}\n" if checks_url else ""
        msg.set_content(
            f"Hallo {member_name},\n\ndein Zugangscode: {code}\n"
            f"Gültig von: {valid_from}\nGültig bis: {valid_until}\n{checks}"
        )
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        return self._send(msg)

    def send_test_email(self, *, to_email: str, html_body: str | None = None) -> bool:
        msg = EmailMessage()
        msg["Subject"] = "Test-E-Mail – Twenty4Seven Gym"
        msg["From"] = self._smtp.from_email
        msg["To"] = to_email
        msg.set_content("Dies ist eine Test-E-Mail. SMTP funktioniert.")
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        return self._send(msg)

    def send_password_reset_email(self, *, to_email: str, reset_url: str,
                                   html_body: str | None = None) -> bool:
        msg = EmailMessage()
        msg["Subject"] = "Passwort zurücksetzen – Twenty4Seven Gym"
        msg["From"] = self._smtp.from_email
        msg["To"] = to_email
        msg.set_content(f"Passwort zurücksetzen:\n{reset_url}\n\n60 Minuten gültig.")
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        return self._send(msg)

class TelegramService:
    def __init__(self, config: TelegramConfig) -> None:
        self._config = config

    def is_configured(self) -> bool:
        return bool(self._config.bot_token and self._config.chat_id)

    def send_message(self, *, text: str) -> bool:
        if not self.is_configured():
            logger.warning("Telegram not configured")
            return False
        r = httpx.post(
            f"https://api.telegram.org/bot{self._config.bot_token}/sendMessage",
            json={"chat_id": self._config.chat_id, "text": text}, timeout=20,
        )
        r.raise_for_status()
        return True
