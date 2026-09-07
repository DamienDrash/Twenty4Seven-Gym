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


class HealthEndpointTests(unittest.TestCase):
    def test_health_endpoints(self):
        from fastapi.testclient import TestClient
        from nuki_integration.app import app
        with mock.patch("nuki_integration.db.Database.health_check", return_value=True):
            client = TestClient(app)

            r_health = client.get("/health")
            self.assertEqual(r_health.status_code, 200)
            self.assertEqual(r_health.json(), {"status": "ready"})

            r_live = client.get("/healthz/live")
            self.assertEqual(r_live.status_code, 200)
            self.assertEqual(r_live.json(), {"status": "alive"})

            r_ready = client.get("/healthz/ready")
            self.assertEqual(r_ready.status_code, 200)
            self.assertEqual(r_ready.json(), {"status": "ready"})


class FreezeWatchTests(unittest.TestCase):
    def test_unpaused_cleans_up(self):
        db = mock.MagicMock()
        settings = mock.MagicMock(nuki_rotation_paused=False)
        res = m.check_freeze_watch(db, settings)
        self.assertFalse(res["paused"])
        self.assertFalse(res["alerted_24h"])

    def test_paused_under_24h(self):
        db = mock.MagicMock()
        # mock DB cursor query returning freeze_start = 5 hours ago
        now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
        five_hours_ago = now - timedelta(hours=5)
        cursor_mock = mock.MagicMock()
        cursor_mock.fetchone.return_value = {"value": five_hours_ago.isoformat(), "updated_at": five_hours_ago}
        db.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor_mock

        settings = mock.MagicMock(nuki_rotation_paused=True)
        res = m.check_freeze_watch(db, settings, now=now)
        self.assertTrue(res["paused"])
        self.assertEqual(res["age_hours"], 5)
        self.assertFalse(res["alerted_24h"])


if __name__ == "__main__":
    unittest.main()


# ── Zustandswechsel statt Dauerfeuer (Betreiber-Entscheid 07.09.2026) ──────────
class _FakeCursor:
    def __init__(self, row):
        self.row, self.executed = row, []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return self.row
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cur):
        self.cur, self.commits = cur, 0
    def cursor(self):
        return self.cur
    def commit(self):
        self.commits += 1
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _FakeDB:
    """Stellt genau das nach, was notify/resolve von der DB sehen: eine Zeile oder keine."""
    def __init__(self, row):
        self.cur = _FakeCursor(row)
    def connection(self):
        return _FakeConn(self.cur)


class EdgeTriggeredAlertTests(unittest.TestCase):
    """Einmal beim Auftreten, einmal bei Entwarnung, dazwischen Ruhe — und erneut
    erst bei einem NEUEN Vorfall. Vorher: Wiederholung alle ``cooldown_secs``,
    eine Nacht Studio-Ausfall = ein Push alle 30 Minuten."""

    NOW = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)

    def _notify(self, row):
        db = _FakeDB(row)
        with mock.patch.object(m, "create_operational_alert") as coa, \
             mock.patch.object(m, "_push_ntfy") as push:
            sent = m.notify(db, object(), key="studio-internet-down", severity=AlertSeverity.ERROR,
                            kind="studio-internet-down", title="Studio offline", detail="x",
                            cooldown_secs=30 * 60, now=self.NOW)
        return sent, coa.called, push.called, db

    def test_first_occurrence_alerts(self):
        sent, alerted, pushed, _ = self._notify({"times_sent": 1})
        self.assertTrue(sent and alerted and pushed)

    def test_ongoing_condition_stays_silent(self):
        """Die DB liefert keine Zeile = Zustand ist schon offen → kein zweiter Push."""
        sent, alerted, pushed, _ = self._notify(None)
        self.assertFalse(sent or alerted or pushed)

    def test_cooldown_no_longer_schedules_repeats(self):
        """Der Wiederholungspfad muss aus dem SQL RAUS sein — nicht nur selten werden."""
        _, _, _, db = self._notify({"times_sent": 1})
        sql, params = db.cur.executed[0]
        self.assertNotIn("interval '1 second'", sql)
        self.assertIn("resolved_at IS NOT NULL", sql)
        self.assertEqual(len(params), 5, "cooldown_secs darf nicht mehr ins SQL fliessen")

    def _resolve(self, row, settings=object(), **kw):
        db = _FakeDB(row)
        with mock.patch.object(m, "create_operational_alert") as coa, \
             mock.patch.object(m, "_push_ntfy") as push:
            announced = m.resolve(db, key="studio-internet-down", now=self.NOW, settings=settings, **kw)
        return announced, coa, push, db

    def test_resolution_is_announced_exactly_when_the_row_flips(self):
        row = {"kind": "studio-internet-down", "severity": "error",
               "first_seen_at": self.NOW - timedelta(hours=2), "times_sent": 1}
        announced, coa, push, _ = self._resolve(row)
        self.assertTrue(announced)
        self.assertEqual(coa.call_args.kwargs["severity"], AlertSeverity.INFO)
        self.assertFalse(coa.call_args.kwargs["send_telegram"])
        self.assertTrue(push.call_args.kwargs["resolved"])
        self.assertIn("Studio wieder online", push.call_args.kwargs["title"])
        self.assertIn("2 h 00 min", push.call_args.kwargs["detail"])

    def test_already_resolved_is_a_silent_noop(self):
        """resolve() laeuft jeden Worker-Zyklus — darf nur beim ECHTEN Wechsel reden."""
        announced, coa, push, db = self._resolve(None)
        self.assertFalse(announced or coa.called or push.called)
        self.assertEqual(len(db.cur.executed), 1)  # Zustand wird trotzdem geprueft/gesetzt

    def test_without_settings_state_is_closed_but_nothing_is_sent(self):
        row = {"kind": "nuki-hub-offline", "severity": "error",
               "first_seen_at": self.NOW - timedelta(minutes=5), "times_sent": 3}
        announced, coa, push, db = self._resolve(row, settings=None)
        self.assertFalse(announced or coa.called or push.called)
        self.assertEqual(len(db.cur.executed), 1)

    def test_unknown_kind_gets_a_generic_title(self):
        row = {"kind": "something-new", "severity": "warning",
               "first_seen_at": self.NOW - timedelta(minutes=30), "times_sent": 1}
        _, _, push, _ = self._resolve(row)
        self.assertEqual(push.call_args.kwargs["title"], "Entwarnung: something-new")

    def test_resolution_push_is_visibly_different(self):
        """Gruener Haken, normale Prioritaet, 'ERLEDIGT' im Titel — auf dem Sperrbildschirm
        muss die Entwarnung auf einen Blick vom Alarm unterscheidbar sein."""
        from types import SimpleNamespace
        settings = SimpleNamespace(ntfy_url="https://ntfy.sh", ntfy_topic="topic")
        with mock.patch("httpx.post") as post:
            m._push_ntfy(settings, severity=AlertSeverity.INFO, kind="studio-internet-down",
                         title="Studio wieder online", detail="Zustand bestand 2 h 00 min.",
                         resolved=True)
        headers = post.call_args.kwargs["headers"]
        self.assertTrue(headers["Title"].startswith("OpenGym ERLEDIGT"))
        self.assertIn("white_check_mark", headers["Tags"])
        self.assertEqual(headers["Priority"], "default")
        with mock.patch("httpx.post") as post:
            m._push_ntfy(settings, severity=AlertSeverity.ERROR, kind="studio-internet-down",
                         title="Studio offline", detail="")
        self.assertEqual(post.call_args.kwargs["headers"]["Priority"], "urgent")
