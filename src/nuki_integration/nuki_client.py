"""Nuki Web API client with dry-run support.

Endpoints verified against Nuki Web API v1.5.3 (2024-11-12):
- https://developer.nuki.io/t/web-api-example-manage-pin-codes-for-your-nuki-keypad/54
- https://api.nuki.io/ (Swagger)

Key differences from Bridge HTTP API:
- Actions use named endpoints (/action/unlock), not numeric codes
- PIN creation returns 204 No Content (no auth ID in response)
- Auth endpoints include smartlockId in path
"""
from __future__ import annotations
import logging
import re
from typing import Any
import httpx
from .config import Settings
from .exceptions import NukiApiError

logger = logging.getLogger(__name__)

# PIN code rules from Nuki documentation:
# - 6 digits, only 1-9 (no zero)
# - Must not start with "12"
# - Each code must be unique per keypad
_PIN_PATTERN = re.compile(r"^[1-9]{6}$")


def validate_keypad_code(code: str) -> None:
    """Validate a keypad PIN against Nuki hardware constraints.

    Raises NukiApiError if the code violates any rule.
    """
    if not _PIN_PATTERN.match(code):
        raise NukiApiError(
            f"Keypad-Code muss genau 6 Ziffern (1-9, keine Null) sein, erhalten: '{code}'"
        )
    if code.startswith("12"):
        raise NukiApiError(
            "Keypad-Code darf nicht mit '12' beginnen (Nuki-Einschränkung)."
        )


def evaluate_materialization(auths: list[dict[str, Any]], code: str) -> dict[str, Any]:
    """Pure check: is ``code`` present as a *materialised* type-13 auth?

    An auth that exists but whose ``updateDate`` is None (or missing) counts as
    NOT materialised — that is exactly the 204-but-not-materialised failure mode.
    """
    want = int(code)
    for auth in auths:
        if auth.get("type") == 13 and auth.get("code") == want:
            update_date = auth.get("updateDate")
            return {
                "materialised": update_date is not None,
                "simulated": False,
                "auth_id": auth.get("id"),
                "update_date": update_date,
            }
    return {"materialised": False, "simulated": False, "auth_id": None, "update_date": None}


def _auth_covers_hour(auth: dict[str, Any], weekday: int, hour: int) -> bool:
    """Does a type-13 auth's time restriction admit the booked ``(weekday, hour)``?

    ``allowedWeekDays`` is the Nuki bitmask (Mo=64 … So=1); a 0/absent mask means
    every day. ``allowedFromTime``/``allowedUntilTime`` are minutes since midnight;
    when absent the code has no daily restriction (always covered). A booked hour is
    covered iff its start minute (``hour*60``) falls inside ``[from, until)`` — this
    matches exactly how the rotation builds each slot's window.
    """
    mask = auth.get("allowedWeekDays")
    if mask:  # 0 or None → all days
        if not (mask & (64 >> weekday)):
            return False
    from_time = auth.get("allowedFromTime")
    until_time = auth.get("allowedUntilTime")
    if from_time is None or until_time is None:
        return True
    start = hour * 60
    return from_time <= start < until_time


def evaluate_window_materialization(
    auths: list[dict[str, Any]], code: str, weekday: int, hour: int
) -> dict[str, Any]:
    """Pure check: is ``code`` materialised *and* valid for the booked hour?

    Extends :func:`evaluate_materialization` with a time-window coverage test so a
    code that exists but whose daily/weekday restriction does not admit the booked
    first hour is reported as ``valid=False`` (and therefore repaired before send).
    """
    want = int(code)
    for auth in auths:
        if auth.get("type") == 13 and auth.get("code") == want:
            materialised = auth.get("updateDate") is not None
            covers = _auth_covers_hour(auth, weekday, hour)
            return {
                "materialised": materialised,
                "covers_window": covers,
                "valid": materialised and covers,
                "simulated": False,
                "auth_id": auth.get("id"),
                "update_date": auth.get("updateDate"),
            }
    return {
        "materialised": False, "covers_window": False, "valid": False,
        "simulated": False, "auth_id": None, "update_date": None,
    }


# Server state mapping (from Nuki Web API docs)
_SERVER_STATE = {
    0: "OK",
    1: "UNREGISTERED",
    2: "AUTH_UUID_INVALID",
    3: "AUTH_INVALID",
    4: "OFFLINE",
}

# Lock state mapping (from Nuki Web API v1.5.3, Section L)
_LOCK_STATE = {
    0: "Nicht kalibriert",
    1: "Abgeschlossen",
    2: "Wird entriegelt…",
    3: "Entriegelt",
    4: "Wird abgeschlossen…",
    5: "Falle gezogen",
    6: "Lock 'n' Go aktiv",
    7: "Öffnet Falle…",
    254: "Motor blockiert",
    255: "Unbekannt",
}

# Door state mapping (from Nuki Web API v1.5.3, Section N)
_DOOR_STATE = {
    0: "Nicht verfügbar",
    1: "Deaktiviert",
    2: "Geschlossen",
    3: "Geöffnet",
    4: "Unbekannt",
    5: "Kalibrierung…",
    16: "Nicht kalibriert",
    240: "Manipulation erkannt",
    255: "Unbekannt",
}


class NukiClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.nuki_base_url,
            timeout=settings.nuki_timeout_seconds,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    # ── Internal request helper ───────────────────────────────────

    def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> Any:
        token = self._settings.active_nuki_token
        if not token and not self._settings.nuki_dry_run:
            raise NukiApiError("No Nuki bearer token configured.")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        logger.info("Nuki %s %s", method, path)
        try:
            response = self._client.request(
                method, path, json=json_body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise NukiApiError(f"Nuki request failed: {exc}") from exc
        if response.status_code >= 400:
            raise NukiApiError(
                f"Nuki API {response.status_code}: {response.text[:500]}"
            )
        if not response.content or response.status_code == 204:
            return None
        return response.json()

    # ── Keypad code lifecycle ─────────────────────────────────────

    def _build_keypad_payload(
        self,
        *,
        name: str,
        code: str | None,
        allowed_from: str,
        allowed_until: str,
        allowed_week_days: int = 127,
        allowed_from_time: int | None = None,
        allowed_until_time: int | None = None,
    ) -> dict[str, Any]:
        """Build a type-13 auth payload, incl. optional time-window restriction.

        ``allowed_from_time`` / ``allowed_until_time`` are minutes since midnight
        (0–1439); when both are given, the code only opens during that daily
        window on the days in ``allowed_week_days`` (Nuki bitmask Mo=64 … So=1).
        """
        payload: dict[str, Any] = {
            "name": name[:32],
            "type": 13,
            "smartlockIds": [self._settings.nuki_smartlock_id],
            "allowedFromDate": allowed_from,
            "allowedUntilDate": allowed_until,
            "allowedWeekDays": allowed_week_days,
        }
        if code is not None:
            payload["code"] = int(code)
        if allowed_from_time is not None and allowed_until_time is not None:
            payload["allowedFromTime"] = allowed_from_time
            payload["allowedUntilTime"] = allowed_until_time
        return payload

    def create_keypad_code(
        self,
        *,
        name: str,
        code: str,
        allowed_from: str,
        allowed_until: str,
        allowed_week_days: int = 127,
        allowed_from_time: int | None = None,
        allowed_until_time: int | None = None,
    ) -> int | None:
        """Create a type-13 (keypad) authorization on the smart lock.

        Returns the nuki auth ID (hex string) if retrievable, None otherwise.
        Note: PUT returns 204 No Content — the ID must be fetched separately.
        The time-window fields default to a full-week, all-day code (backward
        compatible with the per-booking caller).
        """
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code create for %s", name)
            return None

        validate_keypad_code(code)
        safe_name = name[:32]
        smartlock_id = self._settings.nuki_smartlock_id

        payload = self._build_keypad_payload(
            name=safe_name, code=code,
            allowed_from=allowed_from, allowed_until=allowed_until,
            allowed_week_days=allowed_week_days,
            allowed_from_time=allowed_from_time, allowed_until_time=allowed_until_time,
        )

        self._request(
            "PUT",
            f"/smartlock/{smartlock_id}/auth",
            json_body=payload,
        )

        # PUT returns 204 — try to fetch the auth ID by listing + matching name
        return self._find_auth_id_by_name(safe_name)

    def _find_auth_id_by_name(self, name: str) -> int | None:
        """Look up a freshly created auth entry by name.

        Retries with increasing delays to handle Nuki API sync latency -- the
        PUT endpoint returns 204 immediately but the new auth may not appear
        in GET /auth for several seconds.
        """
        import time
        for attempt in range(5):
            if attempt > 0:
                wait = attempt * 4  # 4s, 8s, 12s, 16s
                logger.info(
                    "_find_auth_id_by_name: attempt %d/5, waiting %ds for Nuki sync (name=%r)",
                    attempt + 1, wait, name,
                )
                time.sleep(wait)
            try:
                auths = self._request(
                    "GET",
                    f"/smartlock/{self._settings.nuki_smartlock_id}/auth",
                )
                if not isinstance(auths, list):
                    return None
                match = next(
                    (a for a in auths if a.get("name") == name and a.get("type") == 13),
                    None,
                )
                if match:
                    logger.info(
                        "_find_auth_id_by_name: found auth_id=%s for %r on attempt %d",
                        match["id"], name, attempt + 1,
                    )
                    return match["id"]
            except Exception:
                logger.warning(
                    "_find_auth_id_by_name: request failed (attempt %d, name=%r)",
                    attempt + 1, name,
                )
        logger.error(
            "_find_auth_id_by_name: auth ID for %r not found after 5 attempts -- "
            "code is on Nuki keypad but DB will have nuki_auth_id=NULL",
            name,
        )
        return None

    def update_keypad_code(
        self,
        *,
        auth_id: int,
        name: str,
        code: str | None = None,
        allowed_from: str,
        allowed_until: str,
        allowed_week_days: int = 127,
        allowed_from_time: int | None = None,
        allowed_until_time: int | None = None,
        enabled: bool = True,
    ) -> None:
        """Update an existing keypad authorization.

        All fields must be sent — partial updates cause 422.
        """
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code update auth_id=%s", auth_id)
            return

        if code is not None:
            validate_keypad_code(code)
        payload = self._build_keypad_payload(
            name=name, code=code,
            allowed_from=allowed_from, allowed_until=allowed_until,
            allowed_week_days=allowed_week_days,
            allowed_from_time=allowed_from_time, allowed_until_time=allowed_until_time,
        )
        payload.pop("smartlockIds", None)  # not accepted on the per-auth update
        payload["enabled"] = enabled

        self._request(
            "POST",
            f"/smartlock/{self._settings.nuki_smartlock_id}/auth/{auth_id}",
            json_body=payload,
        )

    def verify_materialization(self, code: str) -> dict[str, Any]:
        """Verify (GET after PUT) that ``code`` actually materialised on the device.

        The known Nuki bug: a PUT returns 204 but the code never lands on the
        keypad (``updateDate`` stays None). This GETs the auth list and checks
        that the type-13 auth for ``code`` exists **and** has a non-null
        ``updateDate``. DRY-RUN performs no network call and returns a simulated OK.
        """
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip materialization check for code ******")
            return {"materialised": True, "simulated": True, "auth_id": None, "update_date": None}
        try:
            auths = self._request(
                "GET", f"/smartlock/{self._settings.nuki_smartlock_id}/auth"
            )
        except Exception as exc:
            logger.error("verify_materialization: GET failed: %s", exc)
            return {"materialised": False, "simulated": False, "auth_id": None, "update_date": None}
        return evaluate_materialization(auths if isinstance(auths, list) else [], code)

    def verify_code_for_window(self, code: str, *, weekday: int, hour: int) -> dict[str, Any]:
        """Verify ``code`` is materialised on the device AND valid for the booked hour.

        This is the pre-dispatch gate: it confirms the exact code is present with a
        non-null ``updateDate`` and that its time restriction admits ``(weekday, hour)``
        — i.e. the member's FIRST booked hour. DRY-RUN performs no network call and
        returns a simulated OK.
        """
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip window verification for code ****** (wd=%s h=%s)", weekday, hour)
            return {
                "materialised": True, "covers_window": True, "valid": True,
                "simulated": True, "auth_id": None, "update_date": None,
            }
        try:
            auths = self._request(
                "GET", f"/smartlock/{self._settings.nuki_smartlock_id}/auth"
            )
        except Exception as exc:
            logger.error("verify_code_for_window: GET failed: %s", exc)
            return {
                "materialised": False, "covers_window": False, "valid": False,
                "simulated": False, "auth_id": None, "update_date": None,
            }
        return evaluate_window_materialization(
            auths if isinstance(auths, list) else [], code, weekday, hour
        )

    def deactivate_keypad_code(self, *, auth_id: int) -> None:
        """Disable a keypad code without deleting it."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip deactivate auth_id=%s", auth_id)
            return
        self._request(
            "POST",
            f"/smartlock/{self._settings.nuki_smartlock_id}/auth/{auth_id}",
            json_body={"enabled": False},
        )

    def delete_keypad_code(self, *, auth_id: int | str) -> None:
        """Permanently delete a keypad authorization."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code delete auth_id=%s", auth_id)
            return
        self._request(
            "DELETE",
            f"/smartlock/{self._settings.nuki_smartlock_id}/auth/{auth_id}",
        )

    # ── Lock actions (Web API uses named endpoints) ───────────────

    def remote_open(self) -> dict[str, Any]:
        """Trigger an unlock action via the Web API."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip remote open")
            return {"dry_run": True, "smartlock_id": self._settings.nuki_smartlock_id}
        try:
            self._request(
                "POST",
                f"/smartlock/{self._settings.nuki_smartlock_id}/action/unlock",
            )
            return {"success": True, "smartlock_id": self._settings.nuki_smartlock_id}
        except Exception as exc:
            raise NukiApiError(f"Remote unlock failed: {exc}") from exc

    def remote_lock(self) -> dict[str, Any]:
        """Trigger a lock action via the Web API."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip remote lock")
            return {"dry_run": True, "smartlock_id": self._settings.nuki_smartlock_id}
        self._request(
            "POST",
            f"/smartlock/{self._settings.nuki_smartlock_id}/action/lock",
        )
        return {"success": True, "smartlock_id": self._settings.nuki_smartlock_id}

    def remote_unlatch(self) -> dict[str, Any]:
        """Trigger an unlatch (Falle ziehen) action via the Web API."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip remote unlatch")
            return {"dry_run": True, "smartlock_id": self._settings.nuki_smartlock_id}
        self._request(
            "POST",
            f"/smartlock/{self._settings.nuki_smartlock_id}/action/unlatch",
        )
        return {"success": True, "smartlock_id": self._settings.nuki_smartlock_id}

    # ── Status ────────────────────────────────────────────────────

    def get_lock_status(self) -> dict[str, Any]:
        """Get the current smart lock state, door state, and battery info.

        Response structure from GET /smartlock/{id}:
        {
          "state": {
            "mode": 2,
            "state": 1,         ← lock state integer
            "batteryCritical": false,
            "batteryCharge": 75,
            "batteryCharging": false,
            "doorState": 2
          },
          "serverState": 0,     ← 0=OK, 4=OFFLINE
          "updateDate": "..."
        }
        """
        if self._settings.nuki_dry_run:
            return {
                "dry_run": True,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "credentials-pending",
                "stateName": "Testmodus",
                "lock_state": "Testmodus",
                "door_state": "Kein Sensor",
                "battery_state": "Unbekannt",
                "battery_critical": False,
                "batteryCritical": False,
                "source": "dry-run",
            }
        try:
            data = self._request(
                "GET", f"/smartlock/{self._settings.nuki_smartlock_id}"
            )
            if not isinstance(data, dict):
                raise NukiApiError("Unexpected smartlock response format.")

            # 1) Check serverState — if not 0, device is unreachable
            server_state = data.get("serverState", -1)
            is_online = server_state == 0

            # 2) Extract nested state object
            state_obj = data.get("state")
            if state_obj is None:
                state_obj = data.get("lastKnownState")
            if not isinstance(state_obj, dict):
                state_obj = {}

            # 3) Lock state
            lock_code = state_obj.get("state")
            if lock_code is not None:
                lock_label = _LOCK_STATE.get(lock_code, f"Unbekannt ({lock_code})")
            else:
                lock_label = "Nicht verfügbar"

            # 4) Door state
            door_code = state_obj.get("doorState")
            if door_code is not None:
                door_label = _DOOR_STATE.get(door_code, f"Unbekannt ({door_code})")
            else:
                door_label = "Kein Sensor"

            # 5) Battery — batteryCharge (int%) may not exist on older models
            battery_critical = bool(state_obj.get("batteryCritical", False))
            battery_charging = bool(state_obj.get("batteryCharging", False))
            battery_charge = state_obj.get("batteryCharge")  # None if missing

            if battery_charge is not None:
                battery_state = f"{battery_charge}%"
                if battery_charging:
                    battery_state += " (lädt)"
                elif battery_critical:
                    battery_state += " ⚠"
            elif battery_critical:
                battery_state = "Kritisch!"
            else:
                battery_state = "OK"

            # 6) Build connectivity label
            if is_online:
                connectivity = "online"
                state_name = lock_label
            else:
                server_label = _SERVER_STATE.get(server_state, str(server_state))
                connectivity = f"offline ({server_label})"
                state_name = f"{lock_label} (veraltet)"

            return {
                "dry_run": False,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": connectivity,
                "server_state": server_state,
                "lock_state": lock_label,
                "stateName": state_name,
                "door_state": door_label,
                "battery_state": battery_state,
                "battery_charge": battery_charge,
                "battery_critical": battery_critical,
                "batteryCritical": battery_critical,
                "battery_charging": battery_charging,
                "last_update": data.get("updateDate"),
                "source": "nuki-api",
            }

        except NukiApiError:
            raise
        except Exception as exc:
            logger.error("Nuki status failed: %s", exc)
            return {
                "dry_run": False,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "error",
                "stateName": "Verbindungsfehler",
                "lock_state": "error",
                "door_state": "Unbekannt",
                "battery_state": "Unbekannt",
                "battery_critical": False,
                "batteryCritical": False,
                "error": str(exc),
                "source": "nuki-api",
            }

    def list_keypad_codes(self) -> list[dict]:
        """Return all type-13 (keypad) authorizations on the smart lock."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip list_keypad_codes")
            return []
        try:
            auths = self._request(
                "GET",
                f"/smartlock/{self._settings.nuki_smartlock_id}/auth",
            )
            if not isinstance(auths, list):
                return []
            return [a for a in auths if a.get("type") == 13]
        except Exception as exc:
            logger.error("Failed to list Nuki keypad codes: %s", exc)
            return []

    # ── Sync ──────────────────────────────────────────────────────

    def force_sync(self) -> None:
        """Force-sync the device to pull latest state from the smart lock."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip force sync")
            return
        self._request(
            "POST",
            f"/smartlock/{self._settings.nuki_smartlock_id}/sync",
        )
