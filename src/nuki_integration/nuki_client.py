"""Nuki Web API client with dry-run support."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings
from .exceptions import NukiApiError

logger = logging.getLogger(__name__)


class NukiClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.nuki_base_url,
            timeout=settings.nuki_timeout_seconds,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    # ── Low-level request ─────────────────────────────────────────

    def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None,
    ) -> Any:
        token = self._settings.active_nuki_token
        if not token and not self._settings.nuki_dry_run:
            raise NukiApiError("No Nuki bearer token configured.")
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        logger.info("Nuki %s %s", method, path)
        try:
            response = self._client.request(method, path, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise NukiApiError(f"Nuki request failed: {exc}") from exc

        if response.status_code >= 400:
            raise NukiApiError(f"Nuki API {response.status_code}: {response.text[:500]}")
        if not response.content:
            return None
        return response.json()

    # ── Keypad code CRUD ──────────────────────────────────────────

    def create_keypad_code(
        self, *, name: str, code: str, allowed_from: str, allowed_until: str,
    ) -> int | None:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code create for %s", name)
            return None
        payload = {
            "name": name,
            "type": 13,
            "code": int(code),
            "smartlockIds": [self._settings.nuki_smartlock_id],
            "allowedFromDate": allowed_from,
            "allowedUntilDate": allowed_until,
        }
        data = self._request("PUT", "/smartlock/auth", json_body=payload)
        if isinstance(data, dict) and "id" in data:
            return int(data["id"])
        return None

    def update_keypad_code(
        self, *, auth_id: int, name: str, allowed_from: str, allowed_until: str,
    ) -> None:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code update auth_id=%s", auth_id)
            return
        payload = {
            "name": name,
            "smartlockIds": [self._settings.nuki_smartlock_id],
            "allowedFromDate": allowed_from,
            "allowedUntilDate": allowed_until,
        }
        self._request("POST", f"/smartlock/auth/{auth_id}", json_body=payload)

    def delete_keypad_code(self, *, auth_id: int) -> None:
        """Remove a keypad code from the smartlock."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code delete auth_id=%s", auth_id)
            return
        self._request(
            "DELETE",
            f"/smartlock/{self._settings.nuki_smartlock_id}/auth/{auth_id}",
        )

    # ── Lock actions ──────────────────────────────────────────────

    def remote_open(self) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip remote open")
            return {"dry_run": True, "smartlock_id": self._settings.nuki_smartlock_id}
        path = f"/smartlock/{self._settings.nuki_smartlock_id}/action"
        try:
            self._request("POST", path, json_body={"action": 3})
            return {"success": True, "smartlock_id": self._settings.nuki_smartlock_id}
        except Exception as exc:
            raise NukiApiError(f"Remote open failed: {exc}") from exc

    def get_lock_status(self) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            return {
                "dry_run": True,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "credentials-pending",
                "stateName": "Testmodus",
                "lock_state": "unknown",
                "battery_state": "unknown",
                "source": "dry-run",
            }
        try:
            data = self._request("GET", f"/smartlock/{self._settings.nuki_smartlock_id}")
            state = data.get("state") or data.get("lastKnownState") or {}
            lock_state = self._map_lock_state(state.get("state"))
            return {
                "dry_run": False,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "online",
                "lock_state": lock_state,
                "stateName": lock_state,
                "door_state": self._map_door_state(state.get("doorState")),
                "battery_state": f"{state.get('batteryCharge', 0)}%",
                "battery_critical": state.get("batteryCritical", False),
                "batteryCritical": state.get("batteryCritical", False),
                "last_update": data.get("updateDate"),
                "source": "nuki-api",
            }
        except Exception as exc:
            logger.error("Nuki status failed: %s", exc)
            return {
                "dry_run": False,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "error",
                "stateName": "Verbindungsfehler",
                "lock_state": "error",
                "error": str(exc),
                "source": "nuki-api",
            }

    # ── State mappers ─────────────────────────────────────────────

    @staticmethod
    def _map_door_state(code: int | None) -> str:
        return {
            1: "Deaktiviert", 2: "Geschlossen", 3: "Geöffnet",
            4: "Unbekannt", 5: "Kalibrierung…",
        }.get(code or 0, "Kein Sensor")

    @staticmethod
    def _map_lock_state(code: int | None) -> str:
        return {
            0: "Nicht kalibriert", 1: "Abgeschlossen", 2: "Wird geöffnet…",
            3: "Entriegelt", 4: "Wird abgeschlossen…", 5: "Falle gezogen",
            6: "Lock 'n' Go aktiv", 7: "Öffnet Falle…",
            254: "Motor blockiert", 255: "Unbekannt",
        }.get(code if code is not None else 255, f"Unbekannt ({code})")
