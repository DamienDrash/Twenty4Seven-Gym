"""Pre-dispatch keypad verification with a fail-closed, single repair loop.

Central rule: **before every access-code dispatch** the exact code must be
confirmed *materialised* on the Nuki keypad and *valid for the member's first
booked hour*. For multi-hour bookings only the FIRST booked hour is relevant —
callers therefore pass the ``(weekday, hour)`` of ``starts_at``.

``ensure_code_materialised`` verifies via the Nuki API; if the code is missing or
its time window does not match, it repairs it once (update existing auth, else
create) and re-verifies. It NEVER decides to send — it only reports whether the
device is in the required state, so the caller can fail closed.

The Nuki collaborator is injected (duck-typed: ``verify_code_for_window``,
``update_keypad_code``, ``create_keypad_code``) so the logic is testable without a
real device or network.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def ensure_code_materialised(
    nuki: Any,
    *,
    slot_name: str,
    code: str,
    weekday: int,
    hour: int,
    weekday_mask: int,
    from_time: int,
    until_time: int,
    allowed_from: str,
    allowed_until: str,
    auth_id: int | str | None = None,
    require_device_confirmation: bool = True,
) -> dict[str, Any]:
    """Verify ``code`` for the booked first hour; repair once if not, then re-verify.

    Returns a dict describing the outcome::

        {"valid": bool, "materialised": bool, "covers_window": bool,
         "repaired": bool, "simulated": bool, "attempts": int, "auth_id": ...}

    ``valid`` is the fail-closed gate: only when it is True may the caller dispatch.
    On DRY-RUN the injected client returns a simulated OK and no repair happens.
    """
    outcome: dict[str, Any] = {
        "valid": False, "deliverable": False, "exists": False,
        "materialised": False, "covers_window": False, "unconfirmed": False,
        "repaired": False, "simulated": False, "attempts": 0, "auth_id": auth_id,
        "link_last_confirmed": None,
    }

    check = nuki.verify_code_for_window(code, weekday=weekday, hour=hour)
    outcome["attempts"] = 1
    outcome["simulated"] = bool(check.get("simulated"))
    outcome["exists"] = bool(check.get("exists"))
    outcome["materialised"] = bool(check.get("materialised"))
    outcome["covers_window"] = bool(check.get("covers_window"))
    outcome["auth_id"] = check.get("auth_id", auth_id)
    outcome["link_last_confirmed"] = check.get("link_last_confirmed")

    # Device/API unreachable (transient WAN/timeout): we cannot verify, so we must
    # not repair blindly nor claim a materialisation failure. Report "unreachable"
    # so the caller skips/retries next cycle instead of alerting on a blip.
    if check.get("error"):
        outcome["unreachable"] = True
        return outcome

    if check.get("valid"):
        # Device-confirmed AND covers the booked hour — the strongest state.
        outcome["valid"] = True
        outcome["deliverable"] = True
        return outcome

    # OUTAGE DETECTOR: the code is present in the cloud auth list with the correct time
    # window (the create/push reached the CLOUD) but the device has not echoed a
    # confirmation back (no ``updateDate``). Cloud-presence ≠ device-presence: during a
    # Cloud↔Lock freeze this code has NEVER reached the physical keypad, so dispatching
    # it would hand a member a dead code (lockout — the 03.08.2026 incident).
    #   require_device_confirmation=True (default, safe): fail closed. Flag
    #     ``unconfirmed`` so the caller alerts + retries; once the device confirms
    #     (updateDate appears, usually within a cycle on a healthy link) it becomes
    #     deliverable. Stable, already-confirmed codes (e.g. the og-bh fallbacks) are
    #     unaffected — they take the ``valid`` branch above and survive an outage.
    #   require_device_confirmation=False (legacy): deliver on presence alone.
    if outcome["exists"] and outcome["covers_window"]:
        if require_device_confirmation and not outcome["materialised"]:
            outcome["unconfirmed"] = True
            outcome["deliverable"] = False
            return outcome
        outcome["deliverable"] = True
        return outcome

    # Repair once: keep the SAME code (it may already be in the member's mail).
    # Update the existing auth in place if we know it, else create a fresh one.
    logger.warning(
        "ensure_code_materialised: %s not valid (materialised=%s covers=%s) — repairing",
        slot_name, outcome["materialised"], outcome["covers_window"],
    )
    try:
        target_auth_id = outcome["auth_id"] or auth_id
        if target_auth_id is not None:
            # Nuki auth ids are hex strings (e.g. "6a607d439baa510030d97d90"), NOT
            # base-10 ints — pass through as-is (int() here crashed every repair).
            nuki.update_keypad_code(
                auth_id=target_auth_id, name=slot_name, code=code,
                allowed_from=allowed_from, allowed_until=allowed_until,
                allowed_week_days=weekday_mask,
                allowed_from_time=from_time, allowed_until_time=until_time,
                enabled=True,
            )
        else:
            new_id = nuki.create_keypad_code(
                name=slot_name, code=code,
                allowed_from=allowed_from, allowed_until=allowed_until,
                allowed_week_days=weekday_mask,
                allowed_from_time=from_time, allowed_until_time=until_time,
            )
            if new_id is not None:
                outcome["auth_id"] = new_id
    except Exception as exc:
        logger.error("ensure_code_materialised: repair of %s failed: %s", slot_name, exc)
        outcome["repaired"] = True
        return outcome

    outcome["repaired"] = True

    recheck = nuki.verify_code_for_window(code, weekday=weekday, hour=hour)
    outcome["attempts"] = 2
    outcome["exists"] = bool(recheck.get("exists"))
    outcome["materialised"] = bool(recheck.get("materialised"))
    outcome["covers_window"] = bool(recheck.get("covers_window"))
    outcome["valid"] = bool(recheck.get("valid"))
    outcome["link_last_confirmed"] = recheck.get("link_last_confirmed")
    # After a repair the freshly (re)created auth is present with the right window but
    # typically not yet device-confirmed. Under the outage detector that is NOT
    # deliverable (see gate above): a repair on a frozen link only updates the cloud,
    # not the keypad — so fail closed and flag ``unconfirmed`` rather than dispatch a
    # code that may not be on the device. Legacy mode delivers on presence.
    present_ok = bool(recheck.get("exists")) and bool(recheck.get("covers_window"))
    if require_device_confirmation and present_ok and not outcome["valid"]:
        outcome["unconfirmed"] = True
        outcome["deliverable"] = False
    else:
        outcome["deliverable"] = present_ok
    outcome["unreachable"] = bool(recheck.get("error"))
    outcome["auth_id"] = recheck.get("auth_id", outcome["auth_id"])
    return outcome
