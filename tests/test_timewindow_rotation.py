"""Rotation + assignment orchestration with in-memory fakes (no DB/Nuki/SMTP)."""
import unittest
from datetime import UTC, date, datetime
from unittest import mock

from nuki_integration.timewindow import rotation
from nuki_integration.timewindow import pin_pool

DAY = date(2026, 7, 6)  # Monday


class InMemoryStore:
    """Duck-typed replacement for timewindow.store (module-level functions)."""
    def __init__(self):
        self.slots = {}          # (hour,pool_index) -> id
        self.pins = {}           # (hour,pool_index,date) -> pin
        self.assignments = []    # dicts
        self._seq = 0

    def ensure_schema(self, db):  # noqa: ARG002
        pass

    def rotation_count_for_day(self, db, day):  # noqa: ARG002
        return sum(1 for (_h, _p, d) in self.pins if d == day)

    def upsert_slot(self, db, *, smartlock_id, name, hour, pool_index, weekday_mask, from_time, until_time):  # noqa: ARG002
        key = (hour, pool_index)
        if key not in self.slots:
            self._seq += 1
            self.slots[key] = self._seq
        return self.slots[key]

    def set_slot_auth_id(self, db, slot_id, nuki_auth_id):  # noqa: ARG002
        pass

    def record_rotation(self, db, *, slot_id, rotation_date, pin, pushed, materialised, dry_run):  # noqa: ARG002
        hour, pidx = next(k for k, v in self.slots.items() if v == slot_id)
        self.pins[(hour, pidx, rotation_date)] = pin

    def get_todays_slot_pin(self, db, *, smartlock_id, hour, pool_index, rotation_date):  # noqa: ARG002
        return self.pins.get((hour, pool_index, rotation_date))

    def recent_pool_indices(self, db, *, member_ref, weekday, hour, limit=4):  # noqa: ARG002
        rows = [a["pool_index"] for a in self.assignments
                if a["member_ref"] == member_ref and a["weekday"] == weekday and a["hour"] == hour]
        return rows[-limit:]

    def record_assignment(self, db, *, member_ref, weekday, hour, pool_index, assigned_date):  # noqa: ARG002
        self.assignments.append(dict(member_ref=member_ref, weekday=weekday, hour=hour,
                                     pool_index=pool_index, assigned_date=assigned_date))

    def rotation_status(self, db, day):  # noqa: ARG002
        return {"slots": len(self.slots), "rotated_today": self.rotation_count_for_day(db, day)}


class FakeNuki:
    """DRY-RUN Nuki: no push, materialisation simulated OK."""
    def __init__(self):
        self.creates = 0
        self.verifies = 0
        self.last_kwargs = None

    def create_keypad_code(self, **kwargs):
        self.creates += 1
        self.last_kwargs = kwargs
        return None  # DRY-RUN → no auth id (not pushed)

    def verify_materialization(self, code):
        self.verifies += 1
        return {"materialised": True, "simulated": True, "auth_id": None, "update_date": None}

    def close(self):
        pass


class FakeEmail:
    def __init__(self):
        self.sends = 0

    def send_access_code(self, **kwargs):
        self.sends += 1
        return False  # SMTP not configured → no mail sent


class RotateDailyTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_rotate_daily_dry_run(self):
        nuki = FakeNuki()
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY, dry_run=True)
        self.assertEqual(res["slots"], 96)
        self.assertEqual(res["pushed"], 0)          # DRY-RUN: nothing pushed
        self.assertEqual(res["materialised"], 96)   # simulated OK
        self.assertEqual(res["alerts"], 0)
        self.assertEqual(nuki.creates, 96)
        self.assertEqual(nuki.verifies, 96)
        # time-window fields present on the push payload
        self.assertIn("allowed_from_time", nuki.last_kwargs)
        self.assertIn("allowed_week_days", nuki.last_kwargs)

    def test_rotate_daily_idempotent(self):
        nuki = FakeNuki()
        rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY, dry_run=True)
        again = rotation.rotate_daily(db=None, nuki=FakeNuki(), smartlock_id=0, day=DAY, dry_run=True)
        self.assertTrue(again["skipped"])


class AssignDeliverTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        rotation.rotate_daily(db=None, nuki=FakeNuki(), smartlock_id=0, day=DAY, dry_run=True)

    def _window(self, hour_utc):
        # July → Berlin = UTC+2. hour_utc=1 → 03:00 Berlin (off-peak Mon).
        return {
            "id": 1, "member_id": 42, "email": "m42@example.com",
            "first_name": "Alex", "last_name": "Muster",
            "starts_at": datetime(2026, 7, 6, hour_utc, 0, tzinfo=UTC),
            "ends_at": datetime(2026, 7, 6, hour_utc + 1, 0, tzinfo=UTC),
        }

    def test_offpeak_assigns_and_no_mail_in_dry(self):
        email = FakeEmail()
        handled = []
        r = rotation.assign_and_deliver(
            db=None, window=self._window(1), email_service=email, smartlock_id=0,
            day=DAY, mark_handled=lambda wid, **kw: handled.append((wid, kw)),
        )
        self.assertTrue(r["assigned"])
        self.assertFalse(r["no_code"])
        self.assertFalse(r["delivered"])       # SMTP off → 0 mails
        self.assertEqual(email.sends, 1)       # attempted, returned False
        self.assertEqual(len(self.store.assignments), 1)
        self.assertEqual(handled[0][0], 1)

    def test_business_hours_no_code(self):
        email = FakeEmail()
        handled = []
        r = rotation.assign_and_deliver(
            db=None, window=self._window(8), email_service=email, smartlock_id=0,  # 10:00 Berlin
            day=DAY, mark_handled=lambda wid, **kw: handled.append((wid, kw)),
        )
        self.assertTrue(r["no_code"])
        self.assertFalse(r["assigned"])
        self.assertEqual(email.sends, 0)       # no code → no mail
        self.assertEqual(len(self.store.assignments), 0)

    def test_anti_repeat_across_weeks(self):
        email = FakeEmail()
        picks = []
        for _ in range(pin_pool.POOL_PER_HOUR):
            r = rotation.assign_and_deliver(
                db=None, window=self._window(1), email_service=email, smartlock_id=0, day=DAY,
            )
            picks.append(r["pool_index"])
        self.assertEqual(sorted(picks), list(range(pin_pool.POOL_PER_HOUR)))


if __name__ == "__main__":
    unittest.main()
