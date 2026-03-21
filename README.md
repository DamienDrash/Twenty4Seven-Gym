# Nuki Integration — Production-Grade Webhook Receiver

Secure, production-ready integration for the Nuki Web API with:

- **HMAC-SHA256 signature verification** (central and decentral modes)
- **Two-tier deduplication**: byte-level (raw hash) + semantic (smartlockId:feature:timestamp)
- **Event lifecycle tracking**: received → processing → processed | failed
- **Replay protection** via configurable timestamp window
- **Silent smart-lock filtering** (uniform 202 — no authorization leaks)
- **OAuth2 token management** with proactive refresh at 75% TTL
- **Deep health checks** with write/read/delete DB verification
- **SQLite WAL mode** for concurrent read/write safety
- **Stale-event recovery** for crashed background tasks

## Architecture

```
Nuki Cloud
    │
    ▼  POST /webhooks/nuki
┌─────────────────────────────────────────────┐
│  FastAPI Webhook Receiver                    │
│                                              │
│  1. HMAC signature verification              │
│  2. Optional shared-secret check             │
│  3. Raw body → byte-hash duplicate check     │
│  4. JSON parse (permissive envelope)         │
│  5. Timestamp replay check                   │
│  6. Smart-lock allowlist (silent filter)     │
│  7. Semantic-key dedup (INSERT OR IGNORE)    │
│  8. Return 202 immediately                   │
│  9. Background: processing → processed|failed│
└─────────────────────────────────────────────┘
    │
    ▼  SQLite (WAL mode)
┌─────────────────────────────────────────────┐
│  webhook_events table                        │
│  - idempotency_key (PK, semantic)            │
│  - raw_hash (index, byte-level)              │
│  - status: received|processing|processed|failed│
│  - error_detail (for failed events)          │
│  - updated_at (for stale detection)          │
└─────────────────────────────────────────────┘
```

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# Fill in NUKI_CLIENT_SECRET or NUKI_DECENTRAL_WEBHOOK_SECRET,
# ALLOWED_SMARTLOCK_IDS, etc.

# Register the decentral webhook (one-time):
nuki-setup-webhook

# Start the receiver:
uvicorn nuki_integration.webhook_service:app --host 0.0.0.0 --port 8080
```

## Webhook Modes

### Central Webhooks
- Configured in Nuki Web via OAuth2 approval flow.
- Signature verified with `NUKI_CLIENT_SECRET`.
- Set `NUKI_WEBHOOK_MODE=central`.

### Decentral Webhooks
- Registered via API (`PUT /api/decentralWebhook`).
- Signature verified with the per-registration `secret`.
- Set `NUKI_WEBHOOK_MODE=decentral`.
- Store the secret from registration as `NUKI_DECENTRAL_WEBHOOK_SECRET`.

## Security Design Decisions

### Uniform 202 Responses
All structurally valid, signature-verified requests receive HTTP 202 —
including events for non-allowed locks.  This prevents:
- **Information leakage**: attackers cannot enumerate valid lock IDs.
- **Nuki delivery penalties**: non-2xx responses count toward the 5%
  error threshold that triggers warnings and eventual suspension.

Only signature failures return 401 (these are not from Nuki).

### Two-Tier Deduplication
1. **Raw hash** (SHA-256 of exact bytes): catches network-level retries
   where the payload is byte-identical.
2. **Semantic key** (`smartlockId:feature:timestamp`): catches logically
   identical events that differ at the byte level (e.g., Nuki adds a
   new field, JSON key ordering changes).

### Event Lifecycle Tracking
The original design had no way to distinguish "received and queued" from
"business logic completed."  Now every event transitions through:

```
received → processing → processed
                     └→ failed (with error_detail)
```

A recovery sweep finds stale `processing` events (crashed workers) and
resets them to `received` for retry.

## Health Endpoints

| Endpoint | Purpose | Checks |
|----------|---------|--------|
| `GET /healthz/live` | Liveness probe | Process alive (no deps) |
| `GET /healthz/ready` | Readiness probe | Full DB write/read/delete cycle |

**Why separate probes**: a temporary DB issue should stop traffic
(readiness → fail) but not restart the container (liveness → still ok).

## OAuth2 Token Management

Nuki issues 1-hour access tokens and 90-day refresh tokens.  The
`OAuthTokenManager` refreshes proactively at 75% of TTL (~45 min)
using an async lock to prevent concurrent refresh races.

When the refresh token expires (90 days) or is invalidated by Nuki
(re-auth from another device), the service logs a `CRITICAL` alert
and raises `TokenRefreshError`.  The operator must re-authorize.

## Production Deployment

### Required
- **HTTPS with TLS termination** via reverse proxy (nginx, Caddy, Traefik).
- **ALLOWED_SMARTLOCK_IDS** set to the specific locks you manage.
- **Signature verification** — never disable in production.

### Recommended
- **PostgreSQL** instead of SQLite for multi-worker deployments.
- **Dramatiq or Celery** queue workers instead of FastAPI BackgroundTasks.
- **Vault / AWS Secrets Manager** for secrets instead of `.env` files.
- **Secret rotation** on a 90-day cycle with dual-key overlap.
- **Structured log aggregation** (Loki, Datadog, ELK).
- **Alerting** on:
  - Events stuck in `processing` > 10 minutes.
  - No `DEVICE_STATUS` received for > 24 hours per lock.
  - `failed` events accumulating.
  - OAuth2 token refresh failures.

### Nuki Operational Constraints
- Webhook error rate > 5% in 24h → Nuki sends warning email.
- 100% error rate sustained → Nuki suspends the webhook URL.
- Only HTTP 200, 202, 204 count as successful delivery.
- Reactivation: `POST /api/key/{apiKeyId}/advanced/reactivate`.
- Polling intervals < 30s are explicitly discouraged by Nuki.
- Manual sync (`POST /smartlock/{id}/sync`) drains lock batteries.

## Project Structure

```
src/nuki_integration/
├── __init__.py
├── config.py           # Settings with cached_property, HTTPS enforcement
├── enums.py            # Nuki states, webhook features, event lifecycle
├── exceptions.py       # Typed exception hierarchy
├── logging_setup.py    # Structured JSON logging
├── models.py           # Pydantic models with semantic idempotency key
├── nuki_client.py      # Async Nuki Web API client
├── oauth.py            # OAuth2 token refresh with async lock
├── security.py         # HMAC verification, replay protection, hashing
├── storage.py          # SQLite with WAL, lifecycle tracking, health check
├── setup_webhook.py    # CLI for one-time webhook registration
└── webhook_service.py  # FastAPI application
```

## Standards Compliance

This implementation addresses requirements from:

- **NIST SP 800-213**: securability, data protection at rest/in transit,
  logical access control, cybersecurity state awareness (audit logging).
- **ETSI EN 303 645**: encrypted communications (5.5), secure storage (5.4),
  minimized attack surface (5.6), input validation (5.13).
- **ETSI TS 103 815**: smart door lock vertical — mandatory encrypted and
  authenticated communications between all components.
- **OWASP API Security 2023**: BOLA mitigation (lock-level allowlist),
  authentication hardening (OAuth2 with refresh), security configuration
  (no docs in prod, structured errors, security headers).
- **OWASP REST Security**: HTTPS-only, strict input typing, rate-limit
  awareness.
