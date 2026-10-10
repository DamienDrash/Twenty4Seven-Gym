"""Magicline-Aussetzer dürfen den Worker-Zyklus nicht abbrechen (Vorfall 10.10.2026).

Ein einzelnes "[Errno 101] Network is unreachable" beim Magicline-Sync ließ den
ganzen Zyklus ausfallen: keine Code-Zustellung, kein Wächter, keine
Keypad-Überwachung — und healthchecks.io meldete den Worker als DOWN.
"""
import logging
import unittest
from unittest import mock

import httpx

import nuki_integration.worker as worker
from nuki_integration import magicline
from nuki_integration.exceptions import MagiclineApiError

LOG = logging.getLogger("test-sync-resilience")


class _FakeDB:
    def expire_finished_windows(self, now):  # noqa: ARG002
        return 0


def _tw_result():
    return {"rotation": {"slots": 101}, "assigned": 0, "no_code": 0,
            "delivered": 0, "blocked": 0, "pushed": 0, "dry_run": False}


def _patched_cycle(sync, calls):
    def tw(db, s):
        calls.append("tw")
        return _tw_result()

    def guardian(db, s):
        calls.append("guardian")
        return {"reconciled": 0, "repaired": 0}

    def monitoring(db, s):
        calls.append("monitoring")

    return [
        mock.patch.object(worker, "sync_magicline_bookings", sync),
        mock.patch.object(worker, "run_timewindow_cycle", tw),
        mock.patch.object(worker, "run_guardian_cycle", guardian),
        mock.patch.object(worker.monitoring, "run_worker_monitoring", monitoring),
        mock.patch.object(worker, "deprovision_expired_codes", lambda db, s: 0),
        mock.patch.object(worker, "cleanup_orphaned_nuki_codes", lambda db, s: 0),
    ]


class CycleContinuesWithoutMagiclineTests(unittest.TestCase):
    def test_sync_failure_still_runs_delivery_guardian_and_monitoring(self):
        calls = []

        def broken_sync(db, s):
            raise MagiclineApiError("Magicline request failed: [Errno 101] Network is unreachable")

        patches = _patched_cycle(broken_sync, calls)
        for p in patches:
            p.start()
        try:
            result = worker.run_cycle(_FakeDB(), settings=object(), logger=LOG)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(calls, ["tw", "guardian", "monitoring"])
        self.assertIn("Network is unreachable", result["sync"]["error"])
        self.assertEqual(result["sync"]["windows"], 0)

    def test_unexpected_errors_still_propagate(self):
        """Nur Magicline-Fehler werden geschluckt — ein Bug bleibt laut."""
        def buggy_sync(db, s):
            raise KeyError("bug")

        patches = _patched_cycle(buggy_sync, [])
        for p in patches:
            p.start()
        try:
            with self.assertRaises(KeyError):
                worker.run_cycle(_FakeDB(), settings=object(), logger=LOG)
        finally:
            for p in patches:
                p.stop()


class DeadmanStreakTests(unittest.TestCase):
    def _run(self, results):
        pings = []

        class _Stop(Exception):
            pass

        seq = iter(results)
        remaining = [len(results)]

        def fake_cycle(db, s, l):
            return next(seq)

        def fake_sleep(sec):
            # Abbruch muss AUSSERHALB des try/except im Worker passieren,
            # sonst schluckt der Worker ihn und läuft endlos weiter.
            remaining[0] -= 1
            if remaining[0] == 0:
                raise _Stop

        def fake_ping(settings, suffix="", payload=None):
            if suffix != "start":
                pings.append(suffix or "ok")
            return True

        fake_settings = mock.Mock(log_level="INFO", database_url="db://x",
                                  magicline_sync_interval_minutes=5)
        with mock.patch.object(worker, "get_settings", lambda: fake_settings), \
             mock.patch.object(worker, "configure_logging", lambda level: None), \
             mock.patch.object(worker, "Database") as DB, \
             mock.patch.object(worker, "run_cycle", fake_cycle), \
             mock.patch.object(worker.deadman, "ping", fake_ping), \
             mock.patch.object(worker.time, "sleep", fake_sleep):
            DB.return_value = mock.Mock()
            with self.assertRaises(_Stop):
                worker.run_forever()
        return pings

    def test_single_sync_failure_stays_green(self):
        bad = {"sync": {"windows": 0, "error": "x"}}
        good = {"sync": {"windows": 0}}
        self.assertEqual(self._run([good, bad, good]), ["ok", "ok", "ok"])

    def test_persistent_sync_failure_turns_red_and_recovers(self):
        bad = {"sync": {"windows": 0, "error": "x"}}
        good = {"sync": {"windows": 0}}
        n = worker.SYNC_FAIL_ALERT_AFTER
        pings = self._run([bad] * (n + 1) + [good])
        self.assertEqual(pings, ["ok"] * (n - 1) + ["fail", "fail", "ok"])


class MagiclineConnectRetryTests(unittest.TestCase):
    def _client(self):
        settings = mock.Mock(magicline_base_url="https://example.invalid", magicline_api_key="k")
        return magicline.MagiclineClient(settings)

    def test_transient_connect_error_is_retried(self):
        c = self._client()
        ok = httpx.Response(200, json={"ok": True})
        side = [httpx.ConnectError("[Errno 101] Network is unreachable"), ok]
        with mock.patch.object(c._client, "request", side_effect=side) as req, \
             mock.patch.object(magicline.time, "sleep") as sl:
            self.assertEqual(c._request("GET", "/v1/customers"), {"ok": True})
        self.assertEqual(req.call_count, 2)
        sl.assert_called_once()

    def test_gives_up_after_retries(self):
        c = self._client()
        err = httpx.ConnectError("down")
        with mock.patch.object(c._client, "request", side_effect=[err, err, err]) as req, \
             mock.patch.object(magicline.time, "sleep"):
            with self.assertRaises(MagiclineApiError):
                c._request("GET", "/v1/customers")
        self.assertEqual(req.call_count, 3)

    def test_read_timeout_is_not_retried(self):
        """Der Request kann angekommen sein — keine blinde Wiederholung."""
        c = self._client()
        with mock.patch.object(c._client, "request", side_effect=httpx.ReadTimeout("slow")) as req, \
             mock.patch.object(magicline.time, "sleep"):
            with self.assertRaises(MagiclineApiError):
                c._request("POST", "/v1/x", json_body={})
        self.assertEqual(req.call_count, 1)


if __name__ == "__main__":
    unittest.main()
