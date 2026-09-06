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
        self.verified = {}   # (hour,pool,date) -> Zeitpunkt des Geraetebeweises

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

    # -- Geraetebeweis (device attestation) --
    def get_todays_slot_pin_row(self, db, *, smartlock_id, hour, pool_index, rotation_date):  # noqa: ARG002
        pin = self.pins.get((hour, pool_index, rotation_date))
        if pin is None:
            return None
        key = (hour, pool_index, rotation_date)
        return {"id": key, "pin": pin, "rotation_date": rotation_date,
                "device_verified_at": self.verified.get(key)}

    def mark_pin_device_verified(self, db, *, pin_history_id, pin):  # noqa: ARG002
        # Wie in der echten Query: nur stempeln, wenn der PIN noch derselbe ist.
        if self.pins.get(pin_history_id) == pin:
            self.verified[pin_history_id] = "2026-09-06T12:00:00Z"

    def pins_needing_attestation(self, db, *, smartlock_id, rotation_date,  # noqa: ARG002
                                 limit=5, stale_after_hours=12):
        out = []
        for (hour, pidx, d), pin in self.pins.items():
            if d != rotation_date or self.verified.get((hour, pidx, d)) is not None:
                continue
            sid = self.slots.get((hour, pidx))
            meta = getattr(self, "slot_meta", {}).get(sid) or {}
            out.append({"id": (hour, pidx, d), "pin": pin, "device_verified_at": None,
                        "hour": hour, "pool_index": pidx,
                        "name": meta.get("name") or f"og-h{hour:02d}-p{pidx}"})
        return out[:limit]


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
        # Dry-Run-Vertrag: alle 53 Slots (48 Off-Peak + 5 Business-Hours-Fallback)
        # werden simuliert/materialisiert-geprüft, aber NICHTS wird gepusht/erzeugt.
        nuki = FakeNuki()
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY, dry_run=True)
        self.assertEqual(res["slots"], 53)
        self.assertEqual(res["pushed"], 0)           # DRY-RUN: nichts gepusht
        self.assertEqual(res["created"], 0)          # DRY-RUN: keine create-Calls
        self.assertEqual(res["materialised"], 53)   # simulated OK
        self.assertEqual(res["alerts"], 0)
        self.assertTrue(res["dry_run"])
        self.assertFalse(res["skipped"])
        self.assertEqual(nuki.creates, 0)            # DRY-RUN ruft create_keypad_code NICHT
        self.assertEqual(nuki.verifies, 53)         # aber prüft jede Materialisierung
        # Die 5 Fallback-Slots sind Teil der Rotation (og-bh-*).
        fb = sum(1 for (h, _p, _d) in self.store.pins if h == pin_pool.FALLBACK_HOUR)
        self.assertEqual(fb, 5)

    def test_rotate_daily_idempotent(self):
        nuki = FakeNuki()
        rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY, dry_run=True)
        again = rotation.rotate_daily(db=None, nuki=FakeNuki(), smartlock_id=0, day=DAY, dry_run=True)
        self.assertTrue(again["skipped"])

    def test_rotate_daily_paused_makes_no_lock_changes(self):
        # NUKI_ROTATION_PAUSED: freeze the lock during a studio-internet outage. No
        # create/verify calls, no new pins written — the keypad codes stay untouched.
        nuki = FakeNuki()
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY,
                                    dry_run=False, paused=True)
        self.assertTrue(res["paused"])
        self.assertTrue(res["skipped"])
        self.assertEqual(nuki.creates, 0)          # nothing created on the lock
        self.assertEqual(nuki.verifies, 0)         # no device round-trips
        self.assertEqual(len(self.store.pins), 0)  # no new pins generated

    def test_paused_takes_precedence_over_force(self):
        # A forced run must still not mutate the offline lock while paused.
        nuki = FakeNuki()
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY,
                                    dry_run=False, force=True, paused=True)
        self.assertTrue(res["paused"])
        self.assertEqual(nuki.creates, 0)


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


class FakeLiveNukiUnconfirmed(FakeLiveNuki):
    """Models a DEGRADED device→cloud link: ``create`` puts the code on the lock (it is
    present + usable) but it is never device-confirmed (``updateDate`` stays None)."""
    def create_keypad_code(self, *, name, code, **kw):  # noqa: ARG002
        self._next_id += 1
        new_id = self._next_id
        self._auths.append({"name": name, "id": new_id, "code": code, "type": 13,
                            "updateDate": None})  # present but NOT device-confirmed
        self.created.append((name, new_id))
        return new_id


class UnconfirmedRotationTests(unittest.TestCase):
    """Iteration 2: on a degraded link the rotation must NOT hang ~75 s/slot waiting for a
    confirmation that never comes. It waits for PRESENCE (fast), records materialised=0 as
    a health signal, and raises NO per-slot alert (the codes ARE on the lock)."""
    def setUp(self):
        self.store = InMemoryStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.pause = mock.patch.object(rotation, "WRITE_PAUSE_SECS", 0)
        self.pause.start(); self.addCleanup(self.pause.stop)

    def test_present_but_unconfirmed_completes_without_alerts(self):
        nuki = FakeLiveNukiUnconfirmed([])
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=0, day=DAY,
                                    dry_run=False, force=True)
        self.assertEqual(res["pushed"], 53)       # all codes created/pushed to the lock
        self.assertEqual(res["materialised"], 0)   # none device-confirmed (health signal only)
        self.assertEqual(res["alerts"], 0)         # NO per-slot alert — the codes are present
        self.assertEqual(len(nuki.created), 53)


class FakeLiveNukiCreateFails(FakeLiveNuki):
    """Models the 2026-08-01 failure: create_keypad_code is accepted (no exception) but the
    code never appears on the lock (present=False) — the rotation must then KEEP the old
    predecessor codes instead of deleting them into an access gap."""
    def create_keypad_code(self, *, name, code, **kw):  # noqa: ARG002
        self.created.append((name, None))
        return None  # never added to _auths → _wait_present() times out → present=False


class SafeDeleteWhenCreateFailsTests(unittest.TestCase):
    """Safety (iter 4): if the new code did not land on the lock, the working predecessor
    must be kept — never delete a working code without a live successor."""
    def setUp(self):
        self.store = InMemoryStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.pause = mock.patch.object(rotation, "WRITE_PAUSE_SECS", 0)
        self.pause.start(); self.addCleanup(self.pause.stop)
        # _wait_present would otherwise poll 15s/slot × 101 → mock it to "never present".
        self.wp = mock.patch.object(rotation, "_wait_present", lambda *a, **k: None)
        self.wp.start(); self.addCleanup(self.wp.stop)

    def test_predecessors_kept_when_new_code_never_present(self):
        preds = [("og-h03-p0", 501, "650000")] + [
            (f"og-bh-p{p}", 600 + p, f"70000{p}") for p in range(pin_pool.FALLBACK_POOL)]
        nuki = FakeLiveNukiCreateFails(preds)
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=7, day=DAY,
                                    dry_run=False, force=True)
        self.assertEqual(nuki.deleted, [])            # NOTHING deleted → no lockout
        self.assertEqual(res["alerts"], 53)           # every slot flagged as create miss
        remaining = {a["id"] for a in nuki.list_keypad_codes()}
        self.assertTrue({501, 600}.issubset(remaining))  # working predecessors still present


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
        self.assertEqual(res["created"], 53)   # all 53 slots freshly created

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

    def test_outage_detector_withholds_unconfirmed_cloud_code(self):
        # Code is present on the lock with the right window but NOT device-confirmed
        # (Cloud↔Lock freeze). It must NOT be dispatched — it would be a dead code.
        email = FakeEmail()
        nuki = MatNuki(materialised=False, covers=True, exists=True)
        r = rotation.assign_and_deliver(
            db=None, window=self._window(), email_service=email, smartlock_id=0,
            day=DAY, nuki=nuki, settings=None,           # settings=None → default safe
        )
        self.assertFalse(r["assigned"])
        self.assertEqual(r["reason"], "unconfirmed")
        self.assertEqual(email.sends, 0)                  # no dead code to the member
        self.assertEqual(len(self.store.assignments), 0)  # booking stays due → retried


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


class FakeLiveNukiDeviceAck(FakeLiveNuki):
    """Hub-Transport: das Schloss quittiert jeden Write auf ``commandResultJson``.

    Die publizierte Liste zeigt eingefroren den Stand VOR dem Lauf: ein frisch
    angelegter Code taucht dort erst auf, wenn der Hub das Keypad das naechste Mal
    ausliest (Intervall 1800 s), und ein geloeschter verschwindet gar nicht — sein
    Einzel-Topic bleibt als Retain liegen. Die Quittung dagegen ist Sekunden nach dem
    Write da. Am 06.09.2026 war die Liste sogar dauerhaft leer (``keypad/json: null``).
    """
    def __init__(self, existing):
        super().__init__(existing)
        self._confirmed = False
        self._snapshot = [dict(a) for a in self._auths]

    def list_keypad_codes(self):
        return [dict(a) for a in self._snapshot]

    def create_keypad_code(self, **kw):
        new_id = super().create_keypad_code(**kw)
        self._confirmed = True
        return new_id

    def delete_keypad_code(self, *, auth_id):
        super().delete_keypad_code(auth_id=auth_id)
        self._confirmed = True

    def last_write_confirmed(self):
        return self._confirmed


class FakeLiveNukiNoAck(FakeLiveNukiCreateFails):
    """Transport MIT Quittungskanal, aber der Write wurde nie quittiert."""
    def last_write_confirmed(self):
        return False


class DeviceAcknowledgedRotationTests(unittest.TestCase):
    """Die Geräteantwort schlägt die Liste: der Hub quittiert einen Write Sekunden bevor
    die publizierte Liste nachzieht — und am 06.09.2026 zog sie überhaupt nicht nach."""
    def setUp(self):
        self.store = InMemoryStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.pause = mock.patch.object(rotation, "WRITE_PAUSE_SECS", 0)
        self.pause.start(); self.addCleanup(self.pause.stop)
        # Beide Poll-Pfade auf "sieht nichts" festnageln: was der Test bestätigt, trägt
        # damit ausschließlich die Quittung — und ein Rückfall aufs Pollen fällt auf.
        self.wp = mock.patch.object(rotation, "_wait_present", lambda *a, **k: None)
        self.wp.start(); self.addCleanup(self.wp.stop)
        self.wg = mock.patch.object(rotation, "_wait_gone", lambda *a, **k: False)
        self.wg.start(); self.addCleanup(self.wg.stop)

    def _preds(self):
        return [("og-h03-p0", 501, "650000")] + [
            (f"og-bh-p{p}", 600 + p, f"70000{p}") for p in range(pin_pool.FALLBACK_POOL)]

    def test_acknowledged_writes_rotate_without_a_visible_list(self):
        nuki = FakeLiveNukiDeviceAck(self._preds())
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=7, day=DAY,
                                    dry_run=False, force=True)
        self.assertEqual(res["alerts"], 0, "quittierte Creates sind keine Fehlschlaege")
        self.assertEqual(res["tombstones"], 0, "quittierte Deletes brauchen kein _wait_gone")
        self.assertIn(501, nuki.deleted, "Vorgaenger muss rotiert worden sein")
        self.assertIn(600, nuki.deleted)

    def test_unacknowledged_write_still_keeps_the_predecessor(self):
        """Gegenprobe: ohne Quittung bleibt es beim alten, sicheren Verhalten —
        niemals einen funktionierenden Code ohne lebenden Nachfolger loeschen."""
        nuki = FakeLiveNukiNoAck(self._preds())
        res = rotation.rotate_daily(db=None, nuki=nuki, smartlock_id=7, day=DAY,
                                    dry_run=False, force=True)
        self.assertEqual(nuki.deleted, [], "kein Delete ohne bestaetigten Nachfolger")
        self.assertEqual(res["alerts"], res["slots"], "jeder Slot bleibt ein Create-Miss")

    def test_web_api_transport_is_untouched(self):
        """Der Web-API-Client kennt keinen Quittungskanal — sein Verhalten bleibt exakt."""
        self.assertFalse(rotation._device_confirmed(FakeLiveNuki([])))
        self.assertFalse(rotation._device_confirmed(object()))


_DB = object()   # Platzhalter-Handle: der Fake-Store benutzt es nicht


class UnreachableNuki(MatNuki):
    """Transport tot: wir koennen NICHT pruefen. Das ist kein Urteil ueber den Code —
    ``ensure_code_materialised`` macht daraus ``unreachable``."""
    def verify_code_for_window(self, code, *, weekday, hour, slot_name=None):  # noqa: ARG002
        self.verify_calls += 1
        return {"exists": False, "materialised": False, "covers_window": False,
                "valid": False, "simulated": False, "auth_id": None,
                "update_date": None, "link_last_confirmed": None, "error": True}


class AttestedFallbackTests(unittest.TestCase):
    """Ein toter Hub darf niemanden vor der verschlossenen Tuer stehen lassen, wenn das
    SCHLOSS den Code vorher selbst bestaetigt hat — und muss es sehr wohl, wenn nicht."""
    def setUp(self):
        self.store = MatStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start(); self.addCleanup(self.patch.stop)
        for p in range(pin_pool.POOL_PER_HOUR):
            self.store.pins[(3, p, DAY)] = f"65432{p}"

    def _window(self):
        return {"id": 1, "member_id": 42, "email": "m@x.de",
                "first_name": "A", "last_name": "B",
                "starts_at": datetime(2026, 7, 6, 1, 0, tzinfo=UTC),   # 03:00 Berlin
                "ends_at": datetime(2026, 7, 6, 2, 0, tzinfo=UTC)}

    def _deliver(self, nuki):
        email = FakeEmail()
        r = rotation.assign_and_deliver(
            db=_DB, window=self._window(), email_service=email, smartlock_id=0,
            day=DAY, nuki=nuki, settings=None,
        )
        return r, email

    def test_unreachable_without_attestation_stays_fail_closed(self):
        """Ohne Beweis bleibt es beim alten Verhalten: lieber kein Code als ein toter."""
        r, email = self._deliver(UnreachableNuki())
        self.assertFalse(r["assigned"])
        self.assertEqual(r.get("reason"), "unreachable")
        self.assertEqual(email.sends, 0)

    def test_unreachable_with_attestation_still_delivers(self):
        """Der eigentliche Zweck: Hub tot, Code aber am Geraet bewiesen -> Versand."""
        for p in range(pin_pool.POOL_PER_HOUR):
            self.store.verified[(3, p, DAY)] = "2026-09-06T10:00:00Z"
        r, email = self._deliver(UnreachableNuki())
        self.assertTrue(r["assigned"])
        self.assertEqual(email.sends, 1)

    def test_successful_verification_records_the_attestation(self):
        """Der Beweis muss im Normalbetrieb kostenlos entstehen — sonst ist spaeter keiner da."""
        r, _ = self._deliver(MatNuki(materialised=True, covers=True))
        self.assertTrue(r["assigned"])
        self.assertTrue(any(self.store.verified.values()), "erfolgreiche Pruefung muss stempeln")

    def test_simulated_verification_never_counts_as_proof(self):
        """DRY-RUN bestaetigt am Geraet gar nichts — daraus darf nie ein Beweis werden."""
        self._deliver(MatNuki(materialised=True, covers=True, simulated=True))
        self.assertEqual(self.store.verified, {})

    def test_attestation_does_not_rescue_a_genuinely_bad_code(self):
        """Wichtige Abgrenzung: der Beweis gilt NUR bei ``unreachable``. Sagt das Geraet
        'Code fehlt', bleibt es fail-closed — sonst schickten wir einen toten Code."""
        for p in range(pin_pool.POOL_PER_HOUR):
            self.store.verified[(3, p, DAY)] = "2026-09-06T10:00:00Z"
        r, email = self._deliver(MatNuki(materialised=False, covers=False,
                                         exists=False, repair_succeeds=False))
        self.assertFalse(r["assigned"])
        self.assertEqual(email.sends, 0)


class AttestNuki:
    """Nuki-Double fuer die Beweisfuehrung: kennt Namen->codeId und beantwortet ``check``."""
    def __init__(self, valid_codes, *, raise_on_check=False):
        self.valid_codes = set(valid_codes)
        self.raise_on_check = raise_on_check
        self.checks = []

    def list_keypad_codes(self):
        return [{"name": f"og-h03-p{p}", "id": 800 + p} for p in range(pin_pool.POOL_PER_HOUR)]

    def check_keypad_code(self, *, code_id, code):
        if self.raise_on_check:
            raise RuntimeError("MQTT weg")
        self.checks.append((code_id, code))
        return code in self.valid_codes

    def close(self):
        pass


class AttestSlotPinsTests(unittest.TestCase):
    """Die Vorsorge selbst: bestaetigen, solange der Hub lebt — haeppchenweise."""
    def setUp(self):
        self.store = MatStore()
        self.patch = mock.patch.object(rotation, "store", self.store)
        self.patch.start(); self.addCleanup(self.patch.stop)
        for p in range(pin_pool.POOL_PER_HOUR):
            self.store.pins[(3, p, DAY)] = f"65432{p}"

    def test_only_codes_the_lock_confirms_are_recorded(self):
        nuki = AttestNuki(valid_codes={"654320"})
        res = rotation.attest_slot_pins(db=_DB, nuki=nuki, smartlock_id=0, day=DAY)
        self.assertEqual(res["attested"], 1)
        self.assertEqual(res["mismatched"], pin_pool.POOL_PER_HOUR - 1)
        self.assertEqual(self.store.verified.get((3, 0, DAY)) is not None, True)
        self.assertIsNone(self.store.verified.get((3, 1, DAY)))

    def test_already_attested_slots_are_not_rechecked(self):
        """Sonst laeuft die Vorsorge jeden Zyklus ueber denselben Satz."""
        self.store.verified[(3, 0, DAY)] = "2026-09-06T12:00:00Z"
        nuki = AttestNuki(valid_codes={"654320", "654321"})
        rotation.attest_slot_pins(db=_DB, nuki=nuki, smartlock_id=0, day=DAY)
        self.assertNotIn(800, [c[0] for c in nuki.checks])

    def test_transport_failure_stops_instead_of_hammering_the_hub(self):
        """Genau dieses Draufhalten hat die MQTT-Task des Hubs am 06.09. lahmgelegt."""
        nuki = AttestNuki(valid_codes=set(), raise_on_check=True)
        res = rotation.attest_slot_pins(db=_DB, nuki=nuki, smartlock_id=0, day=DAY)
        self.assertEqual(res["attested"], 0)
        self.assertEqual(len(nuki.checks), 0)

    def test_limit_bounds_the_work_per_cycle(self):
        nuki = AttestNuki(valid_codes={f"65432{p}" for p in range(pin_pool.POOL_PER_HOUR)})
        res = rotation.attest_slot_pins(db=_DB, nuki=nuki, smartlock_id=0, day=DAY, limit=1)
        self.assertEqual(res["checked"], 1)

    def test_a_transport_without_check_is_skipped_cleanly(self):
        """Der Web-API-Client kennt ``check_keypad_code`` nicht — kein Absturz."""
        res = rotation.attest_slot_pins(db=_DB, nuki=object(), smartlock_id=0, day=DAY)
        self.assertEqual(res["attested"], 0)
