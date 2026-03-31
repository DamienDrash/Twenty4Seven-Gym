"""Security helpers: signature verification, replay protection, hashing.

Design decisions
----------------
* Signature verification operates on the **raw request body** — never
  on re-serialised JSON — to avoid HMAC mismatches caused by key
  ordering or whitespace normalisation.
* Replay protection uses the event-level ``timestamp`` from Nuki's
  payload.  The window is intentionally configurable because clock
  skew between Nuki's infrastructure and the receiver can vary.
* Two hashing strategies coexist:
  - ``raw_body_hash``: SHA-256 of the exact payload bytes for
    byte-level duplicate detection (network retries).
  - ``NukiWebhookEvent.semantic_key``: business-level key for
    logical duplicate detection (same event, different serialisation).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from .config import Settings
from .enums import WebhookMode
from .exceptions import ReplayAttackError, WebhookVerificationError


# ------------------------------------------------------------------
# Signature verification
# ------------------------------------------------------------------

class SignatureVerifier:
    """Verify inbound Nuki webhook signatures.

    For **central** webhooks the HMAC key is the OAuth2 Client Secret.
    For **decentral** webhooks the key is the per-registration secret
    returned by ``PUT /api/decentralWebhook``.

    Reference
    ---------
    Nuki Web API Webhooks v1.1, §3.1 "Signature verification"
    Header: ``X-Nuki-Signature-SHA256`` (replaced deprecated
    ``X-Nuki-Signature`` since May 2021).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _signing_secret(self) -> str:
        if self._settings.nuki_webhook_mode == WebhookMode.CENTRAL:
            secret = self._settings.nuki_client_secret
            if not secret:
                raise WebhookVerificationError(
                    "NUKI_CLIENT_SECRET is required for central webhook verification."
                )
            return secret

        secret = self._settings.nuki_decentral_webhook_secret
        if not secret:
            raise WebhookVerificationError(
                "NUKI_DECENTRAL_WEBHOOK_SECRET is required for decentral "
                "webhook verification."
            )
        return secret

    def verify_signature(
        self,
        raw_body: bytes,
        received_signature: str | None,
    ) -> None:
        """Verify the ``X-Nuki-Signature-SHA256`` HMAC.

        Parameters
        ----------
        raw_body:
            Exact bytes from the HTTP request body.
        received_signature:
            Value of the ``X-Nuki-Signature-SHA256`` header.

        Raises
        ------
        WebhookVerificationError
            If the header is missing or the digest does not match.
        """
        if not received_signature:
            raise WebhookVerificationError(
                "Missing X-Nuki-Signature-SHA256 header."
            )

        key = self._signing_secret().encode("utf-8")
        expected = hmac.new(key, raw_body, hashlib.sha256).hexdigest()

        # Timing-safe comparison prevents side-channel leaks.
        if not hmac.compare_digest(expected, received_signature):
            raise WebhookVerificationError("Invalid webhook signature.")

    def verify_shared_secret(self, received: str | None) -> None:
        """Verify the optional ``X-Webhook-Token`` defense-in-depth header.

        No-op when ``INBOUND_SHARED_SECRET`` is not configured.
        """
        configured = self._settings.inbound_shared_secret
        if not configured:
            return
        if not received:
            raise WebhookVerificationError("Missing X-Webhook-Token header.")
        if not hmac.compare_digest(configured, received):
            raise WebhookVerificationError("Invalid X-Webhook-Token value.")


# ------------------------------------------------------------------
# Hashing
# ------------------------------------------------------------------

def raw_body_hash(raw_body: bytes) -> str:
    """SHA-256 hex digest of the exact request payload.

    Used alongside the semantic key for two-tier deduplication:
    the raw hash catches byte-identical retries immediately, while
    the semantic key catches logically identical events that differ
    at the byte level.
    """
    return hashlib.sha256(raw_body).hexdigest()


# ------------------------------------------------------------------
# Replay protection
# ------------------------------------------------------------------

def validate_event_age(
    event_timestamp: datetime | None,
    max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> None:
    """Reject events whose timestamp is too old or too far in the future.

    Parameters
    ----------
    event_timestamp:
        ``timestamp`` field from the Nuki webhook payload.
    max_age_seconds:
        Permitted clock-skew window in seconds (both directions).
    now:
        Override for testing; defaults to ``datetime.now(UTC)``.

    Raises
    ------
    ReplayAttackError
        If the event falls outside the window.

    Notes
    -----
    A missing timestamp is tolerated (some Nuki features may omit it)
    but logged as a warning at the call site.
    """
    if event_timestamp is None:
        return

    current = now or datetime.now(UTC)

    # Normalise naive timestamps to UTC.
    if event_timestamp.tzinfo is None:
        event_timestamp = event_timestamp.replace(tzinfo=UTC)

    skew = abs((current - event_timestamp).total_seconds())
    if skew > max_age_seconds:
        raise ReplayAttackError(
            f"Event timestamp skew {skew:.0f}s exceeds the "
            f"{max_age_seconds}s permitted window."
        )
