"""Self-service and admin password reset."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from ..config import Settings
from ..db import Database
from ..notifications import EmailService
from .email_builder import build_password_reset_email_html
from .settings import get_effective_smtp_config


def _hash_reset_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def request_password_reset(
    *, db: Database, settings: Settings, email: str,
) -> dict[str, bool]:
    user = db.get_user_by_email(email)
    if not user or not user["is_active"]:
        return {"accepted": True}  # silent success to prevent enumeration

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    db.create_password_reset_token(
        user_id=int(user["id"]),
        token_hash=_hash_reset_token(token),
        expires_at=expires_at,
    )
    smtp = get_effective_smtp_config(db, settings)
    email_service = EmailService(settings, smtp)
    reset_url = f"{settings.app_public_base_url.rstrip('/')}/reset-password?token={token}"
    email_service.send_password_reset_email(
        to_email=str(user["email"]),
        reset_url=reset_url,
        html_body=build_password_reset_email_html(db, settings, reset_url=reset_url),
    )
    return {"accepted": True}


def complete_password_reset(
    *, db: Database, token: str, password: str,
) -> dict[str, bool]:
    user = db.consume_password_reset_token(
        token_hash=_hash_reset_token(token),
        password=password,
        now=datetime.now(UTC),
    )
    if not user:
        raise ValueError("Invalid or expired password reset token.")
    return {"reset": True}
