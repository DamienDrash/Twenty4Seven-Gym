# Twenty4Seven-Gym

Booking-driven 24/7 access platform for fitness studios.

**Stack:** FastAPI · PostgreSQL · Magicline · Nuki Pro · Docker Compose

## Quick Start

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up -d --build
```

Admin: `http://127.0.0.1:8080/app`
Member check-in: `http://127.0.0.1:8080/checks`

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
├── services/               # Business logic (12 modules)
│   ├── access.py           # Code lifecycle
│   ├── sync.py             # Magicline sync + webhooks
│   ├── checks.py           # Member /checks funnel
│   ├── email_builder.py    # Email template assembly
│   ├── settings.py         # Runtime config resolution
│   └── ...
└── static/                 # Frontend
    ├── index.html
    └── assets/
        ├── admin.css       # Warm Minimal design system
        └── app.js          # Admin + member UI
```
