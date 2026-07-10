"""Tests für den Wächter — reine Verifikationslogik (kein Netzwerk)."""
from __future__ import annotations

from datetime import date

from nuki_integration.timewindow import guardian, pin_pool


def _auth(code, *, mask=127, ft=420, ut=480, mat=True, enabled=True,
          fd="2026-07-10T00:00:00.000Z", ud="2026-08-09T23:59:59.000Z"):
    return {
        "type": 13, "code": code, "enabled": enabled,
        "allowedWeekDays": mask, "allowedFromTime": ft, "allowedUntilTime": ut,
        "allowedFromDate": fd, "allowedUntilDate": ud,
        "updateDate": "2026-07-10T19:26:25.845Z" if mat else None,
    }


D = date(2026, 7, 13)  # Montag


def test_ok():
    auths = [_auth(123456)]
    assert guardian.evaluate(auths, code=123456, weekday=0, start_minute=440, on_date=D)["ok"]


def test_not_found():
    assert guardian.evaluate([], code=123456, weekday=0, start_minute=440, on_date=D)["reason"] == "not_found"


def test_not_materialised():
    auths = [_auth(123456, mat=False)]
    assert guardian.evaluate(auths, code=123456, weekday=0, start_minute=440, on_date=D)["reason"] == "not_materialised"


def test_disabled():
    auths = [_auth(123456, enabled=False)]
    assert guardian.evaluate(auths, code=123456, weekday=0, start_minute=440, on_date=D)["reason"] == "disabled"


def test_weekday_excluded():
    # Maske 3 = nur Sa(2)+So(1); Montag (Bit 64) nicht enthalten
    auths = [_auth(123456, mask=3, ft=960, ut=1020)]
    assert guardian.evaluate(auths, code=123456, weekday=0, start_minute=970, on_date=D)["reason"] == "weekday_excluded"


def test_time_window():
    # Fenster 07:00–08:00, Buchung 08:30 (510) außerhalb
    auths = [_auth(123456, ft=420, ut=480)]
    assert guardian.evaluate(auths, code=123456, weekday=0, start_minute=510, on_date=D)["reason"] == "time_window"


def test_date_after():
    auths = [_auth(123456, ud="2026-07-11T23:59:59.000Z")]
    assert guardian.evaluate(auths, code=123456, weekday=0, start_minute=440, on_date=D)["reason"] == "date_after"


def test_slot_params_offpeak_and_fallback():
    ft, ut, mask = guardian.slot_params(7)
    assert (ft, ut) == (420, 480)
    ft2, ut2, mask2 = guardian.slot_params(pin_pool.FALLBACK_HOUR)
    assert (ft2, ut2, mask2) == (pin_pool.FALLBACK_FROM_MIN, pin_pool.FALLBACK_UNTIL_MIN, 127)


def test_slot_name_and_kind():
    assert guardian.slot_name(7, 2) == "og-h07-p2"
    assert guardian.slot_name(pin_pool.FALLBACK_HOUR, 3) == "og-bh-p3"
    # Montag 06:00 = off-peak → echte Stunde; Montag 10:00 = Geschäftszeit → Fallback
    assert guardian.slot_kind(0, 6) == 6
    assert guardian.slot_kind(0, 10) == pin_pool.FALLBACK_HOUR
