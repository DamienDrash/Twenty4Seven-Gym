"""NukiClient time-window payload + materialisation check (no network)."""
import unittest
from types import SimpleNamespace

from nuki_integration.nuki_client import NukiClient, evaluate_materialization


def _client():
    settings = SimpleNamespace(
        nuki_base_url="https://api.nuki.io", nuki_timeout_seconds=5,
        nuki_smartlock_id=22751454439, nuki_dry_run=True, active_nuki_token="",
    )
    return NukiClient(settings)


class PayloadTests(unittest.TestCase):
    def test_time_window_fields_included(self):
        c = _client()
        payload = c._build_keypad_payload(
            name="og-h22-p0", code="345678",
            allowed_from="2026-07-06T00:00:00Z", allowed_until="2026-08-05T23:59:59Z",
            allowed_week_days=3, allowed_from_time=1320, allowed_until_time=1380,
        )
        self.assertEqual(payload["type"], 13)
        self.assertEqual(payload["code"], 345678)
        self.assertEqual(payload["allowedWeekDays"], 3)
        self.assertEqual(payload["allowedFromTime"], 1320)
        self.assertEqual(payload["allowedUntilTime"], 1380)
        c.close()

    def test_no_time_window_when_omitted(self):
        c = _client()
        payload = c._build_keypad_payload(
            name="x", code="345678",
            allowed_from="a", allowed_until="b",
        )
        self.assertEqual(payload["allowedWeekDays"], 127)
        self.assertNotIn("allowedFromTime", payload)
        c.close()

    def test_dry_run_create_skips(self):
        c = _client()
        self.assertIsNone(c.create_keypad_code(
            name="x", code="345678", allowed_from="a", allowed_until="b"))
        # DRY-RUN materialisation → simulated OK, no network
        r = c.verify_materialization("345678")
        self.assertTrue(r["materialised"])
        self.assertTrue(r["simulated"])
        c.close()


class MaterializationTests(unittest.TestCase):
    def test_present_with_update_date(self):
        auths = [{"type": 13, "code": 345678, "id": 7, "updateDate": "2026-07-06T03:00:00Z"}]
        r = evaluate_materialization(auths, "345678")
        self.assertTrue(r["materialised"])
        self.assertEqual(r["auth_id"], 7)

    def test_present_but_null_update_date_is_bug(self):
        auths = [{"type": 13, "code": 345678, "id": 7, "updateDate": None}]
        self.assertFalse(evaluate_materialization(auths, "345678")["materialised"])

    def test_absent(self):
        self.assertFalse(evaluate_materialization([], "345678")["materialised"])


if __name__ == "__main__":
    unittest.main()
