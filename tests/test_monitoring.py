"""Unit tests for the proactive monitoring detectors (pure decision logic + redaction).

DB-backed dedup/heartbeat/overdue-query paths are exercised by the live production
integration checks in the deploy step; here we lock the pure logic that must never
regress: staleness, keypad classification, severity, and secret/code redaction.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from nuki_integration.services import monitoring as m
from nuki_integration.enums import AlertSeverity

UTC = timezone.utc


class RedactionTests(unittest.TestCase):
    def test_safe_payload_drops_secrets_masks_codes_keeps_context(self):
        out = m._safe_payload({
            "code": "345678", "pin": "112345", "api_key": "abc", "token": "t",
            "password": "p", "code_last4": "5678", "booking_count": 3,
            "member_id": 42, "note": "code 345678 rejected", "slot_name": "og-h03-p0",
        })
        # secrets dropped entirely
        for k in ("code", "pin", "api_key", "token", "password"):
            self.assertNotIn(k, out)
        # safe context kept
        self.assertEqual(out["member_id"], 42)
        self.assertEqual(out["code_last4"], "5678")   # *_last4 allowed
        self.assertEqual(out["booking_count"], 3)     # *_count allowed
        self.assertEqual(out["slot_name"], "og-h03-p0")
        # any 6-digit code inside free text is masked
        self.assertEqual(out["note"], "code ****** rejected")

    def test_safe_text_masks_codes(self):
        self.assertEqual(m._safe_text("your code is 918273 today"), "your code is ****** today")


class HeartbeatStalenessTests(unittest.TestCase):
    def test_fresh_not_stale(self):
        now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
        self.assertFalse(m.is_stale(now - timedelta(seconds=120), now, interval_secs=300))

    def test_old_is_stale(self):
        now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
        # 3*300 = 900s threshold; 1000s old -> stale
        self.assertTrue(m.is_stale(now - timedelta(seconds=1000), now, interval_secs=300))

    def test_floor_applies_for_small_intervals(self):
        now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
        # interval 10s -> 3*10=30 but floor 180 -> not stale at 100s
        self.assertFalse(m.is_stale(now - timedelta(seconds=100), now, interval_secs=10))
        self.assertTrue(m.is_stale(now - timedelta(seconds=200), now, interval_secs=10))


class OverdueSeverityTests(unittest.TestCase):
    def test_imminent_is_error_else_warning(self):
        self.assertEqual(m.overdue_severity(10), AlertSeverity.ERROR)
        self.assertEqual(m.overdue_severity(60), AlertSeverity.ERROR)
        self.assertEqual(m.overdue_severity(61), AlertSeverity.WARNING)
        self.assertEqual(m.overdue_severity(600), AlertSeverity.WARNING)


class KeypadClassificationTests(unittest.TestCase):
    def test_door_sensor_is_not_keypad(self):
        cat, ctx = m.classify_keypad_event(
            {"action": 241, "trigger": 0, "source": 0, "state": 0, "name": "Door Sensor"})
        self.assertIsNone(cat)

    def test_og_named_entry_is_keypad_accepted(self):
        cat, ctx = m.classify_keypad_event(
            {"action": 1, "trigger": 0, "state": 0, "name": "og-h03-p0", "id": "x1"})
        self.assertEqual(cat, "keypad-accepted")
        self.assertEqual(ctx["auth_name"], "og-h03-p0")

    def test_reject_state_flags_rejection(self):
        # KEYPAD_REJECT_STATES is empty until a real rejection signature is confirmed;
        # patch it to prove the mechanism fires on a known reject state.
        with mock.patch.object(m, "KEYPAD_REJECT_STATES", frozenset({99})):
            cat, ctx = m.classify_keypad_event(
                {"action": 0, "trigger": 255, "state": 99, "name": "og-bh-p1", "id": "z9"})
        self.assertEqual(cat, "keypad-rejected")

    def test_keypad_by_trigger_without_name(self):
        cat, _ = m.classify_keypad_event({"trigger": m.KEYPAD_TRIGGER, "state": 0, "name": ""})
        self.assertEqual(cat, "keypad-accepted")


if __name__ == "__main__":
    unittest.main()
