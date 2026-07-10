"""Zentrale Geschäftszeiten-Definition — Randfälle (Mo/Sa-Grenzen, Sonntag).

Prüft, dass PIN-Routing (``pin_pool.needs_keypad_code`` / ``datetime_utils.is_open``)
und Auto-Lock (``datetime_utils.is_within_business_hours`` über die abgeleiteten
``DEFAULT_BUSINESS_HOURS``) an denselben Grenzen dieselbe Entscheidung treffen:
Mo–Fr 08:00–21:00, Sa 08:00–15:00, So geschlossen. Kein DB/Netz/API.
"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from nuki_integration import datetime_utils as du
from nuki_integration.timewindow import pin_pool

BERLIN = ZoneInfo("Europe/Berlin")

# 2026-07-06 = Montag, 2026-07-11 = Samstag, 2026-07-12 = Sonntag.
MON = (2026, 7, 6)
SAT = (2026, 7, 11)
SUN = (2026, 7, 12)


def _dt(day, hh, mm):
    y, m, d = day
    return datetime(y, m, d, hh, mm, tzinfo=BERLIN)


class MinuteGranularNowTests(unittest.TestCase):
    """``is_within_business_hours_now`` — minutengenau, zentrale Quelle (Auto-Lock)."""

    def test_monday_boundaries(self):
        self.assertFalse(du.is_within_business_hours_now(_dt(MON, 7, 59)))  # 07:59 zu
        self.assertTrue(du.is_within_business_hours_now(_dt(MON, 8, 0)))    # 08:00 offen
        self.assertTrue(du.is_within_business_hours_now(_dt(MON, 20, 59)))  # 20:59 offen
        self.assertFalse(du.is_within_business_hours_now(_dt(MON, 21, 0)))  # 21:00 zu

    def test_saturday_boundaries(self):
        self.assertFalse(du.is_within_business_hours_now(_dt(SAT, 7, 59)))  # 07:59 zu
        self.assertTrue(du.is_within_business_hours_now(_dt(SAT, 8, 0)))    # 08:00 offen
        self.assertTrue(du.is_within_business_hours_now(_dt(SAT, 14, 59)))  # 14:59 offen
        self.assertFalse(du.is_within_business_hours_now(_dt(SAT, 15, 0)))  # 15:00 zu

    def test_sunday_always_closed(self):
        for hh in (0, 8, 12, 14, 20, 23):
            self.assertFalse(du.is_within_business_hours_now(_dt(SUN, hh, 0)))


class AutoLockScheduleTests(unittest.TestCase):
    """Auto-Lock-Pfad: ``is_within_business_hours(DEFAULT_BUSINESS_HOURS, now)``.

    Muss dieselben Grenzen liefern wie die zentrale Definition — sonst würden
    Lock und PIN-Routing auseinanderlaufen.
    """

    def _open(self, day, hh, mm):
        return du.is_within_business_hours(du.DEFAULT_BUSINESS_HOURS, _dt(day, hh, mm))

    def test_monday_boundaries(self):
        self.assertFalse(self._open(MON, 7, 59))
        self.assertTrue(self._open(MON, 8, 0))
        self.assertTrue(self._open(MON, 20, 59))
        self.assertFalse(self._open(MON, 21, 0))

    def test_saturday_boundaries(self):
        self.assertFalse(self._open(SAT, 7, 59))
        self.assertTrue(self._open(SAT, 8, 0))
        self.assertTrue(self._open(SAT, 14, 59))
        self.assertFalse(self._open(SAT, 15, 0))

    def test_sunday_closed(self):
        self.assertFalse(self._open(SUN, 10, 0))

    def test_agrees_with_central_now(self):
        for day in (MON, SAT, SUN):
            for hh, mm in ((7, 59), (8, 0), (14, 59), (15, 0), (20, 59), (21, 0)):
                now = _dt(day, hh, mm)
                self.assertEqual(
                    du.is_within_business_hours(du.DEFAULT_BUSINESS_HOURS, now),
                    du.is_within_business_hours_now(now),
                    msg=f"divergence at {now:%a %H:%M}",
                )


class RoutingBoundaryTests(unittest.TestCase):
    """PIN-Routing-Grenzen (Stunden-Granularität): welcher Code-Typ greift?

    ``needs_keypad_code`` == True  → Off-Peak → einer der 96 Stundencodes + Auto-Lock.
    ``needs_keypad_code`` == False → Geschäftszeit → einer der 5 Fallback-Codes.
    """

    def test_monday_hours(self):
        self.assertTrue(pin_pool.needs_keypad_code(0, 7))    # 07:xx off-peak
        self.assertFalse(pin_pool.needs_keypad_code(0, 8))   # 08:xx Geschäftszeit
        self.assertFalse(pin_pool.needs_keypad_code(0, 20))  # 20:xx Geschäftszeit
        self.assertTrue(pin_pool.needs_keypad_code(0, 21))   # 21:xx off-peak

    def test_saturday_hours(self):
        self.assertTrue(pin_pool.needs_keypad_code(5, 7))    # 07:xx off-peak
        self.assertFalse(pin_pool.needs_keypad_code(5, 8))   # 08:xx Geschäftszeit
        self.assertFalse(pin_pool.needs_keypad_code(5, 14))  # 14:xx Geschäftszeit
        self.assertTrue(pin_pool.needs_keypad_code(5, 15))   # 15:xx off-peak, kein Fallback

    def test_sunday_all_offpeak(self):
        for hh in range(24):
            self.assertTrue(pin_pool.needs_keypad_code(6, hh))


if __name__ == "__main__":
    unittest.main()
