"""Email template versioning and validation.

Extends the existing email_builder with:
- Template type registry
- Body HTML sanitization (strip scripts/iframes)
- Required placeholder validation per template type
- Version history with rollback
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from ..db import Database

logger = logging.getLogger(__name__)

# ── Template Type Registry ────────────────────────────────────────

TEMPLATE_TYPES = {
    "access_code": {
        "label": "Zugangscode-Email",
        "required_placeholders": ["{member_name}", "{code}"],
        "optional_placeholders": [
            "{valid_from}", "{valid_until}",
            "{checks_url}", "{checks_row}", "{check_in_url}",
        ],
    },
    "reset": {
        "label": "Passwort-Reset-Email",
        "required_placeholders": ["{reset_url}"],
        "optional_placeholders": [],
    },
    "checkin_confirm": {
        "label": "Check-in Bestätigung",
        "required_placeholders": ["{member_name}"],
        "optional_placeholders": ["{valid_from}", "{valid_until}"],
    },
    "checkout_confirm": {
        "label": "Check-out Bestätigung",
        "required_placeholders": ["{member_name}"],
        "optional_placeholders": [],
    },
    "test": {
        "label": "Test-Email",
        "required_placeholders": [],
        "optional_placeholders": [],
    },
}

# ── Sanitization ──────────────────────────────────────────────────

# Tags that are always stripped (with content)
_DANGEROUS_TAGS = re.compile(
    r"<\s*(script|iframe|object|embed|applet|form|input|button)"
    r"[^>]*>.*?</\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Self-closing dangerous tags
_DANGEROUS_SELF = re.compile(
    r"<\s*(script|iframe|object|embed|applet|input)\b[^>]*/?\s*>",
    re.IGNORECASE,
)

# Event handler attributes
_EVENT_ATTRS = re.compile(
    r'\s+on\w+\s*=\s*["\'][^"\']*["\']',
    re.IGNORECASE,
)

# javascript: URLs
_JS_URLS = re.compile(
    r'(href|src|action)\s*=\s*["\']?\s*javascript:',
    re.IGNORECASE,
)

MAX_BODY_LENGTH = 50_000


def sanitize_template_body(body_html: str) -> str:
    """Strip dangerous HTML constructs from template body.

    Removes script/iframe tags, event handlers, and javascript: URLs
    while preserving email-safe HTML (tables, styles, images, links).
    """
    if len(body_html) > MAX_BODY_LENGTH:
        raise ValueError(
            f"Template-Body überschreitet die maximale Länge von "
            f"{MAX_BODY_LENGTH} Zeichen."
        )

    result = body_html
    result = _DANGEROUS_TAGS.sub("", result)
    result = _DANGEROUS_SELF.sub("", result)
    result = _EVENT_ATTRS.sub("", result)
    result = _JS_URLS.sub(r'\1=""', result)

    return result


def validate_required_placeholders(
    body_html: str,
    template_type: str,
) -> list[str]:
    """Check that all required placeholders for a template type are present.

    Returns list of missing placeholder names (empty = all good).
    """
    type_config = TEMPLATE_TYPES.get(template_type)
    if not type_config:
        return []

    missing = []
    for placeholder in type_config["required_placeholders"]:
        if placeholder not in body_html:
            missing.append(placeholder)
    return missing


# ── Version Management ────────────────────────────────────────────

def save_template_version(
    db: Database,
    *,
    template_type: str,
    body_html: str,
    changed_by: str,
    change_note: str | None = None,
) -> dict[str, Any]:
    """Sanitize, validate, and save a new template version.

    Raises ValueError if required placeholders are missing.
    """
    # Sanitize
    clean_body = sanitize_template_body(body_html)

    # Validate placeholders
    missing = validate_required_placeholders(clean_body, template_type)
    if missing:
        raise ValueError(
            f"Pflicht-Platzhalter fehlen im Template: "
            f"{', '.join(missing)}"
        )

    with db.connection() as conn:
        with conn.cursor() as cur:
            # Get next version number for this type
            cur.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_v
                FROM email_template_versions
                WHERE template_type = %s
                """,
                (template_type,),
            )
            next_version = int(cur.fetchone()["next_v"])

            # Deactivate previous versions of this type
            cur.execute(
                """
                UPDATE email_template_versions
                SET is_active = FALSE
                WHERE template_type = %s
                """,
                (template_type,),
            )

            # Insert new version
            cur.execute(
                """
                INSERT INTO email_template_versions
                    (template_type, version, body_html, changed_by, change_note)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, template_type, version, changed_by,
                          change_note, created_at
                """,
                (template_type, next_version, clean_body,
                 changed_by, change_note),
            )
            result = cur.fetchone()
        conn.commit()

    logger.info(
        "Template version saved: type=%s v=%d by=%s",
        template_type, next_version, changed_by,
    )

    # Also update the system_settings for backward compatibility
    _sync_to_system_settings(db, template_type, clean_body)

    return result


def _sync_to_system_settings(
    db: Database,
    template_type: str,
    body_html: str,
) -> None:
    """Keep the system_settings 'email_template' in sync.

    The existing email_builder.py reads from system_settings,
    so we update it when a version is saved.
    """
    from .email_builder import get_email_template

    current = get_email_template(db)
    field_map = {
        "access_code": "access_code_body_html",
        "reset": "reset_body_html",
        "test": "body_html",
    }
    field = field_map.get(template_type)
    if field:
        current[field] = body_html
        db.set_system_setting(key="email_template", value=current)


def list_template_versions(
    db: Database,
    *,
    template_type: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List version history for a template type."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, template_type, version, is_active,
                   changed_by, change_note, created_at
            FROM email_template_versions
            WHERE template_type = %s
            ORDER BY version DESC LIMIT %s
            """,
            (template_type, limit),
        )
        return list(cur.fetchall())


def restore_template_version(
    db: Database,
    *,
    template_type: str,
    version: int,
    restored_by: str,
) -> dict[str, Any]:
    """Restore a previous version by creating a new version with its content."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT body_html FROM email_template_versions
            WHERE template_type = %s AND version = %s
            """,
            (template_type, version),
        )
        old = cur.fetchone()
    if not old:
        raise ValueError(
            f"Version {version} für Typ '{template_type}' nicht gefunden."
        )

    return save_template_version(
        db,
        template_type=template_type,
        body_html=old["body_html"],
        changed_by=restored_by,
        change_note=f"Wiederhergestellt von Version {version}",
    )
