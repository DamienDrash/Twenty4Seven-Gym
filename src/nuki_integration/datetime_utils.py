from __future__ import annotations
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo
from typing import Any


def now_utc() -> datetime:
    return datetime.now(UTC)


DEFAULT_BUSINESS_HOURS: dict[str, Any] = {
    "enabled": True,
    "schedules": [
        {"weekdays": [0, 1, 2, 3, 4], "open": "07:30", "close": "20:30"},
        {"weekdays": [5], "open": "07:30", "close": "14:30"},
    ],
}


def is_within_business_hours(business_hours: dict[str, Any], now_berlin: datetime) -> bool:
    """Return True if now_berlin falls within any configured business hours schedule.

    Weekdays: 0=Mon, 1=Tue, ..., 5=Sat, 6=Sun (matches Python's datetime.weekday()).
    """
    if not business_hours.get("enabled", True):
        return False
    weekday = now_berlin.weekday()
    current_time = now_berlin.time().replace(second=0, microsecond=0)
    for schedule in business_hours.get("schedules", []):
        if weekday not in schedule.get("weekdays", []):
            continue
        try:
            oh, om = map(int, schedule["open"].split(":"))
            ch, cm = map(int, schedule["close"].split(":"))
            if time(oh, om) <= current_time < time(ch, cm):
                return True
        except (KeyError, ValueError):
            continue
    return False


def to_berlin_tz(dt: datetime, tz_name: str = "Europe/Berlin") -> datetime:
    return dt.astimezone(ZoneInfo(tz_name))
