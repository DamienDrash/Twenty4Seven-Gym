"""Pre-dispatch verification: coverage evaluation + fail-closed repair loop."""
import unittest

from nuki_integration.nuki_client import (
    _auth_covers_hour,
    evaluate_window_materialization,
)
from nuki_integration.services.nuki_verification import ensure_code_materialised

from support import FakeNuki


class CoverageTests(unittest.TestCase):
    def test_covers_when_hour_in_daily_window_and_weekday_bit_set(self):
        # Mo bit = 64; 22:00 slot: from=1320 until=1380.
        auth = {"allowedWeekDays": 64, "allowedFromTime": 1320, "allowedUntilTime": 1380}
        self.assertTrue(_auth_covers_hour(auth, weekday=0, hour=22))

    def test_not_covered_when_weekday_bit_missing(self):
        auth = {"allowedWeekDays": 64, "allowedFromTime": 1320, "allowedUntilTime": 1380}
        self.assertFalse(_auth_covers_hour(auth, weekday=1, hour=22))  # Di bit not set

    def test_not_covered_when_hour_outside_window(self):
        auth = {"allowedWeekDays": 127, "allowedFromTime": 1320, "allowedUntilTime": 1380}
        self.assertFalse(_auth_covers_hour(auth, weekday=0, hour=10))

    def test_no_daily_restriction_is_always_covered(self):
        self.assertTrue(_auth_covers_hour({"allowedWeekDays": 127}, weekday=0, hour=3))


class EvaluateWindowTests(unittest.TestCase):
    def test_valid_when_materialised_and_covered(self):
        auths = [{"type": 13, "code": 345678, "id": 7, "updateDate": "2026-07-06T03:00:00Z",
                  "allowedWeekDays": 127, "allowedFromTime": 180, "allowedUntilTime": 240}]
        r = evaluate_window_materialization(auths, "345678", weekday=0, hour=3)
        self.assertTrue(r["valid"])
        self.assertEqual(r["auth_id"], 7)

    def test_invalid_when_update_date_null(self):
        auths = [{"type": 13, "code": 345678, "id": 7, "updateDate": None,
                  "allowedWeekDays": 127, "allowedFromTime": 180, "allowedUntilTime": 240}]
        r = evaluate_window_materialization(auths, "345678", weekday=0, hour=3)
        self.assertFalse(r["valid"])
        self.assertFalse(r["materialised"])

    def test_invalid_when_window_wrong(self):
        auths = [{"type": 13, "code": 345678, "id": 7, "updateDate": "x",
                  "allowedWeekDays": 127, "allowedFromTime": 600, "allowedUntilTime": 660}]
        r = evaluate_window_materialization(auths, "345678", weekday=0, hour=3)
        self.assertTrue(r["materialised"])
        self.assertFalse(r["covers_window"])
        self.assertFalse(r["valid"])

    def test_absent_code(self):
        self.assertFalse(evaluate_window_materialization([], "345678", weekday=0, hour=3)["valid"])


_KW = dict(
    slot_name="og-h03-p0", code="345678", weekday=0, hour=3,
    weekday_mask=64, from_time=180, until_time=240,
    allowed_from="2026-07-06T00:00:00Z", allowed_until="2026-08-05T23:59:59Z",
)


class EnsureCodeMaterialisedTests(unittest.TestCase):
    def test_success_no_repair(self):
        nuki = FakeNuki(materialised=True, covers=True)
        r = ensure_code_materialised(nuki, **_KW)
        self.assertTrue(r["valid"])
        self.assertFalse(r["repaired"])
        self.assertEqual(r["attempts"], 1)
        self.assertEqual(nuki.creates, [])
        self.assertEqual(nuki.updates, [])

    def test_missing_code_is_repaired_via_create(self):
        nuki = FakeNuki(materialised=False, covers=False, repair_succeeds=True)
        r = ensure_code_materialised(nuki, **_KW)  # auth_id None → create path
        self.assertTrue(r["valid"])
        self.assertTrue(r["repaired"])
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(len(nuki.creates), 1)

    def test_wrong_window_is_repaired_via_update(self):
        # materialised but wrong window → known auth_id → update path.
        nuki = FakeNuki(materialised=True, covers=False, repair_succeeds=True)
        r = ensure_code_materialised(nuki, **{**_KW, "auth_id": 7})
        self.assertTrue(r["valid"])
        self.assertTrue(r["repaired"])
        self.assertEqual(len(nuki.updates), 1)
        self.assertEqual(len(nuki.creates), 0)

    def test_repair_failure_stays_invalid(self):
        nuki = FakeNuki(materialised=False, covers=False, repair_succeeds=False)
        r = ensure_code_materialised(nuki, **_KW)
        self.assertFalse(r["valid"])
        self.assertTrue(r["repaired"])


if __name__ == "__main__":
    unittest.main()
