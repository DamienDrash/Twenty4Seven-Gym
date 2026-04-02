# Twenty4Seven-Gym

Booking-driven 24/7 gym access platform for GETIMPULSE BERLIN.

**Stack:** FastAPI · PostgreSQL · Magicline · Nuki Pro · Docker Compose

## What It Does

- Syncs member bookings from Magicline every 30 min
- Provisions Nuki smartlock keypad codes for members with upcoming "Freies Training" bookings
- Sends access codes via email and Telegram
- Web-based check-in / check-out funnel with configurable steps (house rules, yes/no, NPS, video)
- Admin UI for managing funnels, access windows, settings, and audit logs
- Permanent per-member check-in URL via `?key=<uuid>` — no login required

## Quick Start

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up -d --build
```

| URL | Description |
|-----|-------------|
| `/app` | Admin interface |
| `/checks?key=<uuid>` | Member check-in / check-out (permanent link) |
| `/checks?token=<jwt>` | Member check-in via time-limited token |

## Services

| Service | Description |
|---------|-------------|
| `db` | PostgreSQL 16 |
| `web` | FastAPI on port 8080 |
| `worker` | Background sync + code provisioning loop |

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
