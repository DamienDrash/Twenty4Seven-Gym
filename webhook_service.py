"""FastAPI application: Nuki webhook receiver.

Processing pipeline (per request)
---------------------------------
1. Read raw body bytes (before any parsing).
2. Verify ``X-Nuki-Signature-SHA256`` HMAC against raw body.
3. Verify optional ``X-Webhook-Token`` shared secret.
4. Byte-level duplicate check (fast-path rejection for network retries).
5. Parse JSON into ``NukiWebhookEvent`` (permissive envelope).
6. Validate event timestamp against replay window.
7. Check local smart-lock allowlist.
8. Semantic-key deduplication (insert-or-ignore on idempotency_key).
9. Return ``202 Accepted`` immediately.
10. Process event in a background task with status tracking.

Security decisions documented
------------------------------
* **Uniform 202 for all accepted-or-filtered events**: returning 403
  for unauthorized locks leaks which IDs are valid.  Nuki counts
  non-2xx as errors toward the 5 % suspension threshold.
* **Signature verification on raw bytes**: parsing first and re-
  serializing would break the HMAC on any key-order or whitespace
  difference.
* **Two-tier deduplication**: raw_hash catches byte-identical retries;
  semantic_key catches logically identical events with different
  serialization.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Request, Response, status

from .config import Settings, get_settings
from .enums import EventStatus
from .exceptions import (
    ReplayAttackError,
    WebhookVerificationError,
)
from .logging_setup import configure_logging
from .models import NukiWebhookEvent, StoredEvent
from .security import SignatureVerifier, raw_body_hash, validate_event_age
from .storage import EventStore

# ------------------------------------------------------------------
# Bootstrap
# ------------------------------------------------------------------

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

event_store = EventStore(settings.sqlite_path)
verifier = SignatureVerifier(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup and shutdown hooks."""
    logger.info(
        "Nuki webhook receiver starting (mode=%s, env=%s)",
        settings.nuki_webhook_mode,
        settings.app_env,
    )
    yield
    logger.info("Nuki webhook receiver shutting down")


app = FastAPI(
    title="Nuki Webhook Receiver",
    version="2.0.0",
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None,
    lifespan=lifespan,
)


# ------------------------------------------------------------------
# Health endpoints
# ------------------------------------------------------------------

@app.get("/healthz/live", status_code=200, response_model=None)
async def liveness() -> dict[str, str] | Response:
    """Liveness probe: is the process alive?

    No dependency checks — a failing dependency should stop traffic
    (readiness), not restart the container (liveness).
    """
    if not settings.enable_healthcheck:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return {"status": "alive"}


@app.get("/healthz/ready", status_code=200, response_model=None)
async def readiness() -> dict[str, str] | Response:
    """Readiness probe: can the service accept webhook traffic?

    Performs a full write/read/delete cycle against SQLite to catch
    filesystem issues (full disk, permission errors, corrupt WAL).
    """
    if not settings.enable_healthcheck:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    if not event_store.health_check():
        return Response(
            content='{"status":"unhealthy","detail":"storage check failed"}',
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )
    return {"status": "ready"}


# ------------------------------------------------------------------
# Authorization helpers
# ------------------------------------------------------------------

def _is_lock_allowed(event: NukiWebhookEvent) -> bool:
    """Check the event against the local smart-lock allowlist.

    Returns ``True`` unconditionally when the allowlist is empty
    (accept all).  Returns ``False`` for events without a lock ID
    when the allowlist is non-empty.
    """
    if not settings.allowed_smartlock_ids:
        return True
    if event.smartlockId is None:
        return False
    return event.smartlockId in settings.allowed_smartlock_ids


# ------------------------------------------------------------------
# Event processing (background task)
# ------------------------------------------------------------------

def _process_event(event: NukiWebhookEvent, idempotency_key: str) -> None:
    """Execute business logic for a verified, deduplicated event.

    The full lifecycle is tracked in SQLite:

        received → processing → processed | failed

    If this function raises, the event is marked ``failed`` with the
    error message preserved for diagnostics.  A background sweep can
    find and retry ``failed`` or stale ``processing`` events.
    """
    if not event_store.mark_processing(idempotency_key):
        logger.debug("Event already processing or completed: %s", idempotency_key)
        return

    try:
        _handle_event(event)
        event_store.mark_processed(idempotency_key)
    except Exception as exc:
        logger.exception(
            "Event processing failed for %s", idempotency_key,
        )
        event_store.mark_failed(idempotency_key, str(exc)[:2000])


def _handle_event(event: NukiWebhookEvent) -> None:
    """Dispatch by feature type.

    This is the extension point for business logic.  In production,
    push to a durable queue (Dramatiq, Celery, RQ) instead of
    processing inline.
    """
    if event.feature == "DEVICE_STATUS":
        _handle_device_status(event)
    elif event.feature == "DEVICE_LOGS":
        logger.info(
            "Device log event: smartlock_id=%s",
            event.smartlockId,
        )
    else:
        logger.info("Unhandled feature=%s for smartlock_id=%s", event.feature, event.smartlockId)


def _handle_device_status(event: NukiWebhookEvent) -> None:
    """Process DEVICE_STATUS events with safety-critical checks."""
    state = event.state
    if not isinstance(state, dict):
        logger.warning("DEVICE_STATUS without dict state for smartlock_id=%s", event.smartlockId)
        return

    lock_state = state.get("state")
    door_state = state.get("doorState")
    trigger = state.get("trigger")
    last_action = state.get("lastAction")

    logger.info(
        "DEVICE_STATUS: smartlock_id=%s state=%s door=%s trigger=%s action=%s",
        event.smartlockId, lock_state, door_state, trigger, last_action,
    )

    # Safety: battery critical
    if state.get("batteryCritical") is True:
        logger.warning(
            "CRITICAL BATTERY: smartlock_id=%s — schedule replacement",
            event.smartlockId,
        )

    # Safety: door physically open but lock reports locked
    # (possible sensor issue or forced entry)
    if door_state == 3 and lock_state == 1:
        logger.warning(
            "ANOMALY: door open but lock reports locked — "
            "smartlock_id=%s — investigate sensor or forced entry",
            event.smartlockId,
        )

    # Safety: remote unlock outside expected hours (placeholder)
    if trigger == 6 and last_action == 1:
        logger.info(
            "Remote unlock detected: smartlock_id=%s — "
            "verify against access schedule",
            event.smartlockId,
        )


# ------------------------------------------------------------------
# Webhook endpoint
# ------------------------------------------------------------------

@app.post("/webhooks/nuki", status_code=202, response_model=None)
async def webhook_receiver(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Receive, verify, deduplicate, and dispatch a Nuki webhook.

    Returns 202 for all structurally valid requests — including
    filtered-out locks — to avoid leaking authorization information
    and to stay within Nuki's 2xx delivery-success criteria.

    Returns 401 only for signature failures (these are not legitimate
    Nuki requests and should not count against the error budget).
    """
    raw = await request.body()

    # ---- signature verification FIRST ----
    # Must precede all other logic: unauthenticated requests must not
    # touch the database or reveal any internal state.
    try:
        verifier.verify_signature(
            raw_body=raw,
            received_signature=request.headers.get("X-Nuki-Signature-SHA256"),
        )
        verifier.verify_shared_secret(
            request.headers.get("X-Webhook-Token"),
        )
    except WebhookVerificationError as exc:
        logger.warning("Signature verification failed: %s", exc)
        return Response(
            content='{"detail":"verification failed"}',
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="application/json",
        )

    # ---- fast-path: byte-identical duplicate ----
    body_hash = raw_body_hash(raw)
    if event_store.is_raw_duplicate(body_hash):
        logger.debug("Byte-identical duplicate rejected: %s", body_hash[:16])
        return {"status": "duplicate_ignored"}

    # ---- parse payload ----
    try:
        event = NukiWebhookEvent.model_validate_json(raw)
    except Exception:
        logger.warning("Malformed webhook JSON payload")
        # Return 202 (not 400) — Nuki would log a 400 as a delivery
        # failure; a malformed payload from a verified signature is
        # either a Nuki bug or a schema evolution we haven't caught.
        return {"status": "parse_error_accepted"}

    # ---- replay protection ----
    try:
        validate_event_age(
            event_timestamp=event.timestamp,
            max_age_seconds=settings.max_event_age_seconds,
        )
    except ReplayAttackError as exc:
        logger.warning("Replay protection: %s", exc)
        # 202 — the signature was valid so this is likely clock skew,
        # not an attack.  Log it and don't penalise Nuki's delivery.
        return {"status": "replay_rejected"}

    # ---- local authorization (silent filter) ----
    if not _is_lock_allowed(event):
        logger.info(
            "Filtered event for non-allowed smartlock_id=%s",
            event.smartlockId,
        )
        return {"status": "filtered"}

    # ---- semantic deduplication ----
    idem_key = event.semantic_key
    now = datetime.now(UTC)

    inserted = event_store.try_insert(
        StoredEvent(
            idempotency_key=idem_key,
            raw_hash=body_hash,
            received_at=now,
            status=EventStatus.RECEIVED,
            feature=event.feature,
            smartlock_id=event.smartlockId,
            event_timestamp=event.timestamp,
        )
    )

    if not inserted:
        logger.debug("Semantic duplicate ignored: %s", idem_key)
        return {"status": "duplicate_ignored"}

    # ---- schedule processing ----
    background_tasks.add_task(_process_event, event, idem_key)
    return {"status": "accepted"}
