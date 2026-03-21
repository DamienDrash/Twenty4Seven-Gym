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
        try:
            response = self._client.request(method, path, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
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
        raise NukiApiError(
            "Live remote open is intentionally disabled until real Nuki credentials are available."
        )

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
        raise NukiApiError(
            "Live lock state is intentionally disabled until real Nuki credentials are available."
        )
