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

    def test_repair_update_passes_hex_auth_id_as_is(self):
        # Regression: Nuki auth ids are hex strings — int() crashed every repair
        # ("invalid literal for int() ... '6a607d439baa510030d97d90'"). The lock's
        # verify response carries that hex id, so it must be passed through as-is.
        nuki = _HexAuthNuki()
        r = ensure_code_materialised(nuki, **{**_KW, "auth_id": None})
        self.assertTrue(r["valid"])                 # repair succeeded (no int() crash)
        self.assertEqual(len(nuki.updates), 1)
        self.assertEqual(nuki.updates[0]["auth_id"], _HexAuthNuki.HEX)


class _HexAuthNuki:
    """verify returns a materialised-but-wrong-window auth with a HEX auth id
    (as the real Nuki API does); repair updates it in place → then valid."""
    HEX = "6a607d439baa510030d97d90"

    def __init__(self):
        self.updates = []
        self.creates = []
        self._covers = False

    def verify_code_for_window(self, code, *, weekday, hour):  # noqa: ARG002
        return {"materialised": True, "covers_window": self._covers,
                "valid": self._covers, "simulated": False,
                "auth_id": self.HEX, "update_date": None}

    def update_keypad_code(self, **k):
        self.updates.append(k); self._covers = True  # repair fixes the window

    def create_keypad_code(self, **k):
        self.creates.append(k); return self.HEX

    def close(self):
        pass


class _UnreachableNuki:
    """verify_code_for_window signals a transient device/API error (timeout)."""
    def __init__(self):
        self.updates = []
        self.creates = []

    def verify_code_for_window(self, code, *, weekday, hour):  # noqa: ARG002
        return {"materialised": False, "covers_window": False, "valid": False,
                "simulated": False, "auth_id": None, "update_date": None, "error": True}

    def update_keypad_code(self, **k):
        self.updates.append(k)

    def create_keypad_code(self, **k):
        self.creates.append(k); return 1

    def close(self):
        pass


class UnreachableTests(unittest.TestCase):
    def test_transient_error_is_unreachable_not_repaired(self):
        # A Nuki/WAN timeout must NOT be treated as a materialisation failure and
        # must NOT trigger a blind repair — the guardian skips + retries next cycle.
        nuki = _UnreachableNuki()
        r = ensure_code_materialised(nuki, **{**_KW, "auth_id": "abc123"})
        self.assertTrue(r["unreachable"])
        self.assertFalse(r["valid"])
        self.assertFalse(r["repaired"])
        self.assertEqual(nuki.updates, [])   # no repair attempted while unreachable
        self.assertEqual(nuki.creates, [])


if __name__ == "__main__":
    unittest.main()
