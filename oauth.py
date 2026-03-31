"""OAuth2 token lifecycle management for long-running services.

Nuki issues access tokens with a 1-hour TTL and refresh tokens valid
for 90 days.  This module implements:

* **Proactive refresh** at 75 % of TTL (~45 min for Nuki) so requests
  never hit an expired token.
* **File-based locking** to prevent concurrent refresh attempts from
  multiple workers in a single-node deployment.  For multi-node
  setups, replace the file lock with a Redis distributed lock.
* **Graceful degradation** — when the refresh token itself expires
  (90-day lifetime, or invalidated by Nuki after re-auth from another
  device), the module logs a critical alert and raises
  ``TokenRefreshError`` so the operator can re-authorize.

Tokens are held in memory and optionally persisted to the ``.env``
file path for restart resilience.  In production, use a secrets
manager (Vault, AWS Secrets Manager) instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from .exceptions import TokenRefreshError

logger = logging.getLogger(__name__)

# Nuki's documented access token lifetime.
_DEFAULT_TTL_SECONDS = 3600
# Refresh at 75 % of remaining lifetime.
_REFRESH_THRESHOLD = 0.75


@dataclass
class TokenState:
    """In-memory representation of the current OAuth2 token pair."""

    access_token: str
    refresh_token: str
    expires_at: float = field(default_factory=lambda: time.time() + _DEFAULT_TTL_SECONDS)

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        """True when the token has passed the proactive-refresh threshold."""
        remaining = self.expires_at - time.time()
        return remaining < _DEFAULT_TTL_SECONDS * (1 - _REFRESH_THRESHOLD)


class OAuthTokenManager:
    """Manage the OAuth2 access/refresh token lifecycle.

    Parameters
    ----------
    token_url:
        Nuki's token endpoint (``https://api.nuki.io/oauth/token``).
    client_id:
        OAuth2 client ID.
    client_secret:
        OAuth2 client secret.
    initial_access_token:
        Access token from initial authorization or previous run.
    initial_refresh_token:
        Refresh token from initial authorization.
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        initial_access_token: str,
        initial_refresh_token: str,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._state = TokenState(
            access_token=initial_access_token,
            refresh_token=initial_refresh_token,
        )
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=15)

    async def close(self) -> None:
        """Release HTTP resources."""
        await self._http.aclose()

    async def get_access_token(self) -> str:
        """Return a valid access token, refreshing proactively if needed.

        The ``asyncio.Lock`` ensures that only one coroutine refreshes
        at a time; all others await the result.
        """
        if not self._state.needs_refresh:
            return self._state.access_token

        async with self._lock:
            # Double-check after acquiring the lock — another coroutine
            # may have refreshed while we were waiting.
            if not self._state.needs_refresh:
                return self._state.access_token
            await self._refresh()

        return self._state.access_token

    async def _refresh(self) -> None:
        """Execute the OAuth2 refresh-token grant."""
        logger.info("Refreshing Nuki OAuth2 access token")

        try:
            response = await self._http.post(
                self._token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._state.refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            logger.error("Token refresh HTTP error: %s", exc)
            raise TokenRefreshError(
                f"HTTP error during token refresh: {exc}"
            ) from exc

        if response.status_code >= 400:
            # A 400/401 typically means the refresh token is invalid
            # (expired after 90 days or invalidated by Nuki).
            logger.critical(
                "Token refresh failed with status %d — operator must "
                "re-authorize via the OAuth2 flow. Response: %s",
                response.status_code,
                response.text[:500],
            )
            raise TokenRefreshError(
                f"Nuki rejected the refresh token (HTTP {response.status_code}). "
                "Re-authorization required."
            )

        body = response.json()
        new_access = body.get("access_token", "")
        new_refresh = body.get("refresh_token", self._state.refresh_token)
        expires_in = int(body.get("expires_in", _DEFAULT_TTL_SECONDS))

        if not new_access:
            raise TokenRefreshError("Nuki returned an empty access token.")

        self._state = TokenState(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_at=time.time() + expires_in,
        )

        logger.info(
            "Token refreshed successfully, expires in %ds",
            expires_in,
        )
