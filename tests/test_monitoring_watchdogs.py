"""Neue Wächter: Studio-Kette, DB↔Gerät-Abgleich, Keypad-Fehlversuche (rein, ohne Netz/DB)."""
import unittest

from nuki_integration.services.monitoring import (
    classify_keypad_event,
    classify_studio_link,
    diff_db_vs_device,
)


class StudioLinkTests(unittest.TestCase):
    def test_all_up(self):
        kind, _ = classify_studio_link(nas_tailscale=True, public_endpoint=True, home_assistant=True)
        self.assertIsNone(kind)

    def test_ha_down_only(self):
        kind, reason = classify_studio_link(nas_tailscale=True, public_endpoint=True, home_assistant=False)
        self.assertEqual(kind, "home-assistant-offline")
        self.assertIn("Home Assistant", reason)

    def test_nas_down_but_internet_up(self):
        """Antwortet der öffentliche Endpunkt, steht die Leitung — dann ist es die NAS."""
        kind, _ = classify_studio_link(nas_tailscale=False, public_endpoint=True, home_assistant=False)
        self.assertEqual(kind, "nas-offline")

    def test_everything_unreachable_is_the_line(self):
        kind, reason = classify_studio_link(nas_tailscale=False, public_endpoint=False, home_assistant=False)
        self.assertEqual(kind, "studio-internet-down")
        self.assertIn("NAS", reason)  # ehrlich: NAS-aus ist nicht ausgeschlossen

    def test_tailscale_up_implies_internet_up(self):
        """Tailscale läuft über dieselbe WAN-Leitung — nie 'Internet weg' melden, wenn es antwortet."""
        for public in (True, False):
            kind, _ = classify_studio_link(nas_tailscale=True, public_endpoint=public, home_assistant=True)
            self.assertIsNone(kind)


class CodeSyncTests(unittest.TestCase):
    def test_mismatch_detected(self):
        r = diff_db_vs_device({"og-h06-p0": "111222", "og-h07-p0": "333444"},
                              {"og-h06-p0": "999888", "og-h07-p0": "333444"})
        self.assertEqual(r["mismatched"], ["og-h06-p0"])
        self.assertEqual(r["matched"], ["og-h07-p0"])

    def test_invisible_is_not_a_mismatch(self):
        """Die Hub-Firmware deckelt ihre Liste — fehlende Slots sind ungeprüft, nicht falsch."""
        r = diff_db_vs_device({"og-h20-p0": "111222"}, {})
        self.assertEqual(r["mismatched"], [])
        self.assertEqual(r["invisible"], ["og-h20-p0"])

    def test_int_vs_str_codes_compare_equal(self):
        r = diff_db_vs_device({"og-bh-p0": "853421"}, {"og-bh-p0": 853421})
        self.assertEqual(r["mismatched"], [])


class KeypadEventTests(unittest.TestCase):
    def _hub(self, **over):
        e = {"index": 15185, "authorizationName": "og-h17-p0", "type": "KeypadAction",
             "action": "Unlatch", "trigger": "code", "completionStatus": "success", "codeId": 8300}
        e.update(over)
        return e

    def test_hub_success(self):
        cat, ctx = classify_keypad_event(self._hub())
        self.assertEqual(cat, "keypad-accepted")
        self.assertEqual(ctx["code_id"], 8300)

    def test_hub_rejection(self):
        cat, ctx = classify_keypad_event(self._hub(completionStatus="invalidCode"))
        self.assertEqual(cat, "keypad-rejected")
        self.assertEqual(ctx["completion_status"], "invalidCode")

    def test_wrong_slot_is_a_rejection(self):
        """Richtiger Code, falsches Zeitfenster → das Schloss lehnt ab."""
        self.assertEqual(classify_keypad_event(self._hub(completionStatus="timeRestricted"))[0],
                         "keypad-rejected")

    def test_fingerprint_counts_as_keypad(self):
        self.assertEqual(classify_keypad_event(self._hub(trigger="fingerprint"))[0], "keypad-accepted")

    def test_non_keypad_entries_ignored(self):
        for e in ({"type": "DoorSensor", "action": "DoorOpened"},
                  {"type": "LockAction", "action": "Lock", "trigger": "system",
                   "completionStatus": "success", "authorizationName": "MQTT"}):
            self.assertIsNone(classify_keypad_event(e)[0], e)

    def test_legacy_webapi_entry_still_parsed(self):
        cat, ctx = classify_keypad_event({"name": "og-h04-p1", "date": "2026-09-05T04:10:00.000Z",
                                          "action": 1, "state": 0, "id": "abc"})
        self.assertEqual(cat, "keypad-accepted")
        self.assertEqual(ctx["auth_name"], "og-h04-p1")


if __name__ == "__main__":
    unittest.main()
