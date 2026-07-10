"""Nuki error-event guardian: auth, any-authenticated-payload reconcile trigger,
idempotency, rate-limit, worker fallback, Berlin-date PIN lookup and the
reconcile repair loop — all with in-memory fakes (no DB / no network / no secrets).
"""
import hashlib
import hmac
import json
import unittest
from datetime import UTC, datetime, timedelta

from nuki_integration.config import get_settings
from nuki_integration.services import nuki_guardian as ng

from support import FakeNuki, InMemoryStore

SECRET = "test-webhook-secret"  # matches tests/conftest.py NUKI_WEBHOOK_SECRET
NOW = datetime(2026, 7, 6, 1, 0, tzinfo=UTC)  # Mon 03:00 Berlin (CEST)


def _hmac_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Pure authentication ──────────────────────────────────────────────────────
class VerifyWebhookTests(unittest.TestCase):
    def test_no_secret_fails_closed(self):
        self.assertFalse(ng.verify_webhook(secret="", raw_body=b"{}", token="anything"))
        self.assertFalse(ng.verify_webhook(secret="", raw_body=b"{}",
                                           signature=_hmac_hex("x", b"{}")))

    def test_token_valid_and_invalid(self):
        self.assertTrue(ng.verify_webhook(secret=SECRET, raw_body=b"{}", token=SECRET))
        self.assertFalse(ng.verify_webhook(secret=SECRET, raw_body=b"{}", token="nope"))
        self.assertFalse(ng.verify_webhook(secret=SECRET, raw_body=b"{}"))  # neither cred

    def test_hmac_hex_valid_invalid_and_prefix(self):
        body = b'{"event":"x"}'
        good = _hmac_hex(SECRET, body)
        self.assertTrue(ng.verify_webhook(secret=SECRET, raw_body=body, signature=good))
        self.assertTrue(ng.verify_webhook(secret=SECRET, raw_body=body, signature="sha256=" + good))
        self.assertTrue(ng.verify_webhook(secret=SECRET, raw_body=body, signature=good.upper()))
        self.assertFalse(ng.verify_webhook(secret=SECRET, raw_body=body, signature="deadbeef"))
        # signature over a different body must fail
        self.assertFalse(ng.verify_webhook(secret=SECRET, raw_body=b"{}", signature=good))


# ── handle_nuki_webhook: auth + any-payload reconcile + idempotency + rate-limit ─
class _SlotGate:
    """Fake atomic run-slot honouring the cooldown, without a DB."""
    def __init__(self):
        self.last = None

    def __call__(self, db, *, now, cooldown_seconds):  # noqa: ARG002
        if self.last is not None and (now - self.last).total_seconds() < cooldown_seconds:
            return False
        self.last = now
        return True


class _ReconcileSpy:
    def __init__(self, failures=0):
        self.calls = 0
        self.failures = failures

    def __call__(self, db, settings, nuki_cfg, *, now):  # noqa: ARG002
        self.calls += 1
        return {"relevant_windows": 1, "checked": 1, "repaired": 0,
                "failures": self.failures, "skipped": 0, "details": []}


class FakeGuardianDb:
    def __init__(self):
        self._events = set()

    def record_webhook_event(self, *, provider, event_id, event_type, payload):  # noqa: ARG002
        if event_id in self._events:
            return False
        self._events.add(event_id)
        return True

    def get_system_setting(self, key):  # noqa: ARG002
        return None


class HandleWebhookTests(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self.db = FakeGuardianDb()
        self.gate = _SlotGate()
        self.spy = _ReconcileSpy()
        self.audits = []
        patchers = [
            ("ensure_schema", lambda db: None),
            ("get_effective_nuki_config",
             lambda db, s: {"nuki_webhook_secret": SECRET, "nuki_smartlock_id": 0,
                            "nuki_dry_run": True, "nuki_api_token": ""}),
            ("try_acquire_run_slot", self.gate),
            ("_run_reconcile", self.spy),
            ("record_audit", lambda db, **kw: self.audits.append(kw)),
            ("create_operational_alert", lambda **kw: None),
        ]
        import unittest.mock as mock
        for name, value in patchers:
            p = mock.patch.object(ng, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _call(self, body: bytes, *, token=SECRET, signature=None, headers=None, now=NOW):
        return ng.handle_nuki_webhook(
            self.db, self.settings, raw_body=body, signature=signature,
            token=token, headers=headers, now=now,
        )

    def test_invalid_token_rejected_no_reconcile(self):
        r = self._call(b'{"logs":[]}', token="wrong")
        self.assertFalse(r["accepted"])
        self.assertEqual(self.spy.calls, 0)

    def test_arbitrary_authenticated_payload_triggers_reconcile(self):
        # No error keywords, no numeric error code — must STILL reconcile.
        r = self._call(json.dumps({"logs": [{"action": 1, "state": 0}]}).encode())
        self.assertTrue(r["accepted"])
        self.assertTrue(r["reconciled"])
        self.assertEqual(r["error_events"], 0)
        self.assertEqual(self.spy.calls, 1)
        self.assertEqual(self.audits[-1]["trigger_kind"], "webhook")

    def test_classified_error_is_marked_but_not_required(self):
        r = self._call(json.dumps({"logs": [{"msg": "wrong keypad code"}]}).encode())
        self.assertTrue(r["reconciled"])
        self.assertEqual(r["error_events"], 1)
        self.assertEqual(self.audits[-1]["trigger_kind"], "keypad-error")

    def test_idempotent_duplicate_event(self):
        body = json.dumps({"logs": [{"x": 1}]}).encode()
        first = self._call(body)
        second = self._call(body)  # same body → same derived event_id
        self.assertTrue(first["reconciled"])
        self.assertTrue(second.get("duplicate"))
        self.assertFalse(second["reconciled"])
        self.assertEqual(self.spy.calls, 1)  # reconcile ran exactly once

    def test_rate_limited_within_cooldown(self):
        r1 = self._call(json.dumps({"a": 1}).encode(), now=NOW)
        r2 = self._call(json.dumps({"b": 2}).encode(), now=NOW + timedelta(seconds=5))
        self.assertTrue(r1["reconciled"])
        self.assertTrue(r2["rate_limited"])
        self.assertFalse(r2["reconciled"])
        self.assertEqual(self.spy.calls, 1)

    def test_bearer_style_token_via_verify(self):
        # The app extracts the bearer value; here we assert the underlying accept.
        r = self._call(b'{"any":1}', token=SECRET)
        self.assertTrue(r["accepted"])


# ── Worker fallback ──────────────────────────────────────────────────────────
class WorkerFallbackTests(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self.gate = _SlotGate()
        self.spy = _ReconcileSpy()
        self.audits = []
        import unittest.mock as mock
        for name, value in [
            ("ensure_schema", lambda db: None),
            ("get_effective_nuki_config", lambda db, s: {"nuki_smartlock_id": 0}),
            ("try_acquire_run_slot", self.gate),
            ("_run_reconcile", self.spy),
            ("record_audit", lambda db, **kw: self.audits.append(kw)),
            ("create_operational_alert", lambda **kw: None),
        ]:
            p = mock.patch.object(ng, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_fallback_reconciles_without_webhook(self):
        r = ng.run_guardian_cycle(None, self.settings, now=NOW)
        self.assertTrue(r["reconciled"])
        self.assertEqual(self.spy.calls, 1)
        self.assertEqual(self.audits[-1]["trigger_kind"], "worker-fallback")

    def test_fallback_rate_limited_within_interval(self):
        ng.run_guardian_cycle(None, self.settings, now=NOW)
        r2 = ng.run_guardian_cycle(None, self.settings, now=NOW + timedelta(seconds=100))
        self.assertTrue(r2["rate_limited"])
        self.assertFalse(r2["reconciled"])
        self.assertEqual(self.spy.calls, 1)  # interval default 900s → no second run


# ── reconcile_relevant_bookings: repair loop + Berlin-local date ─────────────
class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.mem = InMemoryStore()
        import unittest.mock as mock
        p = mock.patch.object(ng, "store", self.mem)
        p.start()
        self.addCleanup(p.stop)

    def _window(self, starts_at):
        return {"id": 1, "member_id": 42, "starts_at": starts_at,
                "ends_at": starts_at + timedelta(hours=1), "email": "m@x.de",
                "first_name": "A", "last_name": "B"}

    def test_repairs_last_assigned_code_and_uses_berlin_date(self):
        # 22:30 UTC in July = 00:30 next day Berlin → Berlin date is 2026-07-07.
        now = datetime(2026, 7, 6, 22, 30, tzinfo=UTC)
        berlin_day = datetime(2026, 7, 7).date()
        starts_at = now  # booking in progress; Berlin 00:30 → weekday=1 (Tue), hour=0
        self.mem.set_relevant_windows([self._window(starts_at)])
        # Member's most-recently-assigned pool index for this (weekday, off-peak hour).
        self.mem.record_assignment(None, member_ref="42", weekday=1, hour=0,
                                   pool_index=2, assigned_date=berlin_day)
        # Today's rotated PIN exists ONLY under the Berlin date, not the UTC date.
        self.mem.pins[(0, 2, berlin_day)] = "654321"

        nuki = FakeNuki(materialised=False, covers=False, repair_succeeds=True)
        result = ng.reconcile_relevant_bookings(None, nuki=nuki, smartlock_id=0, now=now)

        self.assertEqual(result["relevant_windows"], 1)
        self.assertEqual(result["checked"], 1)   # PIN found ⇒ Berlin date was used
        self.assertEqual(result["repaired"], 1)  # missing code repaired + revalidated
        self.assertEqual(result["failures"], 0)
        self.assertEqual(len(nuki.creates), 1)   # kept SAME code, created once

    def test_skips_member_never_sent_a_code(self):
        now = NOW
        self.mem.set_relevant_windows([self._window(now)])  # no prior assignment
        nuki = FakeNuki(materialised=True, covers=True)
        result = ng.reconcile_relevant_bookings(None, nuki=nuki, smartlock_id=0, now=now)
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
