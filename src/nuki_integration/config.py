from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8080, alias="PORT")
    timezone: str = Field(default="Europe/Berlin", alias="APP_TIMEZONE")
    database_url: str = Field(alias="DATABASE_URL")

    magicline_base_url: str = Field(alias="MAGICLINE_BASE_URL")
    magicline_api_key: str = Field(alias="MAGICLINE_API_KEY")
    magicline_webhook_api_key: str = Field(default="", alias="MAGICLINE_WEBHOOK_API_KEY")
    magicline_studio_id: int = Field(alias="MAGICLINE_STUDIO_ID")
    magicline_studio_name: str = Field(default="", alias="MAGICLINE_STUDIO_NAME")
    magicline_sync_interval_minutes: int = Field(default=30, alias="MAGICLINE_SYNC_INTERVAL_MINUTES")
    magicline_relevant_appointment_title: str = Field(default="Freies Training", alias="MAGICLINE_RELEVANT_APPOINTMENT_TITLE")
    magicline_entitlement_rate_name: str = Field(default="XXLARGE", alias="MAGICLINE_ENTITLEMENT_RATE_NAME")
    magicline_entitlement_product_name: str = Field(default="Freies Training", alias="MAGICLINE_ENTITLEMENT_PRODUCT_NAME")

    nuki_base_url: str = Field(default="https://api.nuki.io", alias="NUKI_BASE_URL")
    nuki_timeout_seconds: int = Field(default=15, alias="NUKI_TIMEOUT_SECONDS")
    nuki_client_id: str = Field(default="", alias="NUKI_CLIENT_ID")
    nuki_client_secret: str = Field(default="", alias="NUKI_CLIENT_SECRET")
    nuki_access_token: str = Field(default="", alias="NUKI_ACCESS_TOKEN")
    nuki_refresh_token: str = Field(default="", alias="NUKI_REFRESH_TOKEN")
    nuki_api_token: str = Field(default="", alias="NUKI_API_TOKEN")
    nuki_smartlock_id: int = Field(default=0, alias="NUKI_SMARTLOCK_ID")
    nuki_dry_run: bool = Field(default=True, alias="NUKI_DRY_RUN")

    bootstrap_admin_email: str = Field(alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: str = Field(alias="BOOTSTRAP_ADMIN_PASSWORD")
    jwt_secret: str = Field(alias="JWT_SECRET")

    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")
    smtp_from_email: str = Field(default="", alias="SMTP_FROM_EMAIL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    app_public_base_url: str = Field(default="https://services.frigew.ski/opengym", alias="APP_PUBLIC_BASE_URL")
    media_storage_path: str = Field(default="./media/uploads", alias="MEDIA_STORAGE_PATH")
    media_url_base: str = Field(default="/media", alias="MEDIA_URL_BASE")

    @property
    def active_nuki_token(self) -> str:
        return self.nuki_access_token or self.nuki_api_token

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgres://")):
            raise ConfigurationError("DATABASE_URL must be a PostgreSQL DSN.")
        return value

    @field_validator("magicline_base_url", "nuki_base_url")
    @classmethod
    def _validate_https_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ConfigurationError("External API URLs must use HTTPS.")
        return value.rstrip("/")

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        if value != "Europe/Berlin":
            raise ConfigurationError("Phase 1 requires APP_TIMEZONE=Europe/Berlin.")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
