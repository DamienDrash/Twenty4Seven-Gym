from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(UTC)


def berlin_today(tz_name: str = "Europe/Berlin") -> date:
    """Aktuelles Kalenderdatum in Lokalzeit (Standard Berlin), NICHT in UTC.

    Rotations- und Wächter-PIN-Lookups sind auf das Berliner Kalenderdatum
    geschlüsselt; nahe UTC-Mitternacht weicht ``now_utc().date()`` davon ab.
    """
    return now_utc().astimezone(ZoneInfo(tz_name)).date()


# ── Zentrale Geschäftszeiten-Definition (Single Source of Truth) ──────────
# Mo–Fr 08:00–21:00, Sa 08:00–15:00, So geschlossen.  (offen_von_h, offen_bis_h)
# Wochentage 0=Mo .. 6=So (matcht datetime.weekday()).
# Diese EINE Definition speist sowohl das PIN-Routing (``timewindow.pin_pool``)
# als auch den Auto-Lock (``services.access``), damit beide nie auseinanderlaufen.
BUSINESS_HOURS: dict[int, tuple[int, int] | None] = {
    0: (8, 21), 1: (8, 21), 2: (8, 21), 3: (8, 21), 4: (8, 21),  # Mo–Fr
    5: (8, 15),                                                   # Sa
    6: None,                                                      # So geschlossen
}


def is_open(weekday: int, hour: int) -> bool:
    """True iff die ganze Stunde ``hour`` an ``weekday`` in die Geschäftszeiten fällt.

    Stunden-Granularität (für die Off-Peak-Bucket-Berechnung). Da alle Grenzen auf
    vollen Stunden liegen (08:00/21:00/15:00), stimmt dies mit der minutengenauen
    Prüfung an den Grenzen überein.
    """
    span = BUSINESS_HOURS[weekday]
    if span is None:
        return False
    return span[0] <= hour < span[1]


def is_within_business_hours_now(now_berlin: datetime) -> bool:
    """Minutengenaue Geschäftszeiten-Prüfung gegen die zentrale ``BUSINESS_HOURS``.

    Grenzen sind ganzstündig: 07:59 → zu, 08:00 → offen, 20:59 → offen,
    21:00 → zu (Sa 14:59 → offen, 15:00 → zu). Sonntag immer zu.
    """
    span = BUSINESS_HOURS[now_berlin.weekday()]
    if span is None:
        return False
    minutes = now_berlin.hour * 60 + now_berlin.minute
    return span[0] * 60 <= minutes < span[1] * 60


def _business_hours_schedule() -> dict[str, Any]:
    """Schedule-Dict-Form der zentralen ``BUSINESS_HOURS`` (für den DB-Override-Merge
    des Auto-Locks). Bleibt so automatisch deckungsgleich mit dem PIN-Routing."""
    schedules = []
    for weekday, span in BUSINESS_HOURS.items():
        if span is None:
            continue
        schedules.append(
            {"weekdays": [weekday], "open": f"{span[0]:02d}:00", "close": f"{span[1]:02d}:00"}
        )
    return {"enabled": True, "schedules": schedules}


# Auto-Lock-Default = zentrale Definition (kann per DB-System-Setting überschrieben
# werden — siehe ``services.settings.get_effective_business_hours``).
DEFAULT_BUSINESS_HOURS: dict[str, Any] = _business_hours_schedule()


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
