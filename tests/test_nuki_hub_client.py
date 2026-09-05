"""Nuki-Hub-Transport: Shape-Übersetzung Hub → Web API (kein Netzwerk, kein MQTT)."""
import unittest
from types import SimpleNamespace

from nuki_integration.nuki_client import (
    build_nuki_client,
    evaluate_window_materialization,
)
from nuki_integration.nuki_hub_client import (
    NukiHubMqttClient,
    _hhmm,
    _hub_date,
    _minutes,
    _weekday_names,
    hub_entry_to_auth,
    parse_keypad_json,
)

SEEN = "2026-09-05T20:00:00.000Z"


def _entry(**over):
    base = {
        "codeId": 8195, "code": 258244, "enabled": 1, "name": "og-bh-p4",
        "dateCreated": "2026-09-01 09:49:13", "lockCount": 0,
        "dateLastActive": "0000-00-00 00:00:00", "timeLimited": 1,
        "allowedFrom": "2026-09-01 00:00:00", "allowedUntil": "2026-12-31 23:59:00",
        "allowedWeekdays": ["mon", "tue", "wed", "thu", "fri", "sat"],
        "allowedFromTime": "08:00", "allowedUntilTime": "21:00",
    }
    base.update(over)
    return base


class ConversionTests(unittest.TestCase):
    def test_time_and_weekday_translation(self):
        auth = hub_entry_to_auth(_entry(), seen_at=SEEN)
        self.assertEqual(auth["type"], 13)
        self.assertEqual(auth["id"], 8195)
        self.assertEqual(auth["code"], 258244)
        self.assertEqual(auth["allowedFromTime"], 480)
        self.assertEqual(auth["allowedUntilTime"], 1260)
        self.assertEqual(auth["allowedWeekDays"], 126)  # Mo–Sa
        self.assertEqual(auth["allowedUntilDate"], "2026-12-31T23:59:00Z")
        self.assertEqual(auth["updateDate"], SEEN)

    def test_hourly_slot_covers_its_own_hour_only(self):
        entry = _entry(
            name="og-h20-p0", code=253694, allowedWeekdays=["sat", "sun"],
            allowedFromTime="20:00", allowedUntilTime="21:00",
        )
        auths = [hub_entry_to_auth(entry, seen_at=SEEN)]
        sat20 = evaluate_window_materialization(auths, "253694", 5, 20)
        self.assertTrue(sat20["exists"] and sat20["covers_window"] and sat20["valid"])
        self.assertFalse(evaluate_window_materialization(auths, "253694", 5, 19)["covers_window"])
        self.assertFalse(evaluate_window_materialization(auths, "253694", 0, 20)["covers_window"])

    def test_empty_daily_span_means_unrestricted_not_never(self):
        """00:00–00:00 on the device = no daily limit; must not fail-close every hour."""
        entry = _entry(name="og-bh-p0", code=853421, allowedFromTime="00:00",
                       allowedUntilTime="00:00", allowedWeekdays=[])
        auth = hub_entry_to_auth(entry, seen_at=SEEN)
        self.assertIsNone(auth["allowedFromTime"])
        for hour in (3, 11, 23):
            self.assertTrue(
                evaluate_window_materialization([auth], "853421", 2, hour)["valid"],
                f"hour {hour} should be covered",
            )

    def test_untimed_entry_has_no_window(self):
        auth = hub_entry_to_auth(_entry(timeLimited=0), seen_at=SEEN)
        self.assertIsNone(auth["allowedUntilDate"])
        self.assertEqual(auth["allowedWeekDays"], 0)
        self.assertTrue(evaluate_window_materialization([auth], "258244", 6, 4)["valid"])

    def test_roundtrip_helpers(self):
        self.assertEqual(_minutes("07:30"), 450)
        self.assertEqual(_hhmm(450), "07:30")
        self.assertEqual(_hhmm(None), "00:00")
        self.assertEqual(_hub_date("2026-12-31T23:59:59Z"), "2026-12-31 23:59:59")
        self.assertEqual(_hub_date("2026-09-01"), "2026-09-01 00:00:00")
        self.assertEqual(_weekday_names(126), ["mon", "tue", "wed", "thu", "fri", "sat"])
        self.assertEqual(len(_weekday_names(0)), 7)  # falsy mask = every day


class ParseTests(unittest.TestCase):
    def test_complete_payload(self):
        payload = "[%s,%s]" % (
            _json(_entry()), _json(_entry(codeId=8196, code=111222, name="og-h05-p0")),
        )
        auths, truncated = parse_keypad_json(payload, seen_at=SEEN)
        self.assertFalse(truncated)
        self.assertEqual([a["name"] for a in auths], ["og-bh-p4", "og-h05-p0"])

    def test_truncated_payload_salvages_complete_entries(self):
        """The hub cuts keypad/json at its buffer limit — keep what did arrive."""
        full = "[%s,%s]" % (_json(_entry()), _json(_entry(codeId=8196, name="og-h05-p0")))
        cut = full[: full.index("},") + 2] + '{"codeId":8196,"code":111'
        auths, truncated = parse_keypad_json(cut, seen_at=SEEN)
        self.assertTrue(truncated)
        self.assertEqual([a["name"] for a in auths], ["og-bh-p4"])

    def test_empty_payload(self):
        self.assertEqual(parse_keypad_json("", seen_at=SEEN), ([], False))


class ActionPayloadTests(unittest.TestCase):
    def _client(self):
        return NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
        ))

    def test_update_payload_matches_hub_dialect(self):
        payload = self._client()._action_payload(
            "update", name="og-h20-p0", code="253694",
            allowed_from="2026-09-06T00:00:00Z", allowed_until="2026-12-31T23:59:59Z",
            allowed_week_days=3, allowed_from_time=1200, allowed_until_time=1260,
        )
        self.assertEqual(payload["action"], "update")
        self.assertEqual(payload["code"], 253694)
        self.assertEqual(payload["timeLimited"], 1)
        self.assertEqual(payload["allowedWeekdays"], ["sat", "sun"])
        self.assertEqual(payload["allowedFromTime"], "20:00")
        self.assertEqual(payload["allowedUntilTime"], "21:00")
        self.assertEqual(payload["allowedUntil"], "2026-12-31 23:59:59")


class FactoryTests(unittest.TestCase):
    def _settings(self, transport):
        return SimpleNamespace(
            nuki_transport=transport, nuki_base_url="https://api.nuki.io",
            nuki_timeout_seconds=5, nuki_smartlock_id=1, nuki_dry_run=True,
            active_nuki_token="", nuki_mqtt_host="broker", nuki_mqtt_port=1883,
            nuki_mqtt_username="", nuki_mqtt_password="", nuki_mqtt_prefix="nukihub",
            nuki_mqtt_timeout_seconds=5,
        )

    def test_transport_switch(self):
        self.assertIsInstance(build_nuki_client(self._settings("nukihub")), NukiHubMqttClient)
        hub = build_nuki_client(self._settings("nukihub"))
        self.assertEqual(type(build_nuki_client(self._settings("webapi"))).__name__, "NukiClient")
        # Unknown transport must not leave the worker clientless.
        self.assertEqual(type(build_nuki_client(self._settings("bogus"))).__name__, "NukiClient")
        hub.close()

    def test_hub_client_exposes_the_webapi_surface(self):
        hub = build_nuki_client(self._settings("nukihub"))
        for name in ("list_keypad_codes", "verify_materialization", "verify_code_for_window",
                     "create_keypad_code", "update_keypad_code", "delete_keypad_code",
                     "deactivate_keypad_code", "remote_open", "remote_lock", "remote_unlatch",
                     "get_lock_status", "get_log", "force_sync", "close"):
            self.assertTrue(callable(getattr(hub, name, None)), name)
        hub.close()


def _json(entry):
    import json
    return json.dumps(entry)


if __name__ == "__main__":
    unittest.main()
