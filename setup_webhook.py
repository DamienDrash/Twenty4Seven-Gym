"""CLI tool for decentral webhook registration.

Run as a one-time setup task — **not** at application startup in a
multi-worker deployment — to avoid race conditions between workers
simultaneously trying to register webhooks.

Usage
-----
.. code-block:: bash

    # Via entry point (after pip install -e .):
    nuki-setup-webhook

    # Direct execution:
    python -m nuki_integration.setup_webhook
"""

from __future__ import annotations

import asyncio
import json
import sys

from .config import get_settings
from .nuki_client import NukiWebClient
from .oauth import OAuthTokenManager


async def _run() -> None:
    settings = get_settings()

    if not settings.public_webhook_url:
        print("ERROR: PUBLIC_WEBHOOK_URL must be configured.", file=sys.stderr)
        sys.exit(1)

    # Build token manager if OAuth2 is configured.
    token_manager: OAuthTokenManager | None = None
    if settings.nuki_refresh_token and settings.nuki_client_id:
        token_manager = OAuthTokenManager(
            token_url=f"{settings.nuki_base_url}/oauth/token",
            client_id=settings.nuki_client_id,
            client_secret=settings.nuki_client_secret,
            initial_access_token=settings.nuki_access_token,
            initial_refresh_token=settings.nuki_refresh_token,
        )

    client = NukiWebClient(settings, token_manager=token_manager)
    try:
        result = await client.ensure_decentral_webhook(
            webhook_url=settings.public_webhook_url,
            features=list(settings.nuki_webhook_features),
        )
        print("Webhook registration result:")
        print(json.dumps(result.model_dump(), indent=2, default=str))

        # Remind operator to store the secret.
        if hasattr(result, "secret"):
            print(
                "\n⚠️  Store this secret as NUKI_DECENTRAL_WEBHOOK_SECRET "
                "in your .env — it is required for signature verification "
                "and cannot be retrieved again."
            )
    finally:
        await client.close()


def main() -> None:
    """Synchronous entry point."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
