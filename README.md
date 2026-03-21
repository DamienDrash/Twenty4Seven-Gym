# Twenty4Seven-Gym

<div align="center">

**Responsive gym access web app for Magicline, Nuki Pro, and Synology**

[![FastAPI](https://img.shields.io/badge/FastAPI-admin%20%2B%20API-0f766e?style=flat-square)](#)
[![Magicline](https://img.shields.io/badge/Magicline-bookings%20%26%20webhooks-111827?style=flat-square)](#)
[![Nuki](https://img.shields.io/badge/Nuki-Pro%20%2B%20Keypad%202-16a34a?style=flat-square)](#)
[![Postgres](https://img.shields.io/badge/Postgres-required-1d4ed8?style=flat-square)](#)
[![Synology](https://img.shields.io/badge/Synology-DS723%2B%20target-f59e0b?style=flat-square)](#)

</div>

Twenty4Seven-Gym is a booking-driven access platform for fitness studios. It provides a responsive admin web app, a public member check-in flow, Magicline synchronization, Nuki code lifecycle management, SMTP notifications, and operational tooling for running 24/7 access on a Synology NAS.

> [!IMPORTANT]
> The source of truth for access is a real Magicline booking of `Freies Training`. This project runs with **PostgreSQL only** and is designed to be deployed via **Docker Compose**.

## What This Project Actually Is

This repository contains the full application, not just an integration module:

- a **responsive admin web app** under `/app`
- a **public member check-in funnel** under `/check-in`
- a **FastAPI backend** with auth, settings, audit, member views, access-window actions, and diagnostics
- a **background worker** for Magicline polling, provisioning, notifications, and retries
- a **Nuki adapter** with dry-run and live paths
- a **Magicline webhook endpoint** for short-notice bookings and changes

The active application code lives in [`src/nuki_integration`](./src/nuki_integration).

## Core User Flows

### Admin / Operator

- log in to the `Twenty4Seven-Gym` operations console
- search members and inspect bookings, access windows, codes, alerts, and audit logs
- resend codes, deactivate access early, issue emergency codes
- configure SMTP, Telegram, and the public check-in content
- inspect lock status and remote-open audit events

### Member

- books `Freies Training` in Magicline
- receives a personal access code by email before the slot
- opens the public check-in link from the mail or scans the studio QR code
- completes the house-rules and checklist funnel before the training block

## System Flow

```text
Magicline booking / webhook
        ->
member sync + booking sync
        ->
access window calculation
        ->
worker schedules due provisioning
        ->
email delivery + Nuki code create/update
        ->
public member check-in funnel
        ->
training slot
```

## Main Features

- Magicline polling sync plus webhook fast path
- booking clustering for adjacent `Freies Training` slots of the same member
- one operational access lifecycle per consolidated member block
- timed lifecycle:
  - code dispatch at `T-15 minutes`
  - validity until `slot end + 30 minutes`
- Admin and Operator roles with local auth
- self-service password reset
- SMTP test flow and configurable mail settings
- optional Telegram alerts for warnings, errors, and emergency-code creation
- public QR + mail-link based pre-use check-in flow
- Synology-friendly Docker Compose deployment

## Application Surfaces

### Admin Console

Primary UI sections currently implemented:

- `Betrieb`
- `Mitglieder`
- `Access Windows`
- `Schloss`
- `Alerts & Audit`
- `Einstellungen`

The UI is responsive and intended to work on desktop and smartphone, with the operational console optimized for fast intervention.

### Public Check-in

The public member page is intentionally separate from the admin console:

- no admin navigation
- no admin login hints
- same design language as the operations UI
- mobile-first funnel for:
  - training block context
  - house rules
  - checklist
  - final confirmation

## HTTP Routes

Important routes exposed by the app:

- `/app` — admin web app shell
- `/check-in` — public member check-in shell
- `/reset-password` — public password reset shell
- `/admin/...` — authenticated admin and operator API
- `/public/check-in/...` — public check-in API
- `/webhooks/magicline` and `/webhook/magicline` — Magicline webhook ingestion
- `/healthz/live`
- `/healthz/ready`
- `/docs`

## Project Structure

```text
src/nuki_integration/
├── app.py              # FastAPI routes and web entrypoints
├── worker.py           # background worker entrypoint
├── db.py               # PostgreSQL schema, queries, and persistence
├── services.py         # access logic, sync orchestration, notifications, check-in
├── magicline.py        # Magicline API client and booking mapping
├── nuki_client.py      # Nuki adapter and dry-run/live behavior
├── notifications.py    # SMTP and Telegram delivery
├── models.py           # request/response and domain models
└── static/
    ├── index.html      # app shell
    ├── app.js          # admin UI + public check-in UI
    └── admin.css       # shared design language
```

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

cp .env.example .env
mkdir -p data/postgres
docker compose up -d --build
```

Useful commands:

```bash
ruff check src tests
pytest
docker compose ps
docker compose logs -f web worker
```

Health checks:

```bash
curl http://127.0.0.1:8080/healthz/live
curl http://127.0.0.1:8080/healthz/ready
```

## Configuration

Key environment variables:

- `APP_PUBLIC_BASE_URL`
- `DATABASE_URL`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `MAGICLINE_BASE_URL`
- `MAGICLINE_API_KEY`
- `MAGICLINE_WEBHOOK_API_KEY`
- `BOOTSTRAP_ADMIN_EMAIL`
- `BOOTSTRAP_ADMIN_PASSWORD`
- `JWT_SECRET`
- `SMTP_HOST`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `NUKI_DRY_RUN`

Use [`.env.example`](./.env.example) as the baseline.

## Synology DS723+ Deployment

Recommended installation path:

```bash
/volume1/docker/twenty4seven-gym
```

1. Install `Container Manager` on DSM.
2. Enable SSH on the NAS.
3. Clone this repository to the NAS.
4. Copy `.env.example` to `.env`.
5. Set strong values for:
   - `POSTGRES_PASSWORD`
   - `BOOTSTRAP_ADMIN_PASSWORD`
   - `JWT_SECRET`
6. Keep `NUKI_DRY_RUN=true` until live Nuki credentials are available.
7. Start the stack:

```bash
docker compose up -d --build
docker compose ps
```

Recommended reverse proxy rules on DSM:

- `/opengym` -> `http://127.0.0.1:8080`
- `/magicline/webhook` -> `http://127.0.0.1:8080`

> [!TIP]
> Persist Postgres on NAS storage through `POSTGRES_DATA_PATH`, for example `./data/postgres` inside the project directory on `/volume1`.

## Current Scope

Phase 1 covers:

- Magicline booking-driven access logic
- Nuki keypad code lifecycle
- email delivery
- admin and operator operations
- public member check-in funnel
- Synology deployment baseline

Later building automation topics such as cameras, presence sensors, and Shelly flows are intentionally out of scope for this phase.
