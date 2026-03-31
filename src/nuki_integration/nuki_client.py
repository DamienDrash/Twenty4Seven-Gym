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

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> Any:
        token = self._settings.active_nuki_token
        if not token and not self._settings.nuki_dry_run:
            raise NukiApiError("No Nuki bearer token configured.")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        
        logger.info("Nuki API Request: %s %s | Payload: %s", method, path, json_body)
        
        try:
            response = self._client.request(method, path, json=json_body, headers=headers)
            logger.info("Nuki API Response: %s | Content: %s", response.status_code, response.text[:200])
        except httpx.HTTPError as exc:
            logger.error("Nuki HTTP Communication Error: %s", exc)
            raise NukiApiError(f"Nuki request failed: {exc}") from exc
            
        if response.status_code >= 400:
            raise NukiApiError(f"Nuki API error {response.status_code}: {response.text[:500]}")
        if not response.content:
            return None
        return response.json()

    def create_keypad_code(
        self,
        *,
        name: str,
        code: str,
        allowed_from: str,
        allowed_until: str,
    ) -> int | None:
        if self._settings.nuki_dry_run:
            logger.info("NUKI_DRY_RUN enabled, skipping live keypad code creation for %s", name)
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
        self,
        *,
        auth_id: int,
        name: str,
        allowed_from: str,
        allowed_until: str,
    ) -> None:
        if self._settings.nuki_dry_run:
            logger.info(
                "NUKI_DRY_RUN enabled, skipping live keypad code update for auth_id=%s",
                auth_id,
            )
            return
        payload = {
            "name": name,
            "smartlockIds": [self._settings.nuki_smartlock_id],
            "allowedFromDate": allowed_from,
            "allowedUntilDate": allowed_until,
        }
        self._request("POST", f"/smartlock/auth/{auth_id}", json_body=payload)

    def remote_open(self) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            logger.info("NUKI_DRY_RUN enabled, skipping live remote open")
            return {"dry_run": True, "smartlock_id": self._settings.nuki_smartlock_id}
        
        # Nuki API: POST /smartlock/{smartlockId}/action/unlock
        # Action 1 = unlock, 3 = unlatch
        # We usually want 3 (unlatch) for entrance doors
        path = f"/smartlock/{self._settings.nuki_smartlock_id}/action"
        payload = {"action": 3} 
        try:
            self._request("POST", path, json_body=payload)
            return {"success": True, "smartlock_id": self._settings.nuki_smartlock_id}
        except Exception as exc:
            logger.error("Failed to remote open Nuki: %s", exc)
            raise NukiApiError(f"Remote open failed: {exc}")

    def get_lock_status(self) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            return {
                "dry_run": True,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "credentials-pending",
                "lock_state": "unknown",
                "battery_state": "unknown",
                "source": "dry-run",
            }
        
        # Nuki API: GET /smartlock/{smartlockId}
        path = f"/smartlock/{self._settings.nuki_smartlock_id}"
        try:
            data = self._request("GET", path)
            # Web API for Smartlock v2/v3 often puts the current state in 'state' object
            # fallback to 'lastKnownState' if 'state' is not what we expect
            state = data.get("state") or data.get("lastKnownState") or {}
            lock_state_name = self._map_lock_state(state.get("state"))
            
            return {
                "dry_run": False,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "online",
                "lock_state": lock_state_name,
                "stateName": lock_state_name,
                "door_state": self._map_door_state(state.get("doorState")),
                "battery_state": f"{state.get('batteryCharge', state.get('batteryChargeState', 0))}%",
                "battery_critical": state.get("batteryCritical", False),
                "batteryCritical": state.get("batteryCritical", False),
                "last_update": data.get("updateDate"),
                "source": "nuki-api",
            }
        except Exception as exc:
            logger.error("Failed to get Nuki status: %s", exc)
            return {
                "dry_run": False,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "error",
                "lock_state": "error",
                "error": str(exc),
                "source": "nuki-api",
            }

    def _map_door_state(self, state_code: int | None) -> str:
        # Nuki door sensor states
        mapping = {
            1: "Deaktiviert",
            2: "Geschlossen",
            3: "Geöffnet",
            4: "Unbekannt",
            5: "Kalibrierung...",
        }
        return mapping.get(state_code if state_code is not None else 0, "Kein Sensor")

    def _map_lock_state(self, state_code: int | None) -> str:
        # Nuki state codes translated to German for SaaS Gold Standard
        mapping = {
            0: "Nicht kalibriert",
            1: "Abgeschlossen",
            2: "Wird geöffnet...",
            3: "Entriegelt",
            4: "Wird abgeschlossen...",
            5: "Falle gezogen",
            6: "Lock 'n' Go aktiv",
            7: "Öffnet Falle...",
            254: "Motor blockiert",
            255: "Unbekannt"
        }
        return mapping.get(state_code if state_code is not None else 255, f"Unbekannt ({state_code})")
