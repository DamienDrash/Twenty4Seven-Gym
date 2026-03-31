"""Enumerations for Nuki webhook processing.

Numeric state and action values come from the official Nuki Web API
documentation.  Keeping them as enums prevents magic numbers from
leaking into business logic.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


# ------------------------------------------------------------------
# Webhook architecture
# ------------------------------------------------------------------

class WebhookMode(StrEnum):
    """Nuki webhook verification modes."""

    CENTRAL = "central"
    DECENTRAL = "decentral"


class WebhookFeature(StrEnum):
    """Subscribable webhook features (Nuki Web API v1.5.3)."""

    DEVICE_STATUS = "DEVICE_STATUS"
    DEVICE_MASTERDATA = "DEVICE_MASTERDATA"
    DEVICE_CONFIG = "DEVICE_CONFIG"
    DEVICE_LOGS = "DEVICE_LOGS"
    DEVICE_AUTHS = "DEVICE_AUTHS"
    ACCOUNT_USER = "ACCOUNT_USER"


# ------------------------------------------------------------------
# Event processing status
# ------------------------------------------------------------------

class EventStatus(StrEnum):
    """Lifecycle state of a persisted webhook event.

    The distinction between RECEIVED and PROCESSED is critical:
    an event that is RECEIVED but never transitions to PROCESSED
    indicates a failed business-logic execution and must be
    retried or investigated.
    """

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


# ------------------------------------------------------------------
# Smart lock states (Nuki Web API)
# ------------------------------------------------------------------

class SmartLockState(IntEnum):
    UNCALIBRATED = 0
    LOCKED = 1
    UNLOCKING = 2
    UNLOCKED = 3
    LOCKING = 4
    UNLATCHED = 5
    UNCALIBRATED_LOCKED = 6
    BOOT_RUN = 7
    MOTOR_BLOCKED = 254
    UNDEFINED = 255


class LastAction(IntEnum):
    UNLOCK = 1
    LOCK = 2
    UNLATCH = 3
    LOCK_N_GO = 4
    LOCK_N_GO_WITH_UNLATCH = 5
    FULL_LOCK = 6
    FOB_ACTION_1 = 81
    FOB_ACTION_2 = 82
    FOB_ACTION_3 = 83


class Trigger(IntEnum):
    SYSTEM = 0
    MANUAL = 1
    BUTTON = 2
    AUTOMATIC = 3
    AUTO_LOCK = 4
    KEYPAD = 5
    REMOTE = 6
    AUTHORIZATION = 7


class DoorState(IntEnum):
    UNAVAILABLE = 0
    DEACTIVATED = 1
    CLOSED = 2
    OPENED = 3
    UNKNOWN = 4
    CALIBRATING = 5
