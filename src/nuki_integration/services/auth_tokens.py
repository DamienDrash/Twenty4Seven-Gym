"""JWT helpers for member-facing check-in and /checks sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from ..auth import decode_token, issue_token
from ..config import Settings


# ── Legacy /check-in tokens ───────────────────────────────────────

def issue_check_in_token(
    *,
    access_window_id: int,
    settings: Settings,
    ttl_seconds: int,
) -> str:
    return issue_token(
        subject=f"checkin:{access_window_id}",
        role="checkin",
        secret=settings.jwt_secret,
        ttl_seconds=ttl_seconds,
    )


def decode_check_in_token(*, token: str, settings: Settings) -> int:
    payload = decode_token(token, settings.jwt_secret)
    if payload.get("role") != "checkin":
        raise ValueError("Invalid check-in token role.")
    subject = str(payload.get("sub") or "")
    if not subject.startswith("checkin:"):
        raise ValueError("Invalid check-in token subject.")
    return int(subject.split(":", 1)[1])


def build_check_in_link(
    *,
    access_window_id: int,
    ends_at: datetime,
    settings: Settings,
) -> str:
    ttl_seconds = max(int((ends_at - datetime.now(UTC)).total_seconds()) + 86400, 3600)
    token = issue_check_in_token(
        access_window_id=access_window_id,
        settings=settings,
        ttl_seconds=ttl_seconds,
    )
    return f"{settings.app_public_base_url.rstrip('/')}/check-in?token={token}"


# ── /checks session tokens ────────────────────────────────────────

def issue_checks_token(
    *,
    member_id: int,
    settings: Settings,
    ttl_seconds: int = 86400,
) -> str:
    """Issue a JWT for a member's /checks session (24 h default)."""
    return issue_token(
        subject=f"checks:{member_id}",
        role="checks",
        secret=settings.jwt_secret,
        ttl_seconds=ttl_seconds,
    )


def decode_checks_token(*, token: str, settings: Settings) -> int:
    """Decode a /checks session JWT and return the member_id."""
    payload = decode_token(token, settings.jwt_secret)
    if payload.get("role") != "checks":
        raise ValueError("Ungültiges Session-Token.")
    subject = str(payload.get("sub") or "")
    if not subject.startswith("checks:"):
        raise ValueError("Ungültiges Session-Token.")
    return int(subject.split(":", 1)[1])


def build_checks_link(
    *,
    checks_key: str | None = None,
    member_id: int | None = None,
    settings: Settings,
    ttl_seconds: int = 86400,
) -> str:
    if checks_key:
        return f"{settings.app_public_base_url.rstrip('/')}/checks?key={checks_key}"
    token = issue_checks_token(
        member_id=member_id,
        settings=settings,
        ttl_seconds=ttl_seconds,
    )
    return f"{settings.app_public_base_url.rstrip('/')}/checks?token={token}"
