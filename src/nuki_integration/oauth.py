from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OAuthTokenManager:
    """Minimal placeholder for legacy compatibility in the new package layout."""

    token_url: str
    client_id: str
    client_secret: str
    initial_access_token: str
    initial_refresh_token: str

    async def get_access_token(self) -> str:
        return self.initial_access_token

    async def close(self) -> None:
        return None
