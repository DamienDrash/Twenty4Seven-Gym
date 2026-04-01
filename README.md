<p align="center">
  <img src="src/nuki_integration/static/assets/favicon.svg" width="72" height="72" alt="Twenty4Seven-Gym Logo" />
</p>

<h1 align="center">Twenty4Seven-Gym</h1>

<p align="center">
  <strong>Booking-driven 24/7 access platform for fitness studios.</strong><br />
  Magicline sync · Nuki keypad codes · Check-in funnels · Operator console
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL 16" />
  <img src="https://img.shields.io/badge/Docker_Compose-ready-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker Compose" />
  <img src="https://img.shields.io/badge/license-proprietary-333?style=flat-square" alt="License" />
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-api-reference">API</a> ·
  <a href="#-configuration">Configuration</a> ·
  <a href="#-deployment">Deployment</a>
</p>

---

## 🎯 What It Does

Twenty4Seven-Gym transforms a staffed fitness studio into a **24/7 self-service facility** by bridging the gap between booking management and physical door access.

```
┌─────────────┐     sync      ┌───────────────────┐     codes     ┌──────────────┐
│  Magicline   │ ──────────▶  │  Twenty4Seven-Gym  │ ──────────▶  │  Nuki Pro    │
│  (Bookings)  │  ◀──webhook  │  (Access Engine)   │  ◀──status   │  + Keypad 2  │
└─────────────┘               └───────┬───────────┘               └──────────────┘
                                      │
                          ┌───────────┼───────────┐
                          ▼           ▼           ▼
                     📧 E-Mail   📱 /checks   🖥️ Admin UI
                     (Code +     (Check-in/   (Members,
                      Link)       Check-out)   Alerts, Lock)
```

**The core loop:** A member books a "Freies Training" slot in Magicline → the system evaluates entitlement → generates a personal Nuki keypad code → emails it 15 min before start → the member enters via Keypad → completes a check-in funnel → trains → completes check-out → code expires automatically.

---

## ⚡ Quick Start

```bash
# 1. Clone and configure
git clone <repo-url> && cd twenty4seven-gym
cp .env.example .env
# Edit .env with your credentials (see Configuration section)

# 2. Launch
docker compose up -d --build

# 3. Access
open http://127.0.0.1:8080/app       # Admin console
open http://127.0.0.1:8080/checks     # Member check-in/out
```

The bootstrap admin account is created from `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` in your `.env` on first startup.

---

## 🏗 Architecture

Twenty4Seven-Gym is a **modular monolith** split into two deployable services sharing a PostgreSQL database:

```
┌──────────────────────────────────────────────────────────┐
│                    Docker Compose                        │
│                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────┐ │
│  │   web            │  │   worker         │  │   db    │ │
│  │   ─────────────  │  │   ─────────────  │  │         │ │
│  │   FastAPI app    │  │   Sync loop      │  │  Pg 16  │ │
│  │   Admin UI/API   │  │   Provisioning   │  │         │ │
│  │   /checks shell  │  │   Expiry sweep   │  │         │ │
│  │   Webhook rx     │  │   Nuki cleanup   │  │         │ │
│  │   Static files   │  │   Alerting       │  │         │ │
│  └────────┬─────────┘  └────────┬─────────┘  └────┬────┘ │
│           └──────────────┬──────┘                  │      │
│                          │         PostgreSQL       │      │
│                          └─────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

### Service Responsibilities

| Service | Purpose | Entry Point |
|---------|---------|-------------|
| **web** | HTTP API, admin UI, member-facing `/checks` shell, Magicline webhooks | `uvicorn nuki_integration.app:app` |
| **worker** | Periodic Magicline sync, code provisioning, expiry sweep, Nuki code cleanup | `studio-access-worker` |
| **db** | PostgreSQL 16 with health checks | Standard Postgres image |

### Package Layout

```
src/nuki_integration/
├── app.py                      # FastAPI routes & lifespan
├── worker.py                   # Background sync + provisioning loop
├── db.py                       # PostgreSQL persistence (psycopg3 pool)
├── config.py                   # Pydantic Settings (env + .env)
├── auth.py                     # PBKDF2 password hashing, HMAC JWT
├── magicline.py                # Magicline API client
├── nuki_client.py              # Nuki Web API client (with dry-run)
├── notifications.py            # SMTP + Telegram delivery
├── models.py                   # Pydantic request/response models
├── enums.py                    # Status enums (roles, windows, codes)
├── exceptions.py               # Typed exception hierarchy
├── dependencies.py             # FastAPI DI (DB pool, settings)
├── services/                   # Business logic (12 modules)
│   ├── __init__.py             # Re-exports for clean imports
│   ├── access.py               # Code lifecycle (generate → provision → email → expire)
│   ├── sync.py                 # Magicline polling, webhook processing, window derivation
│   ├── checks.py               # /checks session resolution + funnel submission
│   ├── checkin.py              # Legacy /check-in flow (backward compat)
│   ├── email_builder.py        # Email template storage & HTML assembly
│   ├── email_templates.py      # Template versioning, sanitization, validation
│   ├── house_rules.py          # House rules CRUD + acknowledgement tracking
│   ├── funnels.py              # Funnel template & step CRUD
│   ├── settings.py             # Runtime config resolution (DB overrides + env)
│   ├── alerts.py               # Operational alerts + Telegram forwarding
│   ├── auth_tokens.py          # JWT helpers (check-in, /checks sessions)
│   ├── formatting.py           # Date formatting, locale helpers
│   ├── members.py              # Member detail aggregation
│   ├── password.py             # Self-service & admin password reset
│   ├── media.py                # File upload + URL resolution
│   └── qr.py                   # QR code generation (SVG + PNG)
└── static/
    ├── index.html              # SPA shell (light/dark theme)
    └── assets/
        ├── admin.css           # Design system (CSS custom properties)
        ├── app.js              # Admin + member UI (vanilla JS SPA)
        ├── favicon.svg
        └── icon-*.svg          # Social media icons for email templates
```

---

## ✨ Features

### Access Engine

- **Magicline Sync** — Polls customers, bookings, and contracts every 30 min. Webhooks provide a fast path for short-notice changes.
- **Entitlement Evaluation** — Members qualify via `XXLARGE` membership or the `Freies Training` add-on product. Configurable rate/product names.
- **Booking Clustering** — Adjacent or overlapping bookings are merged into single access windows to avoid redundant codes.
- **Code Lifecycle** — Generates 6-digit Nuki Keypad codes (digits 1–9 only), provisions them via the Nuki Web API, emails them to the member, and deactivates them after the window ends.
- **Late Booking Handling** — Bookings created less than 15 min before start are processed immediately rather than waiting for the scheduler.
- **Dry-Run Mode** — Full pipeline simulation without touching the real Nuki lock. Ideal for initial setup and testing.

### Member-Facing `/checks`

- **Code-Based Authentication** — Members log in with their email + access code from the notification email.
- **Configurable Funnels** — Admin-defined multi-step check-in and check-out flows with support for confirmations, text notes, images, videos, and house rules acknowledgement.
- **Vertical Stepper UI** — Clean, mobile-first stepper interface with progress tracking.
- **House Rules** — Versioned documents with revision-safe acknowledgement tracking (content hash stored per confirmation).
- **QR Code Access** — Downloadable QR codes (SVG, PNG in multiple sizes) for studio signage linking directly to `/checks`.

### Admin Console

- **Dashboard** — System health at a glance: lock status, sync state, alert count, next scheduled access.
- **Member Management** — Search, detail view with booking history, access windows, and Nuki codes. Expand-in-place UX.
- **Window Actions** — Resend code, create emergency one-time code, early deactivation — all audited.
- **Lock Control** — Real-time Nuki status (state, door sensor, battery), remote open (admin-only, always audited).
- **Alerts & Audit** — Operational alerts with severity levels, full admin action log.
- **Funnel Builder** — Create and edit check-in/check-out funnel templates with drag-and-drop step ordering, step types (confirmation, house rules, video, image, text), and mandatory/optional flags.
- **Branding & Email Editor** — WYSIWYG email template editor with live preview (desktop/mobile), color scheme picker, logo upload, social media links, and placeholder variable insertion.
- **Settings** — Runtime-configurable Magicline, Nuki, SMTP, and Telegram credentials. DB overrides take precedence over `.env` values.
- **User Management** — Create Admin and Operator accounts, reset passwords, toggle active status.

### Notifications

- **Access Code Emails** — Branded HTML emails with the GETIMPULSE design system (table-based, dark mode safe, Outlook VML fallback). Includes code, validity window, and check-in link.
- **Telegram Alerts** — Critical operational events (provisioning failures, emergency codes, Nuki errors) forwarded to a Telegram channel.
- **Password Reset** — Self-service reset via email link (60 min TTL) + admin-driven reset.

### Security

- **PBKDF2-SHA256** password hashing (600k rounds)
- **HMAC-based JWT** tokens (no external auth dependency)
- **Role-based access control** — Admin and Operator roles with endpoint-level enforcement
- **Nuki code uniqueness** — 180-day reuse prevention with hash-based verification
- **Webhook deduplication** — Provider + event ID uniqueness constraint
- **Input validation** — Pydantic models on all endpoints

---

## 📊 Data Model

```
users ─────────────────────────────── password_reset_tokens
  │
members ──┬── member_entitlements
  │       │
  │       ├── bookings
  │       │     │
  │       │     └── access_windows ──┬── access_codes
  │       │           │              │
  │       │           ├── access_window_checkins
  │       │           ├── access_window_checkouts
  │       │           └── funnel_submissions ── funnel_step_events
  │       │
  │       └── house_rules_acknowledgements
  │
funnel_templates ── funnel_steps
house_rules_documents
email_template_versions
system_settings
webhook_events
alerts
admin_actions
```

Key design decisions:
- **Booking ID as access window identity** — `ON CONFLICT (booking_id)` ensures idempotent sync without duplicate windows.
- **Two-tier code verification** — `code_hash` (PBKDF2) for security, `code_last4` for display and fast candidate filtering.
- **System settings overlay** — Runtime DB overrides merge on top of `.env` defaults, enabling configuration changes without redeployment.

---

## 🔌 API Reference

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/login` | Email + password → JWT |
| `GET` | `/me` | Current user info |
| `POST` | `/auth/forgot-password` | Request reset email |
| `POST` | `/auth/reset-password` | Complete reset with token |

### Admin — Members & Windows

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/admin/members` | List members (paginated, searchable) |
| `GET` | `/admin/members/{id}` | Member detail (bookings, windows, codes) |
| `GET` | `/admin/access-windows` | List access windows |
| `POST` | `/admin/access-windows/{id}/resend` | Resend access code |
| `POST` | `/admin/access-windows/{id}/deactivate` | Early deactivation |
| `POST` | `/admin/access-windows/{id}/emergency-code` | One-time emergency code |

### Admin — Lock & Operations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/admin/remote-open` | Remote door open (admin-only) |
| `GET` | `/admin/lock/status` | Nuki lock state + battery |
| `GET` | `/admin/lock/log` | Lock event history |
| `POST` | `/admin/sync` | Trigger Magicline sync |
| `POST` | `/admin/provision` | Trigger code provisioning |

### Admin — System Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET/PUT` | `/admin/system/email-settings` | SMTP configuration |
| `GET/PUT` | `/admin/system/telegram-settings` | Telegram bot config |
| `GET/PUT` | `/admin/system/nuki-settings` | Nuki credentials + dry-run |
| `GET/PUT` | `/admin/system/magicline-settings` | Magicline API config |
| `GET/PUT` | `/admin/system/branding` | Logo, colors, social links |
| `GET/PUT` | `/admin/system/email-template` | Email HTML templates |

### Admin — Funnels

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET/POST` | `/admin/funnels` | List / create funnel templates |
| `GET` | `/admin/funnels/{id}` | Template detail with steps |
| `POST` | `/admin/funnels/{id}/steps` | Add step |
| `PUT/DELETE` | `/admin/funnels/{id}/steps/{sid}` | Update / remove step |

### Public — Member Check-in/out

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/public/checks/resolve` | Authenticate with email + code |
| `GET` | `/public/checks/session` | Resume session with token |
| `GET` | `/public/checks/funnel/{type}` | Get active funnel definition |
| `POST` | `/public/checks/window/{id}/checkin` | Submit check-in |
| `POST` | `/public/checks/window/{id}/checkout` | Submit check-out |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/webhooks/magicline` | Magicline booking events |

### Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/healthz/live` | Liveness probe |
| `GET` | `/healthz/ready` | Readiness probe (DB check) |

---

## ⚙️ Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and fill in your values.

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@db:5432/studio_access` |
| `BOOTSTRAP_ADMIN_EMAIL` | Initial admin email | `admin@studio.de` |
| `BOOTSTRAP_ADMIN_PASSWORD` | Initial admin password | *(min 12 chars)* |
| `JWT_SECRET` | HMAC signing key for JWTs | *(random 64+ chars)* |
| `MAGICLINE_BASE_URL` | Magicline API endpoint | `https://getimpulse.open-api.magicline.com` |
| `MAGICLINE_API_KEY` | Magicline API key | |
| `MAGICLINE_STUDIO_ID` | Magicline studio identifier | `1229488490` |

### Integration Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NUKI_API_TOKEN` | | Nuki Web API bearer token |
| `NUKI_SMARTLOCK_ID` | `0` | Target smart lock ID |
| `NUKI_DRY_RUN` | `true` | Simulate Nuki operations without API calls |
| `SMTP_HOST` | | SMTP server for email dispatch |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USERNAME` | | SMTP authentication |
| `SMTP_PASSWORD` | | SMTP authentication |
| `SMTP_FROM_EMAIL` | | Sender address |
| `TELEGRAM_BOT_TOKEN` | | Telegram alert bot |
| `TELEGRAM_CHAT_ID` | | Target chat/group for alerts |

### Application Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `development` | Runtime environment |
| `APP_TIMEZONE` | `Europe/Berlin` | All access-time calculations |
| `APP_PUBLIC_BASE_URL` | `https://services.frigew.ski/opengym` | Public URL for email links and QR codes |
| `MAGICLINE_RELEVANT_APPOINTMENT_TITLE` | `Freies Training` | Booking title to sync |
| `MAGICLINE_SYNC_INTERVAL_MINUTES` | `30` | Worker sync frequency |
| `LOG_LEVEL` | `INFO` | Structured JSON logging level |

> **Runtime overrides:** Most integration settings can be changed at runtime through the admin UI without restarting containers. DB values take precedence over `.env`.

---

## 🚀 Deployment

### Docker Compose (Recommended)

```bash
# Production deployment
docker compose up -d --build

# View logs
docker compose logs -f web worker

# Database backup
docker compose exec db pg_dump -U studio_access studio_access > backup.sql
```

The stack is designed for **Synology NAS** deployment but runs on any Docker host.

### Manual Development

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# Database (requires running PostgreSQL)
export DATABASE_URL="postgresql://studio_access:dev@localhost:5432/studio_access"

# Run web server
uvicorn nuki_integration.app:app --host 0.0.0.0 --port 8080 --reload

# Run worker (separate terminal)
studio-access-worker
```

### Linting & Type Checking

```bash
ruff check .                          # Lint + import ordering
mypy .                                # Strict static typing
pytest                                # Test suite
pytest --cov=. --cov-report=term-missing  # Coverage report
```

---

## 🔄 Worker Cycle

The worker runs in an infinite loop with the following operations per cycle:

```
┌─ Expire finished windows (DB status update)
├─ Deprovision expired Nuki codes (API DELETE + DB cleanup)
├─ Sync Magicline bookings (poll customers → bookings → contracts)
│   ├─ Upsert members + entitlements
│   ├─ Upsert bookings
│   ├─ Cluster bookings → access windows
│   └─ Update Nuki code validity if window changed
├─ Provision due codes (create keypad code → email member)
└─ Sleep (MAGICLINE_SYNC_INTERVAL_MINUTES)
```

---

## 🔐 Security Model

| Layer | Mechanism |
|-------|-----------|
| **Password storage** | PBKDF2-SHA256, 600k iterations, random 16-byte salt |
| **Session tokens** | HMAC-SHA256 JWT with configurable TTL (1h admin, 24h member) |
| **Access codes** | 6-digit, Nuki Keypad safe (digits 1–9), PBKDF2 hashed, 180-day reuse prevention |
| **Role enforcement** | `Admin` and `Operator` roles checked per endpoint via FastAPI dependency injection |
| **Webhook auth** | API key header validation for Magicline webhooks |
| **Configuration** | Secrets in environment variables only, never in source. `.env` is `.gitignore`d |
| **Audit trail** | Every admin action, lock event, and code operation is persisted with actor, timestamp, and payload |

---

## 📧 Email System

Emails use a **table-based HTML layout** optimized for cross-client rendering:

- Dark mode resistant (forced light scheme via `color-scheme` + `[data-ogsc]` selectors)
- Outlook VML fallback for rounded buttons
- Mobile-responsive (`@media max-width: 620px`)
- Brand-configurable (colors, logo, social links, footer text)
- Live preview in admin UI (desktop + mobile toggle)

Template variables: `{member_name}`, `{code}`, `{valid_from}`, `{valid_until}`, `{checks_url}`, `{checks_row}`, `{reset_url}`

---

## 🧩 Extending

### Adding a New Funnel Step Type

1. Add the type to the `step_type` column options in `models.py` (`FunnelStepCreateRequest`)
2. Handle rendering in `app.js` → `renderActiveStep()`
3. Handle submission validation in `services/checks.py` → `submit_checks_funnel()`

### Adding a New Integration

1. Create a client module (e.g., `shelly_client.py`)
2. Add settings to `config.py` and `services/settings.py`
3. Add DB settings key in `system_settings`
4. Wire into the admin UI settings view

### Adding a New Notification Channel

1. Add a service class in `notifications.py`
2. Add config resolution in `services/settings.py`
3. Wire into `services/alerts.py` for operational notifications
4. Wire into `services/access.py` for member notifications

---

## 📋 Commit Conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add house rules acknowledgement tracking
fix: reject invalid Nuki signature headers
refactor: extract email builder into dedicated service module
test: cover late-booking provisioning edge case
docs: update deployment section for Synology
chore: bump FastAPI to 0.115.x
ci: add PostgreSQL service to test workflow
```

---

## 🗺 Roadmap

- [x] Phase 1 — Access core (Magicline sync, Nuki codes, admin UI)
- [x] Check-in/out funnels with configurable steps
- [x] House rules management with acknowledgement tracking
- [x] Branded email template editor with live preview
- [x] Email template versioning and rollback
- [ ] Phase 2 — Tapo camera orchestration
- [ ] Phase 2 — Aqara presence detection
- [ ] Phase 2 — Shelly relay automation
- [ ] Phase 2 — Alarm scenarios and energy scenes

---

<p align="center">
  <sub>Built for <strong>GETIMPULSE Berlin</strong> · Designed for Synology · Powered by FastAPI + PostgreSQL</sub>
</p>
