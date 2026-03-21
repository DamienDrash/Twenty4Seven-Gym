from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class AccessWindowStatus(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELED = "canceled"
    FLAGGED = "flagged"


class AccessCodeStatus(StrEnum):
    PENDING = "pending"
    PROVISIONED = "provisioned"
    EMAILED = "emailed"
    REPLACED = "replaced"
    FAILED = "failed"
    EXPIRED = "expired"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MagiclineBookingStatus(StrEnum):
    BOOKED = "BOOKED"
    CANCELED = "CANCELED"
