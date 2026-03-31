"""Date formatting and locale helpers shared across service modules."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_DE_MONTHS = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def to_berlin(dt: datetime, tz_name: str = "Europe/Berlin") -> datetime:
    """Convert a datetime to the configured timezone."""
    return dt.astimezone(ZoneInfo(tz_name))


def fmt_dt_de(dt: datetime) -> str:
    """Format a datetime as '24. März 2026, 10:00 Uhr'."""
    return f"{dt.day}. {_DE_MONTHS[dt.month - 1]} {dt.year}, {dt.strftime('%H:%M')} Uhr"


def member_display_name(record: dict[str, object]) -> str:
    """Build a human-readable name from a member or window dict."""
    return (
        " ".join(
            str(part)
            for part in [record.get("first_name"), record.get("last_name")]
            if part
        ).strip()
        or "Mitglied"
    )
