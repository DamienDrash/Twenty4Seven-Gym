"""Delivery gate + outage detector.

Two regimes, switched by ``require_device_confirmation``:

* **Default (True) — outage detector.** A code counts as DELIVERABLE only once the
  device has confirmed it (``updateDate`` present → truly on the keypad). A code that
  is present in the *cloud* auth list but not device-confirmed is withheld
  (``unconfirmed=True``, ``deliverable=False``): during a Cloud↔Lock freeze it never
  reached the physical keypad and would be a dead code (member lockout, 03.08.2026).
  Stable pre-confirmed codes (the og-bh fallbacks) survive an outage — they are
  ``valid`` and dispatch normally.
* **Legacy (False) — deliver on presence.** Present + covers is enough, even without a
  device confirmation. Retained for environments with a reliable push and a laggy echo.

Genuine fail-closed (absent / wrong-window / repair-failed) is preserved in both.
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


class OutageDetectorTests(unittest.TestCase):
    def test_present_but_unconfirmed_is_NOT_deliverable_by_default(self):
        # THE core case: code in the cloud, correct window, NO updateDate. During a
        # Cloud↔Lock freeze this code is not on the keypad → fail closed, do NOT dispatch.
        nuki = FakeNuki(materialised=False, covers=True, exists=True)
        r = ensure_code_materialised(nuki, **_KW)
        self.assertFalse(r["deliverable"])       # withheld — no dead code to the member
        self.assertTrue(r["unconfirmed"])        # flagged for the freeze alert
        self.assertFalse(r["valid"])
        self.assertFalse(r["materialised"])
        self.assertFalse(r["repaired"])          # NO churny repair on a frozen link
        self.assertEqual(nuki.creates, [])
        self.assertEqual(nuki.updates, [])
        self.assertEqual(r["attempts"], 1)

    def test_present_but_unconfirmed_IS_deliverable_in_legacy_mode(self):
        # Opt-out restores deliver-on-presence (present + covers, no confirmation).
        nuki = FakeNuki(materialised=False, covers=True, exists=True)
        r = ensure_code_materialised(nuki, **_KW, require_device_confirmation=False)
        self.assertTrue(r["deliverable"])
        self.assertFalse(r["unconfirmed"])
        self.assertFalse(r["valid"])
        self.assertEqual(nuki.creates, [])
        self.assertEqual(nuki.updates, [])

    def test_freeze_signal_passes_through(self):
        # The newest device confirmation across the lock is surfaced for alert enrichment.
        nuki = FakeNuki(materialised=False, covers=True, exists=True,
                        link_last_confirmed="2026-07-30T08:16:00.000Z")
        r = ensure_code_materialised(nuki, **_KW)
        self.assertTrue(r["unconfirmed"])
        self.assertEqual(r["link_last_confirmed"], "2026-07-30T08:16:00.000Z")


class DeliverableGateTests(unittest.TestCase):
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
