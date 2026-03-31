"""Pydantic models for Nuki API payloads and local persistence.

The webhook envelope is intentionally permissive (``extra="allow"``)
because Nuki delivers different payload shapes per feature and does
not guarantee stable schemas across releases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------
# Decentral webhook management
# ------------------------------------------------------------------

class DecentralWebhookRegistration(BaseModel):
    """Response from PUT /api/decentralWebhook."""

    id: int
    secret: str
    webhookUrl: str
    webhookFeatures: list[str]


class DecentralWebhookRecord(BaseModel):
    """An existing decentral webhook from GET /api/decentralWebhook."""

    id: int
    webhookUrl: str
    webhookFeatures: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------
# Inbound webhook payloads
# ------------------------------------------------------------------

class DeviceStatusState(BaseModel):
    """Nested ``state`` object in DEVICE_STATUS events."""

    model_config = ConfigDict(extra="allow")

    mode: int | None = None
    state: int | None = None
    trigger: int | None = None
    lastAction: int | None = None
    batteryCritical: bool | None = None
    batteryCharging: bool | None = None
    batteryCharge: int | None = None
    keypadBatteryCritical: bool | None = None
    doorsensorBatteryCritical: bool | None = None
    doorState: int | None = None
    ringToOpenTimer: int | None = None
    nightMode: bool | None = None


class NukiWebhookEvent(BaseModel):
    """Generic Nuki webhook envelope.

    Fields common to all feature types are modelled explicitly.
    Everything else is captured via ``extra="allow"`` so unknown
    fields never cause 422 rejections.
    """

    model_config = ConfigDict(extra="allow")

    feature: str
    smartlockId: int | None = None
    accountId: int | None = None
    nukiId: int | None = None
    state: DeviceStatusState | dict[str, Any] | None = None
    serverState: int | None = None
    adminPinState: int | None = None
    timestamp: datetime | None = None

    @property
    def semantic_key(self) -> str:
        """Build a fachlicher (business-level) idempotency key.

        Uses ``(smartlockId, feature, timestamp)`` to identify
        logically identical events even when the raw byte payload
        differs (e.g. due to Nuki adding new fields, different JSON
        key ordering, or whitespace changes).

        Falls back to ``"unknown"`` components when a field is absent
        so the key is always a valid, non-empty string.
        """
        lock = str(self.smartlockId) if self.smartlockId is not None else "unknown"
        ts = self.timestamp.isoformat() if self.timestamp else "unknown"
        return f"{lock}:{self.feature}:{ts}"


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------

class StoredEvent(BaseModel):
    """Row model for the ``webhook_events`` table."""

    idempotency_key: str
    raw_hash: str
    received_at: datetime
    status: str = "received"
    feature: str | None = None
    smartlock_id: int | None = None
    event_timestamp: datetime | None = None
    error_detail: str | None = None
