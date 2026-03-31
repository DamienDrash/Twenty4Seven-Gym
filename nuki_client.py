"""Async Nuki Web API client.

Implements the subset of the Nuki Web API needed for decentral webhook
lifecycle management and basic device discovery.  Uses ``httpx.AsyncClient``
for non-blocking I/O within FastAPI's async event loop.

The client obtains its bearer token from ``OAuthTokenManager`` which
handles proactive refresh transparently.

For CLI scripts that run outside an async context, use
``asyncio.run(client.list_smartlocks())`` or the synchronous wrapper
in ``setup_webhook.py``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings
from .enums import WebhookFeature
from .exceptions import NukiApiError
from .models import DecentralWebhookRecord, DecentralWebhookRegistration
from .oauth import OAuthTokenManager

logger = logging.getLogger(__name__)


class NukiWebClient:
    """Async HTTP client for the Nuki Web API.

    Parameters
    ----------
    settings:
        Application settings.
    token_manager:
        Optional OAuth2 token manager.  When ``None``, the client
        falls back to ``settings.active_bearer_token`` (static token).
    """

    def __init__(
        self,
        settings: Settings,
        token_manager: OAuthTokenManager | None = None,
    ) -> None:
        self._settings = settings
        self._token_manager = token_manager
        self._client = httpx.AsyncClient(
            base_url=settings.nuki_base_url,
            timeout=settings.nuki_timeout_seconds,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        """Release HTTP and token-manager resources."""
        await self._client.aclose()
        if self._token_manager:
            await self._token_manager.close()

    async def _bearer_token(self) -> str:
        """Resolve the current bearer token."""
        if self._token_manager:
            return await self._token_manager.get_access_token()
        token = self._settings.active_bearer_token
        if not token:
            raise NukiApiError("No bearer token configured.")
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Execute an authenticated request against the Nuki Web API.

        Raises
        ------
        NukiApiError
            On network errors, non-2xx responses, or invalid JSON.
        """
        token = await self._bearer_token()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            response = await self._client.request(
                method, path, json=json_body, headers=headers,
            )
        except httpx.HTTPError as exc:
            raise NukiApiError(f"Nuki API request failed: {exc}") from exc

        if response.status_code == 429:
            raise NukiApiError(
                "Nuki API rate limit exceeded (HTTP 429). "
                "Reduce request frequency."
            )

        if response.status_code >= 400:
            raise NukiApiError(
                f"Nuki API error {response.status_code}: "
                f"{response.text[:500]}"
            )

        if not response.content:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise NukiApiError("Nuki API returned invalid JSON.") from exc

    # ------------------------------------------------------------------
    # Smart lock discovery
    # ------------------------------------------------------------------

    async def list_smartlocks(self) -> list[dict[str, Any]]:
        """Return all smart locks visible to the current token."""
        data = await self._request("GET", "/smartlock")
        if not isinstance(data, list):
            raise NukiApiError("Unexpected smartlock list format.")
        return data

    # ------------------------------------------------------------------
    # Decentral webhook management
    # ------------------------------------------------------------------

    async def list_decentral_webhooks(self) -> list[DecentralWebhookRecord]:
        """List all registered decentral webhooks."""
        data = await self._request("GET", "/api/decentralWebhook")
        if not isinstance(data, list):
            raise NukiApiError("Unexpected decentral webhook list format.")
        return [DecentralWebhookRecord.model_validate(item) for item in data]

    async def create_decentral_webhook(
        self,
        webhook_url: str,
        features: list[WebhookFeature],
    ) -> DecentralWebhookRegistration:
        """Register a new decentral webhook.

        Returns the registration including the per-webhook ``secret``
        that must be stored for signature verification.
        """
        payload = {
            "webhookUrl": webhook_url,
            "webhookFeatures": [f.value for f in features],
        }
        logger.info("Creating decentral webhook for %s", webhook_url)
        data = await self._request("PUT", "/api/decentralWebhook", json_body=payload)
        return DecentralWebhookRegistration.model_validate(data)

    async def delete_decentral_webhook(self, webhook_id: int) -> None:
        """Delete an existing decentral webhook."""
        logger.info("Deleting decentral webhook id=%s", webhook_id)
        await self._request("DELETE", f"/api/decentralWebhook/{webhook_id}")

    async def ensure_decentral_webhook(
        self,
        webhook_url: str,
        features: list[WebhookFeature],
    ) -> DecentralWebhookRegistration | DecentralWebhookRecord:
        """Idempotent webhook registration.

        Returns an existing webhook if URL and features match, or
        creates a new one.  If the URL exists with different features,
        the old registration is replaced.

        .. warning::

            This method is not concurrency-safe across multiple
            processes.  In a multi-worker deployment, run webhook
            registration as a one-time CLI task (``nuki-setup-webhook``)
            rather than at application startup.
        """
        existing = await self.list_decentral_webhooks()
        desired = {f.value for f in features}

        for wh in existing:
            if wh.webhookUrl != webhook_url:
                continue
            if set(wh.webhookFeatures) == desired:
                logger.info("Matching decentral webhook already exists (id=%s)", wh.id)
                return wh
            logger.warning(
                "Webhook URL exists with different features — replacing id=%s",
                wh.id,
            )
            await self.delete_decentral_webhook(wh.id)
            break

        return await self.create_decentral_webhook(webhook_url, features)
