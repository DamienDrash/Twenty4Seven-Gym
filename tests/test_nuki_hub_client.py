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


class UnpublishedSlotTests(unittest.TestCase):
    """Der Hub zeigt nicht alle Keypad-Einträge — das darf kein Aussperrgrund sein."""

    def _client(self, trust):
        c = NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
            nuki_hub_trust_unpublished=trust,
        ))
        c._connect = lambda: None
        c._auths_or_error = lambda: ([hub_entry_to_auth(_entry(), seen_at=SEEN)], False)
        return c

    def test_unpublished_slot_is_delivered_when_trusted(self):
        c = self._client(True)
        r = c.verify_code_for_window("227892", weekday=6, hour=20)
        self.assertTrue(r["deliverable"])
        self.assertEqual(r["window_source"], "unpublished-slot")
        c.close()

    def test_unpublished_slot_fails_closed_by_default(self):
        c = self._client(False)
        r = c.verify_code_for_window("227892", weekday=6, hour=20)
        self.assertFalse(r.get("deliverable"))
        self.assertFalse(r["exists"])
        c.close()

    def test_published_slot_is_still_checked_normally(self):
        """Ein sichtbarer Code wird weiterhin am Fenster geprüft, nicht durchgewunken."""
        c = self._client(True)
        r = c.verify_code_for_window("258244", weekday=6, hour=4)   # bh: Mo-Sa 08-21
        self.assertFalse(r["covers_window"], "Sonntag 04:00 darf nicht abgedeckt sein")
        c.close()


class SlotNameVerificationTests(unittest.TestCase):
    """Nach Slot-NAME suchen und den Code-Wert vom Gerät bestätigen lassen.

    Grund: Die per-Eintrag-Topics sind retained und können veralten — am
    06.09.2026 trugen zehn Slots noch die Codes der Rotation vom 06.08.
    """

    def _client(self, entry_code, live):
        c = NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
            nuki_hub_trust_unpublished=False,
        ))
        entry = hub_entry_to_auth(_entry(name="og-h20-p0", code=entry_code,
                                         allowedWeekdays=["sat", "sun"],
                                         allowedFromTime="20:00", allowedUntilTime="21:00"),
                                  seen_at=SEEN)
        c._auths_or_error = lambda: ([entry], False)
        c.check_keypad_code = lambda *, code_id, code: live
        return c

    def test_stale_topic_value_does_not_block_the_real_code(self):
        """Topic zeigt den alten Code, das Gerät bestätigt den neuen → zustellen."""
        c = self._client(entry_code=542228, live=True)      # Topic veraltet
        r = c.verify_code_for_window("227892", weekday=5, hour=20, slot_name="og-h20-p0")
        self.assertTrue(r["deliverable"])
        self.assertEqual(r["window_source"], "slot-name+device-check")
        c.close()

    def test_device_rejects_code_then_fail_closed(self):
        c = self._client(entry_code=227892, live=False)
        r = c.verify_code_for_window("227892", weekday=5, hour=20, slot_name="og-h20-p0")
        self.assertFalse(r["deliverable"])
        c.close()

    def test_window_of_the_named_entry_is_still_enforced(self):
        c = self._client(entry_code=227892, live=True)
        r = c.verify_code_for_window("227892", weekday=5, hour=11, slot_name="og-h20-p0")
        self.assertFalse(r["covers_window"], "11 Uhr liegt nicht im 20-Uhr-Fenster")
        self.assertFalse(r["deliverable"])
        c.close()

    def test_unknown_slot_name_falls_back_to_code_matching(self):
        c = self._client(entry_code=227892, live=True)
        r = c.verify_code_for_window("227892", weekday=5, hour=20, slot_name="og-h99-p9")
        self.assertNotEqual(r.get("window_source"), "slot-name+device-check")
        c.close()


class PerEntrySourceTests(unittest.TestCase):
    """codes/<n>-JSON schlägt die alten Feld-Topics: mit Fenster, ohne Größenlimit."""

    def _client(self, messages):
        c = NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
        ))
        c._messages = dict(messages)
        return c

    def test_json_topics_carry_the_time_window(self):
        c = self._client({
            "nukihub/lock/keypad/codes/7": _json(_entry(
                name="og-h20-p0", code=227892, allowedWeekdays=["sat", "sun"],
                allowedFromTime="20:00", allowedUntilTime="21:00")),
        })
        auths = c._per_entry_auths("2026-09-06T12:00:00.000Z")
        self.assertEqual(len(auths), 1)
        self.assertEqual(auths[0]["allowedFromTime"], 1200)
        self.assertEqual(auths[0]["allowedWeekDays"], 3)
        self.assertNotIn("windowUnknown", auths[0])
        c.close()

    def test_legacy_field_topics_are_only_a_fallback(self):
        c = self._client({
            "nukihub/lock/keypad/code_3/name": "og-h07-p0",
            "nukihub/lock/keypad/code_3/code": "434681",
            "nukihub/lock/keypad/code_3/id": "8250",
        })
        auths = c._per_entry_auths("2026-09-06T12:00:00.000Z")
        self.assertEqual(len(auths), 1)
        self.assertTrue(auths[0]["windowUnknown"])
        c.close()

    def test_json_topics_win_over_stale_field_topics(self):
        c = self._client({
            "nukihub/lock/keypad/codes/7": _json(_entry(name="og-h20-p0", code=227892)),
            "nukihub/lock/keypad/code_99/name": "og-h20-p0",
            "nukihub/lock/keypad/code_99/code": "542228",   # Leiche vom 06.08.
            "nukihub/lock/keypad/code_99/id": "8306",
        })
        auths = c._per_entry_auths("2026-09-06T12:00:00.000Z")
        self.assertEqual([a["code"] for a in auths], [227892])
        c.close()


if __name__ == "__main__":
    unittest.main()


class RequeryThrottleTests(unittest.TestCase):
    """Ein erzwungener Keypad-Read kostet den Hub ~1000 Retain-Publishes — nicht im Minutentakt."""

    def test_requery_is_rate_limited(self):
        c = NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
        ))
        published = []
        c._connect = lambda: None
        c._publish = lambda suffix, payload: published.append(suffix)
        c._await_message = lambda *a, **k: None
        c._last = lambda suffix: "[]"
        c._per_entry_auths = lambda seen_at: []
        c.list_keypad_codes(cache_seconds=0.0)
        self.assertIn("lock/query/keypad", published)
        c.list_keypad_codes(cache_seconds=0.0)          # sofort danach
        self.assertEqual(published.count("lock/query/keypad"), 1, "zweite Abfrage muss gedrosselt sein")
        c._last_query_at -= c._MIN_REQUERY_SECONDS + 1  # Fenster abgelaufen
        c.list_keypad_codes(cache_seconds=0.0)
        self.assertEqual(published.count("lock/query/keypad"), 2)
        c.close()


class NullKeypadJsonTests(unittest.TestCase):
    """06.09.2026: nach jedem Keypad-Schreibvorgang publizierte der Hub ``keypad/json``
    als blankes ``null``. Das Iterieren riss die gesamte Auflistung mit
    (``'NoneType' object is not iterable``) — und damit auch die 108 Einzel-Topics,
    in denen die Codes tatsächlich standen."""

    def _client(self):
        return NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
        ))

    def _offline_transport(self, c, *, retained, per_entry):
        c._connect = lambda: None
        c._publish = lambda *a, **k: None
        c._await_message = lambda *a, **k: None
        c._last = lambda suffix: retained
        c._per_entry_auths = lambda seen_at: list(per_entry)

    def test_null_is_neither_a_crash_nor_a_truncation(self):
        self.assertEqual(parse_keypad_json("null", seen_at=SEEN), ([], False))

    def test_valid_json_that_is_not_an_array_is_ignored(self):
        self.assertEqual(parse_keypad_json('{"codeId":8195}', seen_at=SEEN), ([], False))

    def test_null_json_falls_back_to_the_per_entry_topics(self):
        c = self._client()
        entry = hub_entry_to_auth(_entry(), seen_at=SEEN)
        self._offline_transport(c, retained="null", per_entry=[entry])
        self.assertEqual([a["name"] for a in c.list_keypad_codes(cache_seconds=0.0)],
                         ["og-bh-p4"])
        c.close()

    def test_null_json_without_per_entry_topics_is_unreachable_not_empty(self):
        """Sonst läse sich ein volles Keypad als „das Schloss hat keine Code" — fail-open."""
        c = self._client()
        self._offline_transport(c, retained="null", per_entry=[])
        auths, unreachable = c._auths_or_error()
        self.assertEqual(auths, [])
        self.assertTrue(unreachable)
        c.close()

    def test_silent_hub_is_unreachable_even_with_per_entry_retains_lying_around(self):
        """Der gefaehrliche Fall: der Hub schweigt, aber 109 alte Einzel-Topic-Retains
        liegen noch im Broker. ``list_keypad_codes`` fasst die dann bewusst nicht an —
        also duerfen sie auch hier nicht als Schnappschuss zaehlen, sonst laese sich
        ein volles Keypad als leer."""
        c = self._client()
        self._offline_transport(c, retained=None, per_entry=[hub_entry_to_auth(_entry(), seen_at=SEEN)])
        auths, unreachable = c._auths_or_error()
        self.assertEqual(auths, [])
        self.assertTrue(unreachable)
        c.close()

    def test_a_genuinely_empty_keypad_stays_a_real_answer(self):
        """Gegenprobe: ``[]`` von einem lebenden Hub ist eine Aussage, kein Ausfall."""
        c = self._client()
        self._offline_transport(c, retained="[]", per_entry=[])
        auths, unreachable = c._auths_or_error()
        self.assertEqual(auths, [])
        self.assertFalse(unreachable)
        c.close()


class WriteConfirmationTests(unittest.TestCase):
    """``last_write_confirmed()`` ist die Geräteantwort des Schlosses — sie darf nur für
    genau den Schreibvorgang bürgen, der sie ausgelöst hat."""

    def _client(self):
        c = NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
        ))
        c._connect = lambda: None
        c._publish = lambda *a, **k: None
        return c

    def test_fresh_success_confirms(self):
        c = self._client()
        c._await_message = lambda *a, **k: "success"
        self.assertEqual(c._keypad_action({"action": "delete", "codeId": 1}), "success")
        self.assertTrue(c.last_write_confirmed())
        c.close()

    def test_retained_success_does_not_confirm(self):
        """Ein liegengebliebenes ``success`` darf nicht für den nächsten Write bürgen —
        sonst löscht die Rotation einen Vorgänger auf Basis einer alten Quittung."""
        c = self._client()
        c._await_message = lambda *a, **k: None      # keine frische Antwort
        c._last = lambda suffix: "success"           # aber ein Retain liegt herum
        self.assertEqual(c._keypad_action({"action": "delete", "codeId": 1}), "success")
        self.assertFalse(c.last_write_confirmed())
        c.close()

    def test_confirmation_is_cleared_before_each_write(self):
        c = self._client()
        c._await_message = lambda *a, **k: "success"
        c._keypad_action({"action": "add", "codeId": 1})
        self.assertTrue(c.last_write_confirmed())
        c._await_message = lambda *a, **k: None
        c._last = lambda suffix: None
        c._keypad_action({"action": "add", "codeId": 2})
        self.assertFalse(c.last_write_confirmed(), "alte Quittung darf nicht nachwirken")
        c.close()

    def test_a_rejection_is_not_a_confirmation(self):
        c = self._client()
        c._await_message = lambda *a, **k: "noValidPinSet"
        c._keypad_action({"action": "add", "codeId": 1})
        self.assertFalse(c.last_write_confirmed())
        c.close()


class MissingKeypadJsonTests(unittest.TestCase):
    """Seit 06.09.2026 publiziert der Hub ``keypad/json`` gar nicht mehr (Heap-Grenze).
    Die Einzel-Topics sind dann die einzige Sicht aufs Keypad — verwendbar, aber
    ausschliesslich mit Lebendnachweis, weil Retains einen toten Hub ueberdauern."""

    def _client(self):
        c = NukiHubMqttClient(SimpleNamespace(
            nuki_dry_run=False, nuki_smartlock_id=1, nuki_mqtt_host="broker",
            nuki_mqtt_port=1883, nuki_mqtt_username="", nuki_mqtt_password="",
            nuki_mqtt_prefix="nukihub", nuki_mqtt_timeout_seconds=5,
        ))
        c._connect = lambda: None
        c._publish = lambda *a, **k: None
        c._await_message = lambda *a, **k: None
        c._last = lambda suffix: None          # keypad/json fehlt vollstaendig
        return c

    def test_live_hub_falls_back_to_the_per_entry_topics(self):
        c = self._client()
        c._per_entry_auths = lambda seen_at: [hub_entry_to_auth(_entry(), seen_at=SEEN)]
        c._hub_is_live = lambda: True
        self.assertEqual([a["name"] for a in c.list_keypad_codes(cache_seconds=0.0)],
                         ["og-bh-p4"])
        c.close()

    def test_dead_hub_stays_fail_closed(self):
        """Ohne Lebendnachweis sind dieselben Retains wertlos — dann lieber
        „kann nicht pruefen" als ein selbstbewusst falsches Urteil."""
        c = self._client()
        c._per_entry_auths = lambda seen_at: [hub_entry_to_auth(_entry(), seen_at=SEEN)]
        c._hub_is_live = lambda: False
        self.assertEqual(c.list_keypad_codes(cache_seconds=0.0), [])
        _auths, unreachable = c._auths_or_error()
        self.assertTrue(unreachable)
        c.close()

    def test_liveness_is_a_real_round_trip_and_is_cached(self):
        """Ein Retain darf den Nachweis nicht erbringen — es muss eine frische Antwort
        auf eine gerade gestellte Frage sein. Und einmal pro Minute reicht."""
        c = self._client()
        published = []
        c._publish = lambda suffix, payload: published.append(suffix)
        c._await_message = lambda suffix, **k: "{}" if suffix == "lock/json" else None
        self.assertTrue(c._hub_is_live())
        self.assertIn("lock/query/lockstate", published)
        self.assertTrue(c._hub_is_live())
        self.assertEqual(published.count("lock/query/lockstate"), 1,
                         "zweiter Aufruf muss aus dem Cache kommen")
        c.close()

    def test_a_silent_hub_is_not_alive(self):
        c = self._client()
        self.assertFalse(c._hub_is_live())
        c.close()
