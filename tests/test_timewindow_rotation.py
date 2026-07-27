"""Rotation + assignment orchestration with in-memory fakes (no DB/Nuki/SMTP)."""
import unittest
from datetime import UTC, date, datetime
from unittest import mock

from nuki_integration.timewindow import rotation
from nuki_integration.timewindow import pin_pool

from support import InMemoryStore as MatStore, FakeNuki as MatNuki

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
        # Dry-Run-Vertrag: alle 101 Slots (96 Off-Peak + 5 Business-Hours-Fallback)
        # werden simuliert/materialisiert-geprüft, aber NICHTS wird gepusht/erzeugt.
        nuki = FakeNuki()
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY, dry_run=True)
        self.assertEqual(res["slots"], 101)
        self.assertEqual(res["pushed"], 0)           # DRY-RUN: nichts gepusht
        self.assertEqual(res["created"], 0)          # DRY-RUN: keine create-Calls
        self.assertEqual(res["materialised"], 101)   # simulated OK
        self.assertEqual(res["alerts"], 0)
        self.assertTrue(res["dry_run"])
        self.assertFalse(res["skipped"])
        self.assertEqual(nuki.creates, 0)            # DRY-RUN ruft create_keypad_code NICHT
        self.assertEqual(nuki.verifies, 101)         # aber prüft jede Materialisierung
        # Die 5 Fallback-Slots sind Teil der Rotation (og-bh-*).
        fb = sum(1 for (h, _p, _d) in self.store.pins if h == pin_pool.FALLBACK_HOUR)
        self.assertEqual(fb, 5)

    def test_rotate_daily_idempotent(self):
        nuki = FakeNuki()
        rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY, dry_run=True)
        again = rotation.rotate_daily(db=None, nuki=FakeNuki(), smartlock_id=0, day=DAY, dry_run=True)
        self.assertTrue(again["skipped"])


class FakeLiveNuki:
    """LIVE Nuki: keeps an in-memory auth list. ``create`` adds a materialised
    type-13 auth (non-null updateDate), ``delete`` removes it. Records deleted ids
    so the test can assert the daily rotation actually removed the predecessors.
    """
    def __init__(self, existing):
        # existing: list of (name, id, code) already on the keypad (yesterday's).
        self._auths = [
            {"name": n, "id": i, "code": c, "type": 13, "updateDate": "2026-07-05T00:00:00Z"}
            for (n, i, c) in existing
        ]
        self._next_id = 1000
        self.deleted = []
        self.created = []

    def list_keypad_codes(self):
        return [dict(a) for a in self._auths]

    def create_keypad_code(self, *, name, code, allowed_from, allowed_until,
                           allowed_week_days=127, allowed_from_time=None, allowed_until_time=None):
        self._next_id += 1
        new_id = self._next_id
        self._auths.append({"name": name, "id": new_id, "code": code, "type": 13,
                            "updateDate": "2026-07-06T00:00:00Z"})  # materialised at once
        self.created.append((name, new_id))
        return new_id

    def delete_keypad_code(self, *, auth_id):
        self.deleted.append(auth_id)
        self._auths = [a for a in self._auths if a["id"] != auth_id]

    def close(self):
        pass


class LiveRotationDeletesPredecessorsTests(unittest.TestCase):
    """Regression: the daily CREATE-FIRST rotation must delete the predecessor
    auths of BOTH the off-peak slots (og-hHH-pX) AND the 5 business-hours fallback
    slots (og-bh-pX). A too-narrow "og-h" capture filter would leave the 5 fallback
    predecessors on the keypad forever (stale-but-valid codes + Nuki 200-code limit).
    """
    def setUp(self):
        self.store = InMemoryStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # No real backoff/pauses in the live loop under test.
        self.pause = mock.patch.object(rotation, "WRITE_PAUSE_SECS", 0)
        self.pause.start()
        self.addCleanup(self.pause.stop)

    def test_live_rotation_deletes_offpeak_and_fallback_predecessors(self):
        # Yesterday's auths on the keypad: one off-peak slot + all 5 fallback slots.
        # Old codes contain '0' — gen_pin only emits 1-9, so they can never collide
        # with a freshly rotated code (keeps the assertions deterministic).
        offpeak_pred = ("og-h03-p0", 501, "650000")
        fallback_preds = [(f"og-bh-p{p}", 600 + p, f"70000{p}") for p in range(pin_pool.FALLBACK_POOL)]
        nuki = FakeLiveNuki([offpeak_pred, *fallback_preds])

        res = rotation.rotate_daily(
            db=None, nuki=nuki, smartlock_id=7, day=DAY, dry_run=False, force=True,
        )

        self.assertFalse(res["dry_run"])
        self.assertEqual(res["created"], 101)  # all 101 slots freshly created

        # Core regression: every predecessor — off-peak AND fallback — was deleted.
        expected_deleted = {501} | {600 + p for p in range(pin_pool.FALLBACK_POOL)}
        self.assertTrue(
            expected_deleted.issubset(set(nuki.deleted)),
            f"missing predecessor deletions: {expected_deleted - set(nuki.deleted)}",
        )

        # None of the old predecessors remain on the keypad (no slot leak).
        remaining_ids = {a["id"] for a in nuki.list_keypad_codes()}
        self.assertFalse(expected_deleted & remaining_ids)

        # Exactly the 5 (new) fallback auths remain, and no stale old fallback code
        # survives — the daily rotation guarantee now holds for og-bh-* too.
        remaining_bh = [a for a in nuki.list_keypad_codes() if a["name"].startswith("og-bh-")]
        self.assertEqual(len(remaining_bh), pin_pool.FALLBACK_POOL)
        old_fallback_codes = {c for (_n, _i, c) in fallback_preds}
        self.assertFalse({a["code"] for a in remaining_bh} & old_fallback_codes)


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

    def test_business_hours_assigns_fallback_code(self):
        # Innerhalb der Geschäftszeiten (Mo 10:00 Berlin) → einer der 5
        # Business-Hours-Fallback-Codes (og-bh-pX), KEIN og-hHH-Stundencode.
        email = FakeEmail()
        handled = []
        r = rotation.assign_and_deliver(
            db=None, window=self._window(8), email_service=email, smartlock_id=0,  # 10:00 Berlin
            day=DAY, mark_handled=lambda wid, **kw: handled.append((wid, kw)),
        )
        self.assertTrue(r["assigned"])
        self.assertFalse(r["no_code"])
        self.assertTrue(r["slot_name"].startswith("og-bh-"))
        self.assertIn(r["pool_index"], range(pin_pool.FALLBACK_POOL))
        self.assertEqual(email.sends, 1)       # Code vorhanden → Mail versucht
        self.assertEqual(len(self.store.assignments), 1)
        self.assertEqual(handled[0][0], 1)

    def test_anti_repeat_across_weeks(self):
        email = FakeEmail()
        picks = []
        for _ in range(pin_pool.POOL_PER_HOUR):
            r = rotation.assign_and_deliver(
                db=None, window=self._window(1), email_service=email, smartlock_id=0, day=DAY,
            )
            picks.append(r["pool_index"])
        self.assertEqual(sorted(picks), list(range(pin_pool.POOL_PER_HOUR)))


class FailClosedAssignTests(unittest.TestCase):
    """Fail-closed + Anti-Repeat: eine blockierte (nicht materialisierte) Zuweisung
    darf KEINE Assignment-Zeile schreiben — sonst gälte ein nie versendeter Code als
    der zuletzt zugestellte (und der Wächter würde den falschen re-materialisieren).
    """
    def setUp(self):
        self.store = MatStore()  # support-Store mit get_slot (für verify_slot_code)
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for p in range(pin_pool.POOL_PER_HOUR):     # Mon 03:00 Off-Peak-Slot p0..p3
            self.store.pins[(3, p, DAY)] = f"65432{p}"

    def _window(self):
        return {"id": 1, "member_id": 42, "email": "m@x.de",
                "first_name": "A", "last_name": "B",
                "starts_at": datetime(2026, 7, 6, 1, 0, tzinfo=UTC),   # 03:00 Berlin
                "ends_at": datetime(2026, 7, 6, 2, 0, tzinfo=UTC)}

    def test_blocked_dispatch_records_no_assignment(self):
        email = FakeEmail()
        nuki = MatNuki(materialised=False, covers=False, repair_succeeds=False)
        r = rotation.assign_and_deliver(
            db=None, window=self._window(), email_service=email, smartlock_id=0,
            day=DAY, nuki=nuki, settings=None,
        )
        self.assertFalse(r["assigned"])
        self.assertIs(r["verified"], False)
        self.assertEqual(email.sends, 0)                   # nichts versendet
        self.assertEqual(len(self.store.assignments), 0)   # kein "zuletzt zugestellt"

    def test_verified_dispatch_records_assignment(self):
        email = FakeEmail()
        nuki = MatNuki(materialised=True, covers=True)
        r = rotation.assign_and_deliver(
            db=None, window=self._window(), email_service=email, smartlock_id=0,
            day=DAY, nuki=nuki, settings=None,
        )
        self.assertTrue(r["assigned"])
        self.assertTrue(r["verified"])
        self.assertEqual(len(self.store.assignments), 1)


class FakeCapturingEmail:
    """Captures the kwargs of the last send_access_code call (delivery = success)."""
    def __init__(self, result=True):
        self._result = result
        self.sends = 0
        self.last_kwargs = None

    def send_access_code(self, **kwargs):
        self.sends += 1
        self.last_kwargs = kwargs
        return self._result


class BrandedAutoDeliveryTests(unittest.TestCase):
    """Regression (Bug 1): Der automatische Zeitfenster-Versand MUSS dasselbe
    gebrandete HTML-Zugangscode-Template inkl. Check-in-/Checks-URLs nutzen wie der
    manuelle Versand (services.access._send_code_email). Zuvor rief
    assign_and_deliver send_access_code OHNE html_body/checks_url/check_in_url auf ->
    Mitglieder erhielten nur die ungebrandete Plaintext-Mail.
    """
    def setUp(self):
        self.store = InMemoryStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        rotation.rotate_daily(db=None, nuki=FakeNuki(), smartlock_id=0, day=DAY, dry_run=True)

    def _window(self):
        # Mo 03:00 Berlin (Off-Peak) -> stundengenauer Slot; mit checks_key + email.
        return {
            "id": 7, "member_id": 42, "email": "m42@example.com",
            "first_name": "Alex", "last_name": "Muster", "checks_key": "ck-abc",
            "starts_at": datetime(2026, 7, 6, 1, 0, tzinfo=UTC),
            "ends_at": datetime(2026, 7, 6, 2, 0, tzinfo=UTC),
        }

    def test_auto_delivery_uses_branded_template_and_urls(self):
        email = FakeCapturingEmail(result=True)
        with mock.patch(
                "nuki_integration.services.email_builder.build_access_code_email_html",
                return_value="<html>BRANDED-ACCESS-CODE</html>") as m_html, \
             mock.patch(
                "nuki_integration.services.auth_tokens.build_checks_link",
                return_value="https://svc/checks?key=ck-abc") as m_checks, \
             mock.patch(
                "nuki_integration.services.auth_tokens.build_check_in_link",
                return_value="https://svc/check-in?token=tk"), \
             mock.patch(
                "nuki_integration.services.settings.get_effective_check_in_settings",
                return_value={"enabled": True}):
            r = rotation.assign_and_deliver(
                db=mock.Mock(name="db"), window=self._window(), email_service=email,
                smartlock_id=0, day=DAY, nuki=None, settings=mock.Mock(name="settings"),
            )

        self.assertTrue(r["assigned"])
        self.assertTrue(r["delivered"])
        self.assertEqual(email.sends, 1)
        kw = email.last_kwargs
        # 1) Gebrandetes HTML-Template ist verdrahtet (identischer Builder wie manuell).
        self.assertEqual(kw.get("html_body"), "<html>BRANDED-ACCESS-CODE</html>")
        # 2) Check-in- UND Checks-URLs sind gesetzt.
        self.assertEqual(kw.get("checks_url"), "https://svc/checks?key=ck-abc")
        self.assertEqual(kw.get("check_in_url"), "https://svc/check-in?token=tk")
        # 3) Der Builder erhielt member_name/code/validity/checks_url konsistent.
        m_html.assert_called_once()
        _, hkw = m_html.call_args
        self.assertEqual(hkw["code"], kw["code"])
        self.assertEqual(hkw["member_name"], kw["member_name"])
        self.assertEqual(hkw["checks_url"], kw["checks_url"])
        self.assertEqual(hkw["valid_from"], kw["valid_from"])
        self.assertEqual(hkw["valid_until"], kw["valid_until"])
        # 4) checks_url wurde aus dem window-checks_key gebaut.
        _, ckw = m_checks.call_args
        self.assertEqual(ckw["checks_key"], "ck-abc")
        self.assertEqual(ckw["member_id"], 42)

    def test_auto_delivery_formats_dates_like_manual_send(self):
        # Manueller Versand nutzt fmt_dt_de -> 'D. Monat JJJJ, HH:MM Uhr'.
        email = FakeCapturingEmail(result=True)
        with mock.patch(
                "nuki_integration.services.email_builder.build_access_code_email_html",
                return_value="<html>X</html>"), \
             mock.patch(
                "nuki_integration.services.auth_tokens.build_checks_link",
                return_value="u"), \
             mock.patch(
                "nuki_integration.services.settings.get_effective_check_in_settings",
                return_value={"enabled": False}):
            rotation.assign_and_deliver(
                db=mock.Mock(), window=self._window(), email_service=email,
                smartlock_id=0, day=DAY, nuki=None, settings=mock.Mock(),
            )
        kw = email.last_kwargs
        self.assertIn("Juli", kw["valid_from"])
        self.assertIn("Uhr", kw["valid_from"])
        # check-in disabled -> keine check_in_url
        self.assertIsNone(kw.get("check_in_url"))


if __name__ == "__main__":
    unittest.main()
