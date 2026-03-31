"""Exception hierarchy for the Nuki integration.

Each exception type maps to a specific failure domain so callers can
handle errors precisely without catching overly broad types.
"""

from __future__ import annotations


class NukiIntegrationError(Exception):
    """Base for all integration errors."""


class NukiApiError(NukiIntegrationError):
    """The upstream Nuki Web API returned an unexpected response."""


class ConfigurationError(NukiIntegrationError):
    """Runtime configuration is invalid or incomplete."""


class WebhookVerificationError(NukiIntegrationError):
    """HMAC signature or shared-secret verification failed."""


class ReplayAttackError(NukiIntegrationError):
    """Event timestamp falls outside the permitted skew window."""


class AuthorizationError(NukiIntegrationError):
    """A smart lock or event is not authorized for local processing."""


class TokenRefreshError(NukiIntegrationError):
    """OAuth2 token refresh failed and requires operator intervention."""
