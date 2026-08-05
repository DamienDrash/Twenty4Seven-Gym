"""Shared in-memory fakes for the Nuki verification/guardian tests (no DB/net)."""
from __future__ import annotations

from nuki_integration.timewindow import pin_pool


class InMemoryStore:
    """Duck-typed replacement for timewindow.store (module-level functions)."""

    def __init__(self):
        self.slots = {}        # (hour,pool_index) -> id
        self.slot_meta = {}    # id -> dict(name, weekday_mask, from_time, until_time, nuki_auth_id)
        self.pins = {}         # (hour,pool_index,date) -> pin
        self.assignments = []  # list of dicts
        self._relevant = []    # windows returned by relevant_windows()
        self._seq = 0

    # -- rotation surface --
    def ensure_schema(self, db):  # noqa: ARG002
        pass

    def rotation_count_for_day(self, db, day):  # noqa: ARG002
        return sum(1 for (_h, _p, d) in self.pins if d == day)

    def upsert_slot(self, db, *, smartlock_id, name, hour, pool_index,  # noqa: ARG002
                    weekday_mask, from_time, until_time):
        key = (hour, pool_index)
        if key not in self.slots:
            self._seq += 1
            self.slots[key] = self._seq
        sid = self.slots[key]
        self.slot_meta[sid] = {
            "id": sid, "name": name, "hour": hour, "pool_index": pool_index,
            "weekday_mask": weekday_mask, "from_time": from_time,
            "until_time": until_time, "nuki_auth_id": self.slot_meta.get(sid, {}).get("nuki_auth_id"),
        }
        return sid

    def set_slot_auth_id(self, db, slot_id, nuki_auth_id):  # noqa: ARG002
        if slot_id in self.slot_meta:
            self.slot_meta[slot_id]["nuki_auth_id"] = nuki_auth_id

    def record_rotation(self, db, *, slot_id, rotation_date, pin, pushed, materialised, dry_run):  # noqa: ARG002
        hour, pidx = next(k for k, v in self.slots.items() if v == slot_id)
        self.pins[(hour, pidx, rotation_date)] = pin

    def get_todays_slot_pin(self, db, *, smartlock_id, hour, pool_index, rotation_date):  # noqa: ARG002
        return self.pins.get((hour, pool_index, rotation_date))

    def get_slot(self, db, *, smartlock_id, hour, pool_index):  # noqa: ARG002
        sid = self.slots.get((hour, pool_index))
        return self.slot_meta.get(sid) if sid is not None else None

    def recent_pool_indices(self, db, *, member_ref, weekday, hour, limit=4):  # noqa: ARG002
        rows = [a["pool_index"] for a in self.assignments
                if a["member_ref"] == member_ref and a["weekday"] == weekday and a["hour"] == hour]
        return rows[-limit:]

    def record_assignment(self, db, *, member_ref, weekday, hour, pool_index, assigned_date):  # noqa: ARG002
        self.assignments.append(dict(member_ref=member_ref, weekday=weekday, hour=hour,
                                     pool_index=pool_index, assigned_date=assigned_date))

    def rotation_status(self, db, day):  # noqa: ARG002
        return {"slots": len(self.slots), "rotated_today": self.rotation_count_for_day(db, day)}

    # -- guardian surface --
    def set_relevant_windows(self, windows):
        self._relevant = list(windows)

    def relevant_windows(self, db, *, now, lead_seconds=7200):  # noqa: ARG002
        return list(self._relevant)


class FakeNuki:
    """Controllable Nuki double: tracks verify/create/update and can 'repair'."""

    def __init__(self, *, materialised=True, covers=True, simulated=False,
                 repair_succeeds=True, exists=True, link_last_confirmed=None):
        self.materialised = materialised
        self.covers = covers
        self.simulated = simulated
        self.repair_succeeds = repair_succeeds
        self.exists = exists  # code present on the lock (create/push landed), regardless of updateDate
        self.link_last_confirmed = link_last_confirmed  # newest device confirmation (freeze signal)
        self.verify_calls = 0
        self.creates = []
        self.updates = []

    def verify_code_for_window(self, code, *, weekday, hour):  # noqa: ARG002
        self.verify_calls += 1
        return {
            "exists": self.exists,
            "materialised": self.materialised, "covers_window": self.covers,
            "valid": self.materialised and self.covers, "simulated": self.simulated,
            "auth_id": 7 if self.materialised else None, "update_date": None,
            "link_last_confirmed": self.link_last_confirmed,
        }

    def _apply_repair(self):
        if self.repair_succeeds:
            self.exists = True
            self.materialised = True
            self.covers = True

    def create_keypad_code(self, **kwargs):
        self.creates.append(kwargs)
        self._apply_repair()
        return 99 if self.repair_succeeds else None

    def update_keypad_code(self, **kwargs):
        self.updates.append(kwargs)
        self._apply_repair()

    def close(self):
        pass


class FakeEmail:
    def __init__(self, *, delivered=True):
        self.delivered = delivered
        self.sends = 0
        self.last = None

    def send_access_code(self, **kwargs):
        self.sends += 1
        self.last = kwargs
        return self.delivered


class FakeDb:
    """Minimal DB double capturing alerts (no settings lookups configured)."""

    def __init__(self):
        self.alerts = []

    def create_alert(self, *, severity, kind, message, payload=None):
        self.alerts.append({"severity": severity, "kind": kind, "message": message,
                            "payload": payload or {}})

    def get_system_setting(self, key):  # noqa: ARG002
        return None


def offpeak_slot_name(hour, pool_index):
    return f"og-h{hour:02d}-p{pool_index}"


def fallback_slot_name(pool_index):
    return f"og-bh-p{pool_index}"


FALLBACK_HOUR = pin_pool.FALLBACK_HOUR
