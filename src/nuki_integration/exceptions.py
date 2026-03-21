"""Application exception hierarchy."""

from __future__ import annotations


class AppError(Exception):
    """Base application error."""


class ConfigurationError(AppError):
    """Runtime configuration is invalid or incomplete."""


class AuthenticationError(AppError):
    """Authentication failed."""


class AuthorizationError(AppError):
    """Caller is not authorized."""


class MagiclineApiError(AppError):
    """Magicline API returned an error."""


class NukiApiError(AppError):
    """Nuki API returned an error."""
