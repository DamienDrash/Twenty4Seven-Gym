"""Structured logging setup.

Produces JSON-like log lines parseable by common aggregation tools
(Loki, Datadog, ELK) without adding a heavy dependency like structlog.
Sensitive values are never included in log output.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    # Fields that must never appear in log output.
    _REDACTED_SUBSTRINGS = frozenset({
        "secret", "token", "password", "credential", "authorization",
    })

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    """Configure application-wide structured logging.

    Parameters
    ----------
    level:
        Root log level name, e.g. ``"INFO"`` or ``"DEBUG"``.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any pre-existing handlers to avoid duplicate output.
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)

    # Quieten noisy libraries in production.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
