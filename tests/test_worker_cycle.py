"""Regression tests for the worker cadence + post-sync immediate processing.

Damien change: Magicline sync every 5 minutes (was 30) AND newly-due access
windows/codes are processed/sent in the SAME tick as the sync (not a later one).
"""
import logging
import unittest
from unittest import mock

import nuki_integration.worker as worker
from nuki_integration.config import Settings

LOG = logging.getLogger("test-worker")


class _FakeDB:
    def expire_finished_windows(self, now):  # noqa: ARG002
        return 0  # >0 would trigger lock_if_no_active_sessions; keep it out of the way


def _tw_result():
    return {"rotation": {"slots": 101}, "assigned": 0, "no_code": 0,
            "delivered": 0, "blocked": 0, "pushed": 0, "dry_run": False}


class SyncIntervalTests(unittest.TestCase):
    def test_default_interval_is_5_minutes(self):
        # Prod worker relies on the config default (compose sets no override).
        self.assertEqual(
            Settings.model_fields["magicline_sync_interval_minutes"].default, 5)

    def test_run_forever_sleeps_configured_interval_in_seconds(self):
        slept = []

        class _Break(Exception):
            pass

        def fake_sleep(sec):
            slept.append(sec)
            raise _Break  # break out of the infinite loop after one iteration

        fake_settings = mock.Mock(log_level="INFO", database_url="db://x",
                                  magicline_sync_interval_minutes=5)
        with mock.patch.object(worker, "get_settings", lambda: fake_settings), \
             mock.patch.object(worker, "configure_logging", lambda level: None), \
             mock.patch.object(worker, "Database") as DB, \
             mock.patch.object(worker, "run_cycle", lambda db, s, l: {}), \
             mock.patch.object(worker.time, "sleep", fake_sleep):
            DB.return_value = mock.Mock()
            with self.assertRaises(_Break):
                worker.run_forever()
        self.assertEqual(slept, [5 * 60])  # 300s, not 1800s


class PostSyncProcessingTests(unittest.TestCase):
    def test_cycle_processes_due_windows_immediately_after_sync(self):
        order = []

        def fake_sync(db, s):
            order.append("sync")
            return {"windows": 3}

        def fake_tw(db, s):
            order.append("process")   # run_timewindow_cycle assigns/dispatches due windows
            return _tw_result()

        def fake_guardian(db, s):
            order.append("guardian")
            return {"reconciled": 0, "repaired": 0}

        with mock.patch.object(worker, "sync_magicline_bookings", fake_sync), \
             mock.patch.object(worker, "run_timewindow_cycle", fake_tw), \
             mock.patch.object(worker, "run_guardian_cycle", fake_guardian), \
             mock.patch.object(worker, "deprovision_expired_codes", lambda db, s: 0), \
             mock.patch.object(worker, "cleanup_orphaned_nuki_codes", lambda db, s: 0):
            result = worker.run_cycle(_FakeDB(), settings=object(), logger=LOG)

        # sync happens, then processing happens in the same cycle, before guardian.
        self.assertIn("sync", order)
        self.assertIn("process", order)
        self.assertLess(order.index("sync"), order.index("process"),
                        "due windows must be processed AFTER the sync, same tick")
        self.assertLess(order.index("process"), order.index("guardian"))
        self.assertEqual(result["sync"]["windows"], 3)


if __name__ == "__main__":
    unittest.main()
