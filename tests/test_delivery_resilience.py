"""Delivery resilience (fix/delivery-resilience-materialisation).

A keypad code that is PRESENT on the lock with the correct time window (the
create/push landed) is DELIVERABLE even when the device has not (yet) echoed a
confirmation (``updateDate``) back to the cloud. This stops a slow or degraded
device→cloud link from locking every member out. ``valid`` (device-confirmed)
remains a monitoring/health signal; ``deliverable`` (present + covers) is the new
dispatch gate. Genuine fail-closed is preserved for absent / wrong-window codes.
"""
import unittest

from nuki_integration.nuki_client import evaluate_window_materialization
from nuki_integration.services.nuki_verification import ensure_code_materialised

from support import FakeNuki

_KW = dict(
    slot_name="og-h03-p0", code="345678", weekday=0, hour=3,
    weekday_mask=64, from_time=180, until_time=240,
    allowed_from="2026-07-06T00:00:00Z", allowed_until="2026-08-05T23:59:59Z",
)


class EvaluateExistsTests(unittest.TestCase):
    def test_exists_true_even_when_unmaterialised(self):
        auths = [{"type": 13, "code": 345678, "id": 7, "updateDate": None,
                  "allowedWeekDays": 127, "allowedFromTime": 180, "allowedUntilTime": 240}]
        r = evaluate_window_materialization(auths, "345678", weekday=0, hour=3)
        self.assertTrue(r["exists"])
        self.assertFalse(r["materialised"])
        self.assertTrue(r["covers_window"])
        self.assertFalse(r["valid"])  # valid still requires the device confirmation

    def test_exists_false_when_absent(self):
        r = evaluate_window_materialization([], "345678", weekday=0, hour=3)
        self.assertFalse(r["exists"])
        self.assertFalse(r["valid"])


class DeliverableGateTests(unittest.TestCase):
    def test_present_and_covers_but_unconfirmed_is_deliverable_without_repair(self):
        # THE core case: code on the lock, correct window, NO updateDate (device→cloud lag).
        nuki = FakeNuki(materialised=False, covers=True, exists=True)
        r = ensure_code_materialised(nuki, **_KW)
        self.assertTrue(r["deliverable"])        # → dispatched (members are not locked out)
        self.assertFalse(r["valid"])             # but flagged unconfirmed for monitoring
        self.assertFalse(r["materialised"])
        self.assertFalse(r["repaired"])          # NO churny repair
        self.assertEqual(nuki.creates, [])
        self.assertEqual(nuki.updates, [])
        self.assertEqual(r["attempts"], 1)

    def test_confirmed_and_covers_is_deliverable_and_valid(self):
        nuki = FakeNuki(materialised=True, covers=True, exists=True)
        r = ensure_code_materialised(nuki, **_KW)
        self.assertTrue(r["deliverable"])
        self.assertTrue(r["valid"])
        self.assertFalse(r["repaired"])

    def test_absent_code_is_repaired_then_deliverable(self):
        nuki = FakeNuki(materialised=False, covers=False, exists=False, repair_succeeds=True)
        r = ensure_code_materialised(nuki, **_KW)  # auth_id None → create path
        self.assertTrue(r["deliverable"])
        self.assertTrue(r["repaired"])
        self.assertEqual(len(nuki.creates), 1)

    def test_absent_and_repair_fails_is_NOT_deliverable(self):
        # Genuine fail-closed: the code never made it onto the lock.
        nuki = FakeNuki(materialised=False, covers=False, exists=False, repair_succeeds=False)
        r = ensure_code_materialised(nuki, **_KW)
        self.assertFalse(r["deliverable"])
        self.assertFalse(r["valid"])
        self.assertTrue(r["repaired"])

    def test_wrong_window_is_repaired_not_shortcut_delivered(self):
        # exists but window wrong → must NOT deliver on "exists" alone; repair the window first.
        nuki = FakeNuki(materialised=True, covers=False, exists=True, repair_succeeds=True)
        r = ensure_code_materialised(nuki, **{**_KW, "auth_id": 7})
        self.assertTrue(r["deliverable"])        # deliverable only AFTER the window is fixed
        self.assertEqual(len(nuki.updates), 1)   # repair happened (update path)
        self.assertEqual(len(nuki.creates), 0)


if __name__ == "__main__":
    unittest.main()
