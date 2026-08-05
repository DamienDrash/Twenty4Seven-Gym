"""Test bootstrap for the isolated OpenGym snapshot.

The package eagerly constructs ``Settings`` (and the FastAPI ``app``) at import
time, so the required environment must exist before the first import. The
snapshot ships without a ``.env`` and without a live database, so we inject
safe, offline defaults here: ``NUKI_DRY_RUN=true`` (no device I/O) and a
placeholder DSN (never actually connected to by the unit tests, which use
in-memory fakes).
"""
from __future__ import annotations

import os

_DEFAULTS = {
    "APP_ENV": "test",
    "APP_TIMEZONE": "Europe/Berlin",
    "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
    "MAGICLINE_BASE_URL": "https://getimpulse.open-api.magicline.com",
    "MAGICLINE_API_KEY": "test-key",
    "MAGICLINE_STUDIO_ID": "1229488490",
    "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com",
    "BOOTSTRAP_ADMIN_PASSWORD": "test-admin-pw",
    "JWT_SECRET": "test-jwt-secret-do-not-use-in-prod",
    "NUKI_DRY_RUN": "true",
    "NUKI_SMARTLOCK_ID": "22751454439",
    # Guardian webhook shared secret used by the security tests.
    "NUKI_WEBHOOK_SECRET": "test-webhook-secret",
    # Timing values the tests rely on — pinned to the repo defaults so a local
    # production .env (which may override them, e.g. fallback interval 60s)
    # cannot leak into the suite. Env vars beat the .env file in pydantic-settings.
    "NUKI_GUARDIAN_COOLDOWN_SECONDS": "60",
    "NUKI_GUARDIAN_FALLBACK_INTERVAL_SECONDS": "900",
    "NUKI_FREEZE_THRESHOLD_HOURS": "3",
}

# Force-set (NOT setdefault): the suite must be hermetic — on a production
# host the repo .env (read by pydantic-settings via env_file) would otherwise
# shadow these and break timing-sensitive tests. Env vars take precedence
# over the .env file, so forcing them here is sufficient.
for _key, _value in _DEFAULTS.items():
    os.environ[_key] = _value
