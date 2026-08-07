# Twenty4Seven-Gym

Booking-driven 24/7 gym access platform for GETIMPULSE BERLIN.

**Stack:** FastAPI · PostgreSQL · Magicline · Nuki Pro · Docker Compose

## What It Does

- Syncs member bookings from Magicline every 5 min (`MAGICLINE_SYNC_INTERVAL_MINUTES`, default `5`)
- Provisions Nuki smartlock keypad codes for members with upcoming "Freies Training" bookings
- Sends access codes via email and Telegram
- Web-based check-in / check-out funnel with configurable steps (house rules, yes/no, NPS, video)
- Admin UI for managing funnels, access windows, settings, and audit logs
- Permanent per-member check-in URL via `?key=<uuid>` — no login required

## Quick Start (local / fresh install)

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up -d --build
```

## Production

> Production is **not** driven by the `docker-compose.yml` in this repo. The live stack
> is defined in **`/opt/getimpulse/docker-compose.yml`** (Compose project `getimpulse`)
> and runs as the containers `opengym-service`, `opengym-worker` and `db-service`.
>
> The source is **baked into the image** (`COPY src` + `pip install`) — there is no
> source bind-mount. Therefore a restart or a plain `docker compose up -d` does **not**
> ship new code; only a rebuild (`docker compose build` / `up -d --build`) is a deploy,
> and a deploy is approval-gated.
>
> Operations, restart vs. deploy, backup/restore, the emergency flags
> `NUKI_ROTATION_PAUSED` / `NUKI_REQUIRE_DEVICE_CONFIRMATION` and the escalation path
> are documented in [`docs/BETRIEB.md`](docs/BETRIEB.md).

## Branching

- `main` — production branch; everything that is live is an ancestor of `main`.
- `fix/**` — short-lived fix branches, merged into `main` (fast-forward preferred).
- CI (`.github/workflows/tests.yml`) runs pytest on push to `main` / `fix/**` and on
  PRs. It runs **tests only — it never deploys.**

| URL | Description |
|-----|-------------|
| `/app` | Admin interface |
| `/checks?key=<uuid>` | Member check-in / check-out (permanent link) |
| `/checks?token=<jwt>` | Member check-in via time-limited token |

## Services

Local (`docker-compose.yml` in this repo):

| Service | Container | Description |
|---------|-----------|-------------|
| `db` | `twenty4seven-gym-db` | PostgreSQL 16 |
| `web` | `twenty4seven-gym-web` | FastAPI on port 8080 |
| `worker` | `twenty4seven-gym-worker` | Background sync + code provisioning loop |

Production (`/opt/getimpulse/docker-compose.yml`, project `getimpulse`) — different
names and a shared database server:

| Container | Description |
|-----------|-------------|
| `db-service` | PostgreSQL 15, database `opengym` (shared with the other getimpulse services) |
| `opengym-service` | FastAPI, no published ports — reachable only via the api-gateway |
| `opengym-worker` | Background sync + rotation + delivery loop |

## Project Structure

```
src/nuki_integration/
├── app.py                  # FastAPI routes
├── worker.py               # Background sync + provisioning
├── db.py                   # PostgreSQL persistence
├── magicline.py            # Magicline API client
├── nuki_client.py          # Nuki Web API client
├── notifications.py        # SMTP + Telegram delivery
├── models.py               # Pydantic models
├── services/
│   ├── access.py           # Code lifecycle (provision / deprovision)
│   ├── sync.py             # Magicline sync, access window clustering
│   ├── checks.py           # Check-in / check-out funnel logic
│   ├── auth_tokens.py      # JWT + permanent ?key= URL generation
│   ├── email_builder.py    # Email template assembly
│   ├── settings.py         # Runtime config resolution
│   └── ...
└── static/
    ├── index.html
    └── assets/
        ├── admin.css       # Warm Minimal design system
        └── app.js          # Admin + member UI (single-page)
```

## Environment Variables

See `.env.example` for all required variables. Key settings:

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `MAGICLINE_API_KEY` | Magicline studio API key |
| `MAGICLINE_STUDIO_ID` | Studio ID |
| `NUKI_API_TOKEN` | Nuki Web API token |
| `NUKI_DEVICE_ID` | Nuki smartlock device ID |
| `APP_PUBLIC_BASE_URL` | Public base URL (used in emails/links) |
| `SMTP_*` | SMTP credentials for email delivery |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (optional) |
