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

    def test_ha_tls_invalid(self):
        kind, reason = classify_studio_link(nas_tailscale=True, public_endpoint=False, home_assistant=False,
                                            ha_tls_invalid=True)
        self.assertEqual(kind, "home-assistant-tls-invalid")
        self.assertIn("TLS-Zertifikat", reason)

    def test_ha_tls_invalid_takes_precedence_over_unreachable(self):
        """Bei TLS-Fehler antwortet der Server — kein 'Internet down', sondern spezifischer TLS-Alarm."""
        kind, reason = classify_studio_link(nas_tailscale=False, public_endpoint=False, home_assistant=False,
                                            ha_tls_invalid=True)
        self.assertEqual(kind, "home-assistant-tls-invalid")
        self.assertIn("TLS-Zertifikat", reason)


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


class NukiLinkAlertTests(unittest.TestCase):
    """``hybrid_connected=False`` ist seit dem Abschalten des Hybrid-Modus (06.09.2026)
    der ABSICHTLICHE Normalzustand. Als Alarmkriterium meldete es alle 30 Minuten ein
    Schloss als unerreichbar, das laut ``availability`` erreichbar war."""

    def _alarme(self, **health):
        from types import SimpleNamespace
        from unittest import mock
        from nuki_integration.services import monitoring

        zustand = {"responsive": True, "mqtt_connected": True, "lock_available": True,
                   "hybrid_connected": False, "lock_state": "locked", "battery_level": 65,
                   "battery_critical": False, "ble_rssi": -65, "wifi_rssi": -42,
                   "uptime": 100, "error": None}
        zustand.update(health)
        nuki = SimpleNamespace(hub_health=lambda: zustand)
        raus = []
        with mock.patch.object(monitoring, "notify",
                               side_effect=lambda *a, **k: (raus.append(k.get("key")), True)[1]), \
             mock.patch.object(monitoring, "resolve", lambda *a, **k: None):
            monitoring.check_nuki_link(None, object(), nuki=nuki)
        return raus

    def test_disabled_hybrid_is_not_an_unreachable_lock(self):
        self.assertNotIn("nuki-lock-unreachable", self._alarme())

    def test_a_genuinely_unreachable_lock_still_alerts(self):
        self.assertIn("nuki-lock-unreachable", self._alarme(lock_available=False))

    def test_a_silent_hub_still_alerts(self):
        raus = self._alarme(responsive=False)
        self.assertIn("nuki-hub-offline", raus)
        self.assertNotIn("nuki-lock-unreachable", raus)  # frueher Ausstieg, keine Doppelmeldung


class HubBusyTests(unittest.TestCase):
    """Ein Keypad-Vollabzug (109 Codes ueber BLE, auf UNSERE Anforderung) legt den Hub
    fuer ~1 min lahm. Das darf weder als Ausfall gemeldet noch als Erholung entwarnt
    werden — sonst flattert der Alarm stuendlich (Vorfall 08.09.2026, 13:30)."""

    def _run(self, **health):
        from types import SimpleNamespace
        from unittest import mock
        from nuki_integration.services import monitoring

        zustand = {"responsive": False, "busy": False, "mqtt_connected": True,
                   "lock_available": True, "hybrid_connected": False, "lock_state": "locked",
                   "battery_level": 65, "battery_critical": False, "ble_rssi": -65,
                   "wifi_rssi": -42, "uptime": 100, "error": None}
        zustand.update(health)
        raus, entwarnt = [], []
        with mock.patch.object(monitoring, "notify",
                               side_effect=lambda *a, **k: (raus.append(k.get("key")), True)[1]), \
             mock.patch.object(monitoring, "resolve",
                               side_effect=lambda *a, **k: entwarnt.append(k.get("key"))):
            res = monitoring.check_nuki_link(None, object(),
                                             nuki=SimpleNamespace(hub_health=lambda: zustand))
        return raus, entwarnt, res

    def test_busy_hub_is_not_reported_offline(self):
        raus, entwarnt, res = self._run(busy=True)
        self.assertEqual(raus, [])
        self.assertTrue(res.get("no_verdict"))

    def test_busy_hub_does_not_falsely_resolve_an_open_outage(self):
        """Kein Urteil heisst KEIN Urteil — auch keine vorschnelle Entwarnung."""
        _raus, entwarnt, _res = self._run(busy=True)
        self.assertEqual(entwarnt, [])

    def test_a_real_outage_still_alerts(self):
        raus, _e, _r = self._run(busy=False)
        self.assertIn("nuki-hub-offline", raus)

    def test_a_responsive_hub_resolves(self):
        raus, entwarnt, _r = self._run(responsive=True)
        self.assertEqual(raus, [])
        self.assertIn("nuki-hub-offline", entwarnt)


class StudioLinkCheckTests(unittest.TestCase):
    """Prüfung von check_studio_link und TLS-Fehlerbehandlung (M3.4)."""

    def test_http_check_detects_tls_error(self):
        import ssl
        import httpx
        from unittest import mock
        from nuki_integration.services.monitoring import _http_check

        ssl_err = ssl.SSLCertVerificationError("certificate verify failed: certificate has expired")
        connect_err = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
                                         request=httpx.Request("GET", "https://services.getimpulse.de:8123"))
        connect_err.__cause__ = ssl_err

        with mock.patch("httpx.get", side_effect=connect_err):
            ok, tls_invalid = _http_check("https://services.getimpulse.de:8123")
            self.assertFalse(ok)
            self.assertTrue(tls_invalid)

    def test_http_check_valid_cert(self):
        import httpx
        from unittest import mock
        from nuki_integration.services.monitoring import _http_check

        mock_resp = mock.Mock(status_code=200)
        with mock.patch("httpx.get", return_value=mock_resp):
            ok, tls_invalid = _http_check("https://services.getimpulse.de:8123")
            self.assertTrue(ok)
            self.assertFalse(tls_invalid)

    def test_http_check_other_network_error(self):
        import httpx
        from unittest import mock
        from nuki_integration.services.monitoring import _http_check

        with mock.patch("httpx.get", side_effect=httpx.ConnectError("Connection refused",
                                                                     request=httpx.Request("GET", "https://services.getimpulse.de:8123"))):
            ok, tls_invalid = _http_check("https://services.getimpulse.de:8123")
            self.assertFalse(ok)
            self.assertFalse(tls_invalid)

    def test_check_studio_link_tls_error_alerts(self):
        from types import SimpleNamespace
        import ssl
        import httpx
        from unittest import mock
        from nuki_integration.services import monitoring

        settings = SimpleNamespace(
            nuki_mqtt_host="100.103.57.114",
            nuki_mqtt_port=1883,
            ha_url="https://services.getimpulse.de:8123",
            ha_token="dummy-token",
        )

        ssl_err = ssl.SSLCertVerificationError("certificate verify failed: certificate has expired")
        connect_err = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]",
                                         request=httpx.Request("GET", "https://services.getimpulse.de:8123"))
        connect_err.__cause__ = ssl_err

        raus, entwarnt = [], []
        with mock.patch.object(monitoring, "_tcp_open", return_value=True), \
             mock.patch("httpx.get", side_effect=connect_err), \
             mock.patch.object(monitoring, "notify",
                               side_effect=lambda *a, **k: (raus.append(k.get("key")), True)[1]), \
             mock.patch.object(monitoring, "resolve",
                               side_effect=lambda *a, **k: entwarnt.append(k.get("key"))):
            res = monitoring.check_studio_link(None, settings)

        self.assertEqual(res["kind"], "home-assistant-tls-invalid")
        self.assertTrue(res["ha_tls_invalid"])
        self.assertIn("home-assistant-tls-invalid", raus)
        self.assertIn("home-assistant-offline", entwarnt)

    def test_check_studio_link_valid_cert_resolves(self):
        from types import SimpleNamespace
        from unittest import mock
        from nuki_integration.services import monitoring

        settings = SimpleNamespace(
            nuki_mqtt_host="100.103.57.114",
            nuki_mqtt_port=1883,
            ha_url="https://services.getimpulse.de:8123",
            ha_token="dummy-token",
        )

        mock_resp = mock.Mock(status_code=200)
        raus, entwarnt = [], []
        with mock.patch.object(monitoring, "_tcp_open", return_value=True), \
             mock.patch("httpx.get", return_value=mock_resp), \
             mock.patch.object(monitoring, "notify",
                               side_effect=lambda *a, **k: (raus.append(k.get("key")), True)[1]), \
             mock.patch.object(monitoring, "resolve",
                               side_effect=lambda *a, **k: entwarnt.append(k.get("key"))):
            res = monitoring.check_studio_link(None, settings)

        self.assertIsNone(res["kind"])
        self.assertFalse(res["alerted"])
        self.assertFalse(res["ha_tls_invalid"])
        self.assertEqual(raus, [])
        self.assertIn("home-assistant-tls-invalid", entwarnt)
        self.assertIn("home-assistant-offline", entwarnt)

