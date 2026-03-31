"""Application configuration.

All secrets are loaded from environment variables.  Parsed compound
values use ``@cached_property`` so they are computed exactly once per
``Settings`` instance rather than re-parsed on every access.
"""

from __future__ import annotations

from functools import cached_property, lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .enums import WebhookFeature, WebhookMode
from .exceptions import ConfigurationError


class Settings(BaseSettings):
    """Strongly-typed runtime settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # -- runtime --
    app_env: str = Field(default="production", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8080, alias="PORT")

    # -- Nuki API --
    nuki_base_url: str = Field(default="https://api.nuki.io", alias="NUKI_BASE_URL")
    nuki_timeout_seconds: int = Field(default=15, alias="NUKI_TIMEOUT_SECONDS")

    # OAuth2
    nuki_client_id: str = Field(default="", alias="NUKI_CLIENT_ID")
    nuki_client_secret: str = Field(default="", alias="NUKI_CLIENT_SECRET")
    nuki_access_token: str = Field(default="", alias="NUKI_ACCESS_TOKEN")
    nuki_refresh_token: str = Field(default="", alias="NUKI_REFRESH_TOKEN")

    # Legacy fallback
    nuki_api_token: str = Field(default="", alias="NUKI_API_TOKEN")

    # -- webhook mode --
    nuki_webhook_mode: WebhookMode = Field(
        default=WebhookMode.DECENTRAL, alias="NUKI_WEBHOOK_MODE",
    )
    nuki_decentral_webhook_secret: str = Field(
        default="", alias="NUKI_DECENTRAL_WEBHOOK_SECRET",
    )
    nuki_webhook_features_raw: str = Field(
        default="DEVICE_STATUS,DEVICE_LOGS", alias="NUKI_WEBHOOK_FEATURES",
    )
    public_webhook_url: str = Field(default="", alias="PUBLIC_WEBHOOK_URL")

    # -- inbound security --
    inbound_shared_secret: str = Field(default="", alias="INBOUND_SHARED_SECRET")
    allowed_smartlock_ids_raw: str = Field(default="", alias="ALLOWED_SMARTLOCK_IDS")
    max_event_age_seconds: int = Field(default=300, alias="MAX_EVENT_AGE_SECONDS")

    # -- persistence --
    sqlite_path: str = Field(default="./data/nuki_events.db", alias="SQLITE_PATH")

    # -- operations --
    enable_healthcheck: bool = Field(default=True, alias="ENABLE_HEALTHCHECK")

    # ------------------------------------------------------------------
    # Computed properties — cached so parsing happens exactly once.
    # ------------------------------------------------------------------

    @cached_property
    def allowed_smartlock_ids(self) -> frozenset[int]:
        """Immutable set of accepted smart lock IDs."""
        raw = self.allowed_smartlock_ids_raw.strip()
        if not raw:
            return frozenset()
        ids: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return frozenset(ids)

    @cached_property
    def nuki_webhook_features(self) -> tuple[WebhookFeature, ...]:
        """Parsed webhook features as an immutable tuple."""
        features: list[WebhookFeature] = []
        for part in self.nuki_webhook_features_raw.split(","):
            normalized = part.strip()
            if normalized:
                features.append(WebhookFeature(normalized))
        return tuple(features)

    @property
    def active_bearer_token(self) -> str:
        """Return the best available bearer token.

        Prefers the OAuth2 access token; falls back to the legacy API
        token when no OAuth2 token is configured.
        """
        return self.nuki_access_token or self.nuki_api_token

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("nuki_base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ConfigurationError("NUKI_BASE_URL must use HTTPS.")
        return value.rstrip("/")

    @field_validator("public_webhook_url")
    @classmethod
    def _validate_webhook_url(cls, value: str) -> str:
        if value and not value.startswith("https://"):
            raise ConfigurationError("PUBLIC_WEBHOOK_URL must use HTTPS.")
        return value.rstrip("/")

    @field_validator("max_event_age_seconds")
    @classmethod
    def _validate_max_age(cls, value: int) -> int:
        if value <= 0:
            raise ConfigurationError("MAX_EVENT_AGE_SECONDS must be > 0.")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton settings instance."""
    return Settings()
