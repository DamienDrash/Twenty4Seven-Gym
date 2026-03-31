"""Resolve effective runtime settings by merging DB overrides with env config."""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..db import Database
from ..notifications import SMTPConfig, TelegramConfig
from .formatting import fmt_dt_de, to_berlin  # noqa: F401 — re-export convenience
from .qr import generate_qr_data_uri

DEFAULT_CHECK_IN_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "title": "Check-in vor Trainingsbeginn",
    "intro": (
        "Bitte lies vor jedem Trainingsblock die Hausregeln und bestätige die Checkliste, "
        "damit Schäden und Auffälligkeiten dokumentiert werden können."
    ),
    "rules_heading": "Hausregeln",
    "rules_body": (
        "Trainiere verantwortungsvoll, hinterlasse das Studio ordentlich, melde Defekte oder "
        "Vorbeschädigungen sofort und beachte alle Sicherheits- und Hygieneregeln."
    ),
    "checklist_heading": "Checkliste vor Betreten",
    "checklist_items": [
        {
            "id": "no_visible_damage",
            "label": (
                "Ich habe keine sichtbaren Vorbeschädigungen oder "
                "Auffälligkeiten festgestellt."
            ),
        },
        {
            "id": "rules_read",
            "label": "Ich habe die Hausregeln gelesen und werde sie während der Nutzung einhalten.",
        },
        {
            "id": "reporting_commitment",
            "label": (
                "Ich melde Schäden, Störungen oder sicherheitsrelevante "
                "Vorfälle unverzüglich."
            ),
        },
    ],
    "success_message": (
        "Check-in erfolgreich bestätigt. Du kannst dein Training jetzt im gebuchten Zeitfenster "
        "nutzen."
    ),
}


def get_effective_smtp_config(db: Database, settings: Settings) -> SMTPConfig:
    raw = db.get_system_setting("smtp") or {}
    return SMTPConfig(
        host=str(raw.get("smtp_host") or settings.smtp_host),
        port=int(raw.get("smtp_port") or settings.smtp_port),
        username=str(raw.get("smtp_username") or settings.smtp_username),
        password=str(raw.get("smtp_password") or settings.smtp_password),
        use_tls=bool(raw.get("smtp_use_tls") if "smtp_use_tls" in raw else settings.smtp_use_tls),
        from_email=str(raw.get("smtp_from_email") or settings.smtp_from_email),
    )


def get_effective_telegram_config(db: Database, settings: Settings) -> TelegramConfig:
    raw = db.get_system_setting("telegram") or {}
    return TelegramConfig(
        bot_token=str(raw.get("telegram_bot_token") or settings.telegram_bot_token),
        chat_id=str(raw.get("telegram_chat_id") or settings.telegram_chat_id),
    )


def get_effective_nuki_config(db: Database, settings: Settings) -> dict[str, object]:
    raw = db.get_system_setting("nuki") or {}
    return {
        "nuki_api_token": str(raw.get("nuki_api_token") or settings.nuki_api_token),
        "nuki_smartlock_id": int(raw.get("nuki_smartlock_id") or settings.nuki_smartlock_id),
        "nuki_dry_run": bool(
            raw["nuki_dry_run"] if "nuki_dry_run" in raw else settings.nuki_dry_run
        ),
    }


def get_effective_magicline_config(db: Database, settings: Settings) -> dict[str, object]:
    raw = db.get_system_setting("magicline") or {}
    return {
        "magicline_base_url": str(
            raw.get("magicline_base_url") or settings.magicline_base_url
        ),
        "magicline_api_key": str(
            raw.get("magicline_api_key") or settings.magicline_api_key
        ),
        "magicline_webhook_api_key": str(
            raw.get("magicline_webhook_api_key") or settings.magicline_webhook_api_key
        ),
        "magicline_studio_id": int(
            raw.get("magicline_studio_id") or settings.magicline_studio_id
        ),
        "magicline_studio_name": str(
            raw.get("magicline_studio_name") or settings.magicline_studio_name
        ),
        "magicline_relevant_appointment_title": str(
            raw.get("magicline_relevant_appointment_title")
            or settings.magicline_relevant_appointment_title
        ),
    }


def get_effective_check_in_settings(db: Database, settings: Settings) -> dict[str, object]:
    raw = db.get_system_setting("check_in") or {}
    merged: dict[str, Any] = {**DEFAULT_CHECK_IN_SETTINGS, **raw}
    merged["checklist_items"] = [
        {"id": str(item["id"]), "label": str(item["label"])}
        for item in merged.get("checklist_items", [])
        if isinstance(item, dict) and item.get("id") and item.get("label")
    ]
    merged["studio_check_in_url"] = f"{settings.app_public_base_url.rstrip('/')}/check-in"
    merged["studio_qr_svg"] = generate_qr_data_uri(str(merged["studio_check_in_url"]))
    return merged


def get_branding_settings(db: Database) -> dict[str, str | None]:
    raw = db.get_system_setting("branding") or {}
    return {
        "logo_url": raw.get("logo_url"),
        "logo_link_url": raw.get("logo_link_url"),
        "instagram_url": raw.get("instagram_url"),
        "facebook_url": raw.get("facebook_url"),
        "tiktok_url": raw.get("tiktok_url"),
        "youtube_url": raw.get("youtube_url"),
        "header_bg_color": raw.get("header_bg_color", "#ffffff"),
        "body_bg_color": raw.get("body_bg_color", "#f9f9f9"),
        "footer_bg_color": raw.get("footer_bg_color", "#ffffff"),
        "footer_text": raw.get("footer_text"),
        "accent_color": raw.get("accent_color", "#b5ac9e"),
    }
