from __future__ import annotations

import logging
import secrets
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import qrcode
import qrcode.image.svg

from .auth import decode_token, issue_token
from .config import Settings
from .db import Database
from .enums import AccessCodeStatus, AccessWindowStatus, AlertSeverity
from .magicline import (
    MagiclineClient,
    MagiclineCustomer,
    booking_effective_received_at,
    derive_entitlements,
    is_access_booking,
)
from .models import FunnelStepCreateRequest, FunnelTemplateCreateRequest
from .notifications import EmailService, SMTPConfig, TelegramConfig, TelegramService
from .nuki_client import NukiClient

if TYPE_CHECKING:
    from fastapi import UploadFile

logger = logging.getLogger(__name__)


DEFAULT_CHECK_IN_SETTINGS = {
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


def get_effective_check_in_settings(db: Database, settings: Settings) -> dict[str, object]:
    raw = db.get_system_setting("check_in") or {}
    merged = {
        **DEFAULT_CHECK_IN_SETTINGS,
        **raw,
    }
    merged["checklist_items"] = [
        {"id": str(item["id"]), "label": str(item["label"])}
        for item in merged.get("checklist_items", [])
        if isinstance(item, dict) and item.get("id") and item.get("label")
    ]
    merged["studio_check_in_url"] = f"{settings.app_public_base_url.rstrip('/')}/check-in"
    merged["studio_qr_svg"] = generate_qr_data_uri(str(merged["studio_check_in_url"]))
    return merged


def get_media_url(settings: Settings, filename: str) -> str:
    base = settings.media_url_base.rstrip("/")
    return f"{base}/{filename.lstrip('/')}"


def save_media_file(settings: Settings, upload: UploadFile) -> str:
    dest_dir = Path(settings.media_storage_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename).suffix
    filename = f"{secrets.token_hex(12)}{suffix}"
    dest_path = dest_dir / filename
    with dest_path.open("wb") as buffer:
        buffer.write(upload.file.read())
    return filename


def list_funnel_templates(*, db: Database) -> list[dict[str, object]]:
    return db.list_funnel_templates()


def get_funnel_template(*, db: Database, template_id: int) -> dict[str, object] | None:
    return db.get_funnel_template_detail(template_id=template_id)


def upsert_funnel_template_service(
    *,
    db: Database,
    payload: FunnelTemplateCreateRequest,
    template_id: int | None = None,
) -> dict[str, object]:
    return create_or_update_template(
        db=db,
        template_id=template_id,
        name=payload.name,
        slug=payload.slug,
        funnel_type=payload.funnel_type,
        description=payload.description,
    )


def upsert_funnel_step_service(
    *,
    db: Database,
    payload: FunnelStepCreateRequest,
    step_id: int | None = None,
) -> dict[str, object]:
    return create_or_update_step(
        db=db,
        template_id=payload.template_id,
        step_order=payload.step_order,
        title=payload.title,
        body=payload.body,
        image_path=payload.image_path,
        requires_note=payload.requires_note,
        requires_photo=payload.requires_photo,
        step_id=step_id,
    )


def media_url_response(*, settings: Settings, filename: str) -> str:
    path = Path(filename)
    if path.is_absolute():
        return get_media_url(settings, filename)
    return get_media_url(settings, filename)


def create_or_update_template(
    *,
    db: Database,
    name: str,
    slug: str,
    funnel_type: str,
    description: str | None,
    template_id: int | None = None,
) -> dict[str, object]:
    return db.upsert_funnel_template(
        template_id=template_id,
        name=name,
        slug=slug,
        funnel_type=funnel_type,
        description=description,
    )


def create_or_update_step(
    *,
    db: Database,
    template_id: int,
    step_order: int,
    title: str,
    body: str | None,
    image_path: str | None,
    requires_note: bool,
    requires_photo: bool,
    step_id: int | None = None,
) -> dict[str, object]:
    return db.upsert_funnel_step(
        step_id=step_id,
        template_id=template_id,
        step_order=step_order,
        title=title,
        body=body,
        image_path=image_path,
        requires_note=requires_note,
        requires_photo=requires_photo,
    )


def delete_funnel_step(*, db: Database, step_id: int) -> None:
    db.delete_funnel_step(step_id=step_id)


def generate_qr_data_uri(url: str) -> str:
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgImage)
    svg_bytes = image.to_string()
    return f"data:image/svg+xml;base64,{b64encode(svg_bytes).decode('ascii')}"


def generate_qr_png_bytes(url: str, box_size: int = 10) -> bytes:
    import io
    from PIL import Image  # type: ignore[import-untyped]
    img: Image.Image = qrcode.make(url, box_size=box_size, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_effective_nuki_config(db: Database, settings: Settings) -> dict[str, object]:
    raw = db.get_system_setting("nuki") or {}
    return {
        "nuki_api_token": str(raw.get("nuki_api_token") or settings.nuki_api_token),
        "nuki_smartlock_id": int(raw.get("nuki_smartlock_id") or settings.nuki_smartlock_id),
        "nuki_dry_run": bool(raw["nuki_dry_run"] if "nuki_dry_run" in raw else settings.nuki_dry_run),
    }


def get_effective_magicline_config(db: Database, settings: Settings) -> dict[str, object]:
    raw = db.get_system_setting("magicline") or {}
    return {
        "magicline_base_url": str(raw.get("magicline_base_url") or settings.magicline_base_url),
        "magicline_api_key": str(raw.get("magicline_api_key") or settings.magicline_api_key),
        "magicline_webhook_api_key": str(raw.get("magicline_webhook_api_key") or settings.magicline_webhook_api_key),
        "magicline_studio_id": int(raw.get("magicline_studio_id") or settings.magicline_studio_id),
        "magicline_studio_name": str(raw.get("magicline_studio_name") or settings.magicline_studio_name),
        "magicline_relevant_appointment_title": str(
            raw.get("magicline_relevant_appointment_title") or settings.magicline_relevant_appointment_title
        ),
    }


# ── Email Template ─────────────────────────────────────────────────────────────

_DEFAULT_EMAIL_HEADER = """<style type="text/css">
  body, table, td, p, a, li { -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }
  table, td { mso-table-lspace:0pt; mso-table-rspace:0pt; border-collapse:collapse; }
  img { -ms-interpolation-mode:bicubic; border:0; display:block; outline:none; }
  * { box-sizing:border-box; }
  :root { color-scheme:light only; supported-color-schemes:light; }
  body { margin:0!important; padding:0!important; background-color:#f0ede9!important; color:#000000!important; width:100%!important; }
  [data-ogsc] .fbb { background-color:#f0ede9!important; }
  [data-ogsc] .fbw { background-color:#ffffff!important; }
  [data-ogsc] .fbk { background-color:#000000!important; }
  [data-ogsc] .fcw { color:#ffffff!important; }
  [data-ogsc] .fcb { color:#000000!important; }
  [data-ogsc] .fcd { color:#3a3a3a!important; }
  [data-ogsc] .fcm { color:#7a7a7a!important; }
  [data-ogsc] .fbd { border-color:#e4e0db!important; }
  [data-ogsc] .fbtn { background-color:#b5ac9e!important; }
  @media (prefers-color-scheme:dark) {
    body { background-color:#f0ede9!important; }
    .fbb { background-color:#f0ede9!important; }
    .fbw { background-color:#ffffff!important; }
    .fbk { background-color:#000000!important; }
    .fcw { color:#ffffff!important; }
    .fcb { color:#000000!important; }
    .fcd { color:#3a3a3a!important; }
    .fcm { color:#7a7a7a!important; }
    .fbd { border-color:#e4e0db!important; }
    .fbtn { background-color:#b5ac9e!important; }
  }
  @media only screen and (max-width:620px) {
    .wrapper { width:100%!important; max-width:100%!important; }
    .h1 { font-size:27px!important; line-height:1.25!important; }
    .ph { padding-left:24px!important; padding-right:24px!important; }
    .p-hero { padding:36px 24px 28px!important; }
    .p-sec { padding:28px 24px!important; }
    .p-cta { padding:4px 24px 40px!important; }
  }
</style>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f0ede9;" class="fbb">
<tr><td align="center" style="padding:28px 16px;">
<table role="presentation" class="wrapper" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">
<tr>
  <td class="fbk" style="background-color:#000000;padding:22px 40px;text-align:center;">
    <a href="https://getimpulse.de/" style="font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:700;letter-spacing:4px;color:#ffffff;text-decoration:none;text-transform:uppercase;" class="fcw">GETIMPULSE</a>
  </td>
</tr>"""

_DEFAULT_EMAIL_BODY = """<tr>
  <td class="fbw p-hero" style="background-color:#ffffff;padding:52px 56px 36px;text-align:center;">
    <h1 class="h1 fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:36px;font-weight:700;color:#000000;margin:0 0 22px 0;line-height:1.2;">Dein Betreff kommt hier rein</h1>
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#3a3a3a;margin:0;line-height:1.7;">Hallo {{ contact.first_name }},<br><br>Hier kommt dein einleitender Text. Gib deinen Lesern direkt einen klaren Mehrwert und motiviere sie zum Weiterlesen.</p>
  </td>
</tr>
<tr>
  <td class="fbw ph" style="background-color:#ffffff;padding:0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="fbd" style="border-top:1px solid #e4e0db;font-size:0;line-height:0;">&nbsp;</td></tr></table>
  </td>
</tr>
<tr>
  <td class="fbw p-sec" style="background-color:#ffffff;padding:36px 56px;">
    <h3 class="fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:19px;font-weight:700;color:#000000;margin:0 0 12px 0;line-height:1.3;">Abschnittstitel</h3>
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#3a3a3a;margin:0 0 18px 0;line-height:1.7;">Hier folgt der Flie&szlig;text zu diesem Abschnitt. Beschreibe das Thema pr&auml;gnant und gib deinen Lesern einen klaren Mehrwert.</p>
    <a href="https://getimpulse.de/" class="fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;color:#000000;text-decoration:none;border-bottom:1px solid #000000;padding-bottom:2px;">Mehr erfahren &rsaquo;</a>
  </td>
</tr>
<tr>
  <td class="fbw ph" style="background-color:#ffffff;padding:0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="fbd" style="border-top:1px solid #e4e0db;font-size:0;line-height:0;">&nbsp;</td></tr></table>
  </td>
</tr>
<tr>
  <td class="fbw p-sec" style="background-color:#ffffff;padding:32px 56px;">
    <h3 class="fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:19px;font-weight:700;color:#000000;margin:0 0 12px 0;line-height:1.3;">Weiterer Abschnitt</h3>
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#3a3a3a;margin:0 0 18px 0;line-height:1.7;">Ank&uuml;ndigungen, Neuigkeiten oder weiterf&uuml;hrende Informationen – Text und Link einfach anpassen.</p>
    <a href="https://getimpulse.de/" class="fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;color:#000000;text-decoration:none;border-bottom:1px solid #000000;padding-bottom:2px;">Zum Angebot &rsaquo;</a>
  </td>
</tr>
<tr>
  <td class="fbw p-cta" style="background-color:#ffffff;padding:4px 56px 52px;text-align:center;">
    <!--[if mso]>
    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="https://getimpulse.de/" style="height:52px;v-text-anchor:middle;width:240px;" arcsize="12%" strokecolor="#b5ac9e" fillcolor="#b5ac9e"><w:anchorlock/><center style="color:#ffffff;font-family:Arial,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Jetzt entdecken</center></v:roundrect>
    <![endif]-->
    <!--[if !mso]><!-->
    <a href="https://getimpulse.de/" class="fbtn" style="display:inline-block;background-color:#b5ac9e;color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;padding:16px 48px;border-radius:6px;">Jetzt entdecken</a>
    <!--<![endif]-->
  </td>
</tr>"""

_DEFAULT_EMAIL_FOOTER = """<tr>
  <td class="fbk" style="background-color:#000000;padding:40px 40px 32px;text-align:center;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto 28px auto;">
      <tr>
        <td style="padding:0 10px;"><a href="https://www.instagram.com/getimpulse/" title="Instagram" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20" rx="5" fill="none" stroke="#ffffff" stroke-width="1.8"/><circle cx="12" cy="12" r="4.2" fill="none" stroke="#ffffff" stroke-width="1.8"/><circle cx="17.3" cy="6.7" r="1.1" fill="#ffffff"/></svg></a></td>
        <td style="padding:0 10px;"><a href="https://www.youtube.com/@getimpulse886" title="YouTube" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24"><path d="M22.5 6.6s-.2-1.7-1-2.4c-.9-1-2-1-2.4-1.1C16.5 3 12 3 12 3s-4.5 0-7.1.1c-.5.1-1.5.1-2.4 1.1C1.7 4.9 1.5 6.6 1.5 6.6S1.2 8.5 1.2 10.4v1.8c0 1.9.3 3.8.3 3.8s.2 1.7 1 2.4c.9 1 2.1.9 2.7 1 1.9.2 8.3.2 8.3.2s4.5 0 7.1-.2c.5-.1 1.5-.1 2.4-1.1.8-.7 1-2.4 1-2.4s.3-1.9.3-3.8V10.4c0-1.9-.3-3.8-.3-3.8z" fill="#ffffff"/><polygon points="9.7,15.5 9.7,8.4 16.1,12" fill="#000000"/></svg></a></td>
        <td style="padding:0 10px;"><a href="https://www.tiktok.com/@getimpulse" title="TikTok" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="24" height="26" viewBox="0 0 24 26"><path d="M19.3 5.1A4.6 4.6 0 0 1 14.8.5h-3.4v14.9a2.8 2.8 0 0 1-2.8 2.5 2.8 2.8 0 0 1-2.8-2.8 2.8 2.8 0 0 1 2.8-2.8c.3 0 .5 0 .8.1V9a6.2 6.2 0 0 0-.8-.1 6.2 6.2 0 0 0-6.2 6.2 6.2 6.2 0 0 0 6.2 6.2 6.2 6.2 0 0 0 6.2-6.2V7.5a7.9 7.9 0 0 0 4.6 1.5V5.6a4.6 4.6 0 0 1-3.1-1.5z" fill="#ffffff"/></svg></a></td>
        <td style="padding:0 10px;"><a href="https://www.facebook.com/getimpulse.berlinheidestrasse/" title="Facebook" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24"><path d="M24 12.1C24 5.4 18.6 0 12 0S0 5.4 0 12.1c0 6 4.4 11 10.1 11.9V15.6H7.1v-3.5h3V9.4c0-3 1.8-4.6 4.5-4.6 1.3 0 2.6.2 2.6.2v2.9h-1.5c-1.4 0-1.9.9-1.9 1.8v2.2h3.2l-.5 3.5h-2.7V24C19.6 23.1 24 18.1 24 12.1z" fill="#ffffff"/></svg></a></td>
      </tr>
    </table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px;">
      <tr><td style="border-top:1px solid #2c2c2c;font-size:0;line-height:0;">&nbsp;</td></tr>
    </table>
    <p class="fcw" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#ffffff;margin:0 0 10px 0;line-height:1.5;letter-spacing:0.5px;">Get-Impulse Berlin GmbH</p>
    <p class="fcm" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#7a7a7a;margin:0 0 4px 0;line-height:1.7;">Heidestra&szlig;e 11, 10557 Berlin</p>
    <p class="fcm" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#7a7a7a;margin:0 0 24px 0;line-height:1.7;"><a href="tel:+493030106609" style="color:#7a7a7a;text-decoration:none;">030 30106609</a></p>
    <p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#4a4a4a;margin:0;line-height:1.7;">
      Du erh&auml;ltst diese E-Mail, weil du dich f&uuml;r unseren Newsletter angemeldet hast.<br>
      <a href="https://getimpulse.de/datenschutz" style="color:#7a7a7a;text-decoration:underline;">Datenschutz</a>&nbsp;&middot;&nbsp;<a href="https://getimpulse.de/impressum" style="color:#7a7a7a;text-decoration:underline;">Impressum</a>&nbsp;&middot;&nbsp;<a href="UNSUBSCRIBE_URL" style="color:#7a7a7a;text-decoration:underline;">Abmelden</a>
    </p>
  </td>
</tr>
</table>
</td></tr>
</table>"""

_ACCESS_CODE_BODY_TMPL = """<tr>
  <td class="fbw p-hero" style="background-color:#ffffff;padding:52px 56px 36px;text-align:center;">
    <h1 class="h1 fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:36px;font-weight:700;color:#000000;margin:0 0 22px 0;line-height:1.2;">Dein Zugangscode</h1>
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#3a3a3a;margin:0 0 28px 0;line-height:1.7;">Hallo {member_name},<br><br>hier ist dein pers&ouml;nlicher Zugangscode f&uuml;r das <strong>Freie Training</strong>:</p>
    <div style="display:inline-block;background-color:#f0ede9;border:1px solid #e4e0db;padding:20px 44px;border-radius:6px;">
      <span style="font-family:Arial,Helvetica,sans-serif;font-size:34px;font-weight:700;color:#000000;letter-spacing:10px;">{code}</span>
    </div>
  </td>
</tr>
<tr>
  <td class="fbw ph" style="background-color:#ffffff;padding:0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="fbd" style="border-top:1px solid #e4e0db;font-size:0;line-height:0;">&nbsp;</td></tr></table>
  </td>
</tr>
<tr>
  <td class="fbw p-sec" style="background-color:#ffffff;padding:28px 56px 32px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding-bottom:10px;font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#7a7a7a;">G&uuml;ltig von</td>
        <td style="padding-bottom:10px;font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;color:#000000;text-align:right;">{valid_from}</td>
      </tr>
      <tr>
        <td style="font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#7a7a7a;">G&uuml;ltig bis</td>
        <td style="font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;color:#000000;text-align:right;">{valid_until}</td>
      </tr>
    </table>
  </td>
</tr>
{checks_row}"""

_ACCESS_CODE_CHECKS_ROW = """<tr>
  <td class="fbw ph" style="background-color:#ffffff;padding:0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="fbd" style="border-top:1px solid #e4e0db;font-size:0;line-height:0;">&nbsp;</td></tr></table>
  </td>
</tr>
<tr>
  <td class="fbw p-cta" style="background-color:#ffffff;padding:4px 56px 52px;text-align:center;">
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#3a3a3a;margin:0 0 20px 0;line-height:1.7;">Bitte melde dich vor und nach dem Training &uuml;ber den Check-In/Out-Link an.</p>
    <!--[if mso]>
    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{checks_url}" style="height:52px;v-text-anchor:middle;width:260px;" arcsize="12%" strokecolor="#b5ac9e" fillcolor="#b5ac9e"><w:anchorlock/><center style="color:#ffffff;font-family:Arial,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Check-In / Check-Out</center></v:roundrect>
    <![endif]-->
    <!--[if !mso]><!-->
    <a href="{checks_url}" class="fbtn" style="display:inline-block;background-color:#b5ac9e;color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;padding:16px 48px;border-radius:6px;">Check-In / Check-Out</a>
    <!--<![endif]-->
  </td>
</tr>"""

_RESET_BODY_TMPL = """<tr>
  <td class="fbw p-hero" style="background-color:#ffffff;padding:52px 56px 36px;text-align:center;">
    <h1 class="h1 fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:36px;font-weight:700;color:#000000;margin:0 0 22px 0;line-height:1.2;">Passwort zur&uuml;cksetzen</h1>
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#3a3a3a;margin:0;line-height:1.7;">Du hast ein neues Passwort f&uuml;r dein Studio-Access-Konto angefordert.<br><br>Klicke auf den Button, um ein neues Passwort zu vergeben. Der Link ist <strong>60 Minuten</strong> g&uuml;ltig.</p>
  </td>
</tr>
<tr>
  <td class="fbw p-cta" style="background-color:#ffffff;padding:4px 56px 52px;text-align:center;">
    <!--[if mso]>
    <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="urn:schemas-microsoft-com:office:word" href="{reset_url}" style="height:52px;v-text-anchor:middle;width:260px;" arcsize="12%" strokecolor="#b5ac9e" fillcolor="#b5ac9e"><w:anchorlock/><center style="color:#ffffff;font-family:Arial,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Passwort setzen</center></v:roundrect>
    <![endif]-->
    <!--[if !mso]><!-->
    <a href="{reset_url}" class="fbtn" style="display:inline-block;background-color:#b5ac9e;color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:700;letter-spacing:1px;text-transform:uppercase;text-decoration:none;padding:16px 48px;border-radius:6px;">Passwort setzen</a>
    <!--<![endif]-->
  </td>
</tr>"""


def get_email_template(db: "Database") -> dict[str, str]:
    raw = db.get_system_setting("email_template") or {}
    return {
        "header_html": str(raw.get("header_html") or _DEFAULT_EMAIL_HEADER),
        "body_html": str(raw.get("body_html") or _DEFAULT_EMAIL_BODY),
        "footer_html": str(raw.get("footer_html") or _DEFAULT_EMAIL_FOOTER),
    }


def _assemble_email_html(header: str, body_rows: str, footer: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="de">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        "</head>\n"
        "<body>\n"
        f"{header}\n"
        f"{body_rows}\n"
        f"{footer}\n"
        "</body>\n"
        "</html>"
    )


def build_access_code_email_html(
    db: "Database",
    *,
    member_name: str,
    code: str,
    valid_from: str,
    valid_until: str,
    checks_url: str | None,
) -> str:
    tpl = get_email_template(db)
    checks_row = _ACCESS_CODE_CHECKS_ROW.replace("{checks_url}", checks_url) if checks_url else ""
    body = _ACCESS_CODE_BODY_TMPL.format(
        member_name=member_name,
        code=code,
        valid_from=valid_from,
        valid_until=valid_until,
        checks_row=checks_row,
    )
    return _assemble_email_html(tpl["header_html"], body, tpl["footer_html"])


def build_password_reset_email_html(db: "Database", *, reset_url: str) -> str:
    tpl = get_email_template(db)
    body = _RESET_BODY_TMPL.replace("{reset_url}", reset_url)
    return _assemble_email_html(tpl["header_html"], body, tpl["footer_html"])


def build_test_email_html(db: "Database") -> str:
    tpl = get_email_template(db)
    return _assemble_email_html(tpl["header_html"], tpl["body_html"], tpl["footer_html"])


def issue_check_in_token(*, access_window_id: int, settings: Settings, ttl_seconds: int) -> str:
    return issue_token(
        subject=f"checkin:{access_window_id}",
        role="checkin",
        secret=settings.jwt_secret,
        ttl_seconds=ttl_seconds,
    )


def decode_check_in_token(*, token: str, settings: Settings) -> int:
    payload = decode_token(token, settings.jwt_secret)
    if payload.get("role") != "checkin":
        raise ValueError("Invalid check-in token role.")
    subject = str(payload.get("sub") or "")
    if not subject.startswith("checkin:"):
        raise ValueError("Invalid check-in token subject.")
    return int(subject.split(":", 1)[1])


def build_check_in_link(*, access_window_id: int, ends_at: datetime, settings: Settings) -> str:
    ttl_seconds = max(int((ends_at - datetime.now(UTC)).total_seconds()) + 86400, 3600)
    token = issue_check_in_token(
        access_window_id=access_window_id,
        settings=settings,
        ttl_seconds=ttl_seconds,
    )
    return f"{settings.app_public_base_url.rstrip('/')}/check-in?token={token}"


def issue_checks_token(
    *,
    member_id: int,
    settings: Settings,
    ttl_seconds: int = 86400,
) -> str:
    """Issue a JWT for a member's /checks session (24h default)."""
    return issue_token(
        subject=f"checks:{member_id}",
        role="checks",
        secret=settings.jwt_secret,
        ttl_seconds=ttl_seconds,
    )


def decode_checks_token(*, token: str, settings: Settings) -> int:
    """Decode a /checks session JWT and return the member_id."""
    payload = decode_token(token, settings.jwt_secret)
    if payload.get("role") != "checks":
        raise ValueError("Ungültiges Session-Token.")
    subject = str(payload.get("sub") or "")
    if not subject.startswith("checks:"):
        raise ValueError("Ungültiges Session-Token.")
    return int(subject.split(":", 1)[1])


def resolve_checks_session(
    *,
    db: Database,
    settings: Settings,
    token: str | None = None,
    email: str | None = None,
    code: str | None = None,
) -> dict[str, object]:
    """Authenticate a member for the /checks shell."""
    if token:
        member_id = decode_checks_token(token=token, settings=settings)
        member = db.get_member_by_id(member_id=member_id)
        if not member:
            raise ValueError("Mitglied nicht gefunden.")
    else:
        if not email or not code:
            raise ValueError("E-Mail und Code sind erforderlich.")
        verified = db.verify_member_access_code(
            email=email,
            raw_code=code.strip(),
            now=datetime.now(UTC),
        )
        if not verified:
            raise ValueError(
                "Code ungültig oder kein aktives Zugangsfenster gefunden."
            )
        member_id = int(verified["member_id"])
        member = db.get_member_by_id(member_id=member_id)
        if not member:
            raise ValueError("Mitglied nicht gefunden.")

    has_checkin_funnel = db.get_funnel_by_type("checkin") is not None
    has_checkout_funnel = db.get_funnel_by_type("checkout") is not None

    windows_raw = db.list_member_windows_with_status(
        member_id=member_id,
        from_dt=datetime.now(UTC) - timedelta(hours=1),
    )

    session_token = issue_checks_token(
        member_id=member_id,
        settings=settings,
        ttl_seconds=86400,
    )
    member_name = (
        " ".join(
            str(p)
            for p in [member.get("first_name"), member.get("last_name")]
            if p
        ).strip()
        or str(member.get("email") or "Member")
    )
    return {
        "token": session_token,
        "member_name": member_name,
        "member_email": str(member.get("email") or ""),
        "windows": [
            {
                **w,
                "has_checkin_funnel": has_checkin_funnel,
                "has_checkout_funnel": has_checkout_funnel,
            }
            for w in windows_raw
        ],
    }


def get_active_funnel_for_type(
    *,
    db: Database,
    funnel_type: str,
) -> dict[str, object] | None:
    """Return the active (most recent) funnel template for a given type."""
    return db.get_funnel_by_type(funnel_type)


def submit_checks_funnel(
    *,
    db: Database,
    settings: Settings,
    token: str,
    window_id: int,
    funnel_type: str,
    steps_data: list[dict[str, object]],
) -> dict[str, object]:
    """Validate and persist a funnel submission (checkin or checkout)."""
    member_id = decode_checks_token(token=token, settings=settings)

    funnel = db.get_funnel_by_type(funnel_type)
    if not funnel:
        raise ValueError(f"Kein aktiver {funnel_type}-Funnel konfiguriert.")

    window = db.get_access_window_detail(access_window_id=window_id)
    if not window:
        raise ValueError("Zugangsfenster nicht gefunden.")
    if int(window["member_id"]) != member_id:
        raise ValueError("Zugangsfenster gehört nicht zu diesem Mitglied.")

    step_map = {int(s["id"]): s for s in (funnel.get("steps") or [])}

    for step in funnel.get("steps") or []:
        step_id = int(step["id"])
        if not step.get("requires_note"):
            continue
        answer = next(
            (d for d in steps_data if int(d.get("step_id", 0)) == step_id),
            None,
        )
        note = (answer.get("note") or "").strip() if answer else ""
        if not note:
            raise ValueError(
                f"Schritt '{step['title']}' erfordert eine Notiz."
            )

    submission = db.create_funnel_submission(
        access_window_id=window_id,
        template_id=int(funnel["id"]),
        entry_source=f"checks-{funnel_type}",
        success=True,
    )
    for step_data in steps_data:
        step_id = int(step_data.get("step_id", 0))
        if step_id not in step_map:
            continue
        db.create_funnel_step_event(
            submission_id=int(submission["id"]),
            step_id=step_id,
            status="completed",
            note=step_data.get("note") or None,
            photo_path=None,
        )

    checklist_payload = [
        {
            "step_id": d.get("step_id"),
            "checked": d.get("checked", False),
            "note": d.get("note", ""),
        }
        for d in steps_data
    ]

    if funnel_type == "checkin":
        record = db.upsert_access_window_checkin(
            access_window_id=window_id,
            member_id=member_id,
            source="checks-funnel",
            rules_accepted=True,
            checklist=checklist_payload,
        )
    else:
        record = db.upsert_window_checkout(
            access_window_id=window_id,
            member_id=member_id,
            source="checks-funnel",
            checklist=checklist_payload,
        )

    return {
        "submitted": True,
        "funnel_type": funnel_type,
        "window_id": window_id,
        "confirmed_at": record.get("confirmed_at"),
    }


def build_checks_link(
    *,
    member_id: int,
    settings: Settings,
    ttl_seconds: int = 86400,
) -> str:
    token = issue_checks_token(
        member_id=member_id,
        settings=settings,
        ttl_seconds=ttl_seconds,
    )
    return f"{settings.app_public_base_url.rstrip('/')}/checks?token={token}"


def _notify_telegram(
    *,
    db: Database,
    settings: Settings,
    text: str,
) -> bool:
    telegram = TelegramService(get_effective_telegram_config(db, settings))
    return telegram.send_message(text=text)


def create_operational_alert(
    *,
    db: Database,
    settings: Settings,
    severity: str,
    kind: str,
    message: str,
    payload: dict[str, object] | None = None,
    notify_telegram: bool = True,
) -> None:
    db.create_alert(severity=severity, kind=kind, message=message, payload=payload)
    if notify_telegram and severity in {AlertSeverity.ERROR, AlertSeverity.WARNING}:
        try:
            _notify_telegram(
                db=db,
                settings=settings,
                text=f"[OpenGym] {severity.upper()} {kind}\n{message}",
            )
        except Exception:
            logger.exception("Failed to notify Telegram for alert kind=%s", kind)


def _hash_reset_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def request_password_reset(
    *,
    db: Database,
    settings: Settings,
    email: str,
) -> dict[str, bool]:
    user = db.get_user_by_email(email)
    if not user or not user["is_active"]:
        return {"accepted": True}

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    db.create_password_reset_token(
        user_id=int(user["id"]),
        token_hash=_hash_reset_token(token),
        expires_at=expires_at,
    )
    smtp = get_effective_smtp_config(db, settings)
    email_service = EmailService(settings, smtp)
    reset_url = f"{settings.app_public_base_url.rstrip('/')}/reset-password?token={token}"
    email_service.send_password_reset_email(
        to_email=str(user["email"]),
        reset_url=reset_url,
        html_body=build_password_reset_email_html(db, reset_url=reset_url),
    )
    return {"accepted": True}


def complete_password_reset(
    *,
    db: Database,
    token: str,
    password: str,
) -> dict[str, bool]:
    user = db.consume_password_reset_token(
        token_hash=_hash_reset_token(token),
        password=password,
        now=datetime.now(UTC),
    )
    if not user:
        raise ValueError("Invalid or expired password reset token.")
    return {"reset": True}


def get_member_detail(*, db: Database, member_id: int) -> dict[str, object] | None:
    member = db.get_member_by_id(member_id=member_id)
    if not member:
        return None
    return {
        "member": member,
        "bookings": db.list_member_bookings(member_id=member_id),
        "access_windows": db.list_member_access_windows(member_id=member_id),
        "access_codes": db.list_member_access_codes(member_id=member_id),
    }


def _member_name(window: dict[str, object]) -> str:
    return (
        " ".join(str(part) for part in [window.get("first_name"), window.get("last_name")] if part)
        .strip()
        or "Mitglied"
    )


def _issue_window_code(
    *,
    db: Database,
    settings: Settings,
    window: dict[str, object],
    code: str,
    is_emergency: bool,
) -> int:
    nuki = NukiClient(settings)
    email_service = EmailService(settings, get_effective_smtp_config(db, settings))
    check_in_settings = get_effective_check_in_settings(db, settings)
    try:
        nuki_auth_id = nuki.create_keypad_code(
            name=(
                f"member-{window['member_id']}-emergency-{window['id']}"
                if is_emergency
                else f"member-{window['member_id']}-cluster-{window['booking_id']}"
            ),
            code=code,
            allowed_from=window["starts_at"].isoformat(),
            allowed_until=window["ends_at"].isoformat(),
        )
        code_id = db.store_access_code(
            access_window_id=int(window["id"]),
            raw_code=code,
            nuki_auth_id=nuki_auth_id,
            status=AccessCodeStatus.PROVISIONED,
            expires_at=window["ends_at"],
            is_emergency=is_emergency,
        )
        if window.get("email"):
            try:
                _checks_url = build_checks_link(
                    member_id=int(window["member_id"]),
                    settings=settings,
                )
                emailed = email_service.send_access_code(
                    to_email=str(window["email"]),
                    member_name=_member_name(window),
                    code=code,
                    valid_from=_berlin(window["starts_at"], settings.timezone).isoformat(),
                    valid_until=_berlin(window["ends_at"], settings.timezone).isoformat(),
                    checks_url=_checks_url,
                    check_in_url=(
                        build_check_in_link(
                            access_window_id=int(window["id"]),
                            ends_at=window["ends_at"],
                            settings=settings,
                        )
                        if check_in_settings.get("enabled")
                        else None
                    ),
                    html_body=build_access_code_email_html(
                        db,
                        member_name=_member_name(window),
                        code=code,
                        valid_from=_berlin(window["starts_at"], settings.timezone).isoformat(),
                        valid_until=_berlin(window["ends_at"], settings.timezone).isoformat(),
                        checks_url=_checks_url,
                    ),
                )
            except Exception as exc:
                create_operational_alert(
                    db=db,
                    settings=settings,
                    severity=AlertSeverity.ERROR,
                    kind="access-email-failed",
                    message=(
                        f"Failed to send access code email for access window "
                        f"{window['id']}: {exc}"
                    ),
                    payload={
                        "access_window_id": int(window["id"]),
                        "member_id": int(window["member_id"]),
                    },
                )
            else:
                if emailed:
                    db.mark_code_emailed(code_id)
                else:
                    create_operational_alert(
                        db=db,
                        settings=settings,
                        severity=AlertSeverity.WARNING,
                        kind="access-email-skipped",
                        message=(
                            f"Access code email skipped for access window "
                            f"{window['id']} because SMTP is not configured."
                        ),
                        payload={
                            "access_window_id": int(window["id"]),
                            "member_id": int(window["member_id"]),
                        },
                    )
        return code_id
    finally:
        nuki.close()


def resend_access_code(
    *,
    db: Database,
    settings: Settings,
    access_window_id: int,
    actor_email: str,
) -> dict[str, object]:
    window = db.get_access_window_detail(access_window_id=access_window_id)
    if not window:
        raise ValueError("Access window not found.")
    if window["status"] not in {AccessWindowStatus.SCHEDULED, AccessWindowStatus.ACTIVE}:
        raise ValueError("Access window is not active or scheduled.")

    previous_code = db.get_active_code_for_window(access_window_id=access_window_id)
    if previous_code:
        db.mark_code_replaced(code_id=int(previous_code["id"]))
    replacement = f"{secrets.randbelow(1_000_000):06d}"
    new_code_id = _issue_window_code(
        db=db,
        settings=settings,
        window=window,
        code=replacement,
        is_emergency=False,
    )
    if previous_code:
        db.mark_code_replaced(code_id=int(previous_code["id"]), replaced_by_code_id=new_code_id)
    db.create_admin_action(
        actor_email=actor_email,
        action="resend-access-code",
        access_window_id=access_window_id,
        access_code_id=new_code_id,
        payload={"replaced_code_id": previous_code["id"] if previous_code else None},
    )
    return {
        "access_window_id": access_window_id,
        "code_id": new_code_id,
        "replaced_code_id": previous_code["id"] if previous_code else None,
        "sent": True,
    }


def deactivate_access_window(
    *,
    db: Database,
    access_window_id: int,
    actor_email: str,
) -> dict[str, object]:
    window = db.get_access_window_detail(access_window_id=access_window_id)
    if not window:
        raise ValueError("Access window not found.")
    previous_code = db.get_active_code_for_window(access_window_id=access_window_id)
    db.cancel_access_window(access_window_id=access_window_id)
    if previous_code:
        db.mark_code_replaced(code_id=int(previous_code["id"]))
    db.create_admin_action(
        actor_email=actor_email,
        action="deactivate-access-window",
        access_window_id=access_window_id,
        access_code_id=previous_code["id"] if previous_code else None,
    )
    return {
        "access_window_id": access_window_id,
        "deactivated": True,
        "previous_code_id": previous_code["id"] if previous_code else None,
    }


def issue_emergency_access_code(
    *,
    db: Database,
    settings: Settings,
    access_window_id: int,
    actor_email: str,
) -> dict[str, object]:
    window = db.get_access_window_detail(access_window_id=access_window_id)
    if not window:
        raise ValueError("Access window not found.")
    now = datetime.now(UTC)
    if window["ends_at"] < now:
        raise ValueError("Access window already ended.")

    previous_code = db.get_active_code_for_window(access_window_id=access_window_id)
    if previous_code:
        db.mark_code_replaced(code_id=int(previous_code["id"]))
    emergency = f"{secrets.randbelow(1_000_000):06d}"
    new_code_id = _issue_window_code(
        db=db,
        settings=settings,
        window=window,
        code=emergency,
        is_emergency=True,
    )
    if previous_code:
        db.mark_code_replaced(code_id=int(previous_code["id"]), replaced_by_code_id=new_code_id)
    db.create_admin_action(
        actor_email=actor_email,
        action="issue-emergency-code",
        access_window_id=access_window_id,
        access_code_id=new_code_id,
        payload={"replaced_code_id": previous_code["id"] if previous_code else None},
    )
    try:
        _notify_telegram(
            db=db,
            settings=settings,
            text=(
                f"[OpenGym] WARNING emergency-code-created\n"
                f"access_window={access_window_id}\n"
                f"actor={actor_email}\n"
                f"member_id={window['member_id']}"
            ),
        )
    except Exception:
        logger.exception(
            "Failed to notify Telegram for emergency code access_window=%s",
            access_window_id,
        )
    return {
        "access_window_id": access_window_id,
        "code_id": new_code_id,
        "replaced_code_id": previous_code["id"] if previous_code else None,
        "is_emergency": True,
        "sent": True,
    }


def _berlin(dt: datetime, tz_name: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz_name))


def _cluster_bookings(bookings: list) -> list[list]:
    booked = sorted(
        (booking for booking in bookings if booking["booking_status"] == "BOOKED"),
        key=lambda booking: booking["start_at"],
    )
    if not booked:
        return []

    clusters: list[list] = [[booked[0]]]
    current_end = booked[0]["end_at"]
    for booking in booked[1:]:
        if booking["start_at"] <= current_end:
            clusters[-1].append(booking)
            if booking["end_at"] > current_end:
                current_end = booking["end_at"]
            continue
        clusters.append([booking])
        current_end = booking["end_at"]
    return clusters


def _sync_customer_payload(
    *,
    db: Database,
    settings: Settings,
    customer: MagiclineCustomer,
    bookings: list,
    contracts: list[dict],
) -> dict[str, int]:
    member_id = db.upsert_member(customer.model_dump(mode="json"))
    entitlements = derive_entitlements(contracts, settings)
    db.upsert_entitlement(
        member_id=member_id,
        has_xxlarge=entitlements["has_xxlarge"],
        has_free_training_product=entitlements["has_free_training_product"],
        raw_source={"customer": customer.model_dump(mode="json"), "contracts": contracts},
    )

    synced_bookings = 0
    for booking in bookings:
        db.upsert_booking(
            member_id=member_id,
            booking=booking.model_dump(mode="json"),
            source_received_at=booking_effective_received_at(),
        )
        synced_bookings += 1

    booked_rows = db.list_member_access_bookings(
        member_id=member_id,
        title=settings.magicline_relevant_appointment_title,
    )
    clusters = _cluster_bookings(booked_rows)
    now_utc = datetime.now(UTC)
    representative_booking_ids: list[int] = []
    nuki = NukiClient(settings)
    try:
        for cluster in clusters:
            representative_booking = cluster[0]
            representative_booking_ids.append(representative_booking["id"])
            dispatch_at = representative_booking["start_at"] - timedelta(minutes=15)
            if dispatch_at < now_utc:
                dispatch_at = now_utc
            ends_at = cluster[-1]["end_at"] + timedelta(minutes=30)
            window_id = db.upsert_access_window(
                member_id=member_id,
                booking_id=representative_booking["id"],
                booking_ids=[booking["id"] for booking in cluster],
                booking_count=len(cluster),
                starts_at=representative_booking["start_at"] - timedelta(minutes=15),
                ends_at=ends_at,
                dispatch_at=dispatch_at,
                status=AccessWindowStatus.SCHEDULED,
                access_reason="freies-training-booking-cluster",
            )
            active_code = db.get_active_code_for_window(access_window_id=window_id)
            if active_code and active_code.get("expires_at") != ends_at:
                try:
                    if active_code.get("nuki_auth_id") is not None:
                        nuki.update_keypad_code(
                            auth_id=int(active_code["nuki_auth_id"]),
                            name=f"member-{member_id}-cluster-{representative_booking['id']}",
                            allowed_from=(
                                representative_booking["start_at"] - timedelta(minutes=15)
                            ).isoformat(),
                            allowed_until=ends_at.isoformat(),
                        )
                    db.sync_window_code_expiry(access_window_id=window_id, expires_at=ends_at)
                except Exception as exc:
                    logger.exception(
                        "Failed to update Nuki validity for access_window=%s",
                        window_id,
                    )
                    create_operational_alert(
                        db=db,
                        settings=settings,
                        severity=AlertSeverity.ERROR,
                        kind="nuki-code-update-failed",
                        message=(
                            f"Failed to update code validity for access window "
                            f"{window_id}: {exc}"
                        ),
                        payload={"access_window_id": window_id, "member_id": member_id},
                    )
        db.prune_member_windows(member_id=member_id, keep_booking_ids=representative_booking_ids)
    finally:
        nuki.close()

    return {
        "member_id": member_id,
        "bookings": synced_bookings,
        "windows": len(clusters),
    }


def sync_magicline_bookings(db: Database, settings: Settings) -> dict[str, int]:
    client = MagiclineClient(settings)
    synced_members = 0
    synced_bookings = 0
    scheduled_windows = 0
    try:
        for customer, bookings, contracts in client.sync_candidates():
            synced_members += 1
            result = _sync_customer_payload(
                db=db,
                settings=settings,
                customer=customer,
                bookings=bookings,
                contracts=contracts,
            )
            synced_bookings += result["bookings"]
            scheduled_windows += result["windows"]
    finally:
        client.close()

    return {"members": synced_members, "bookings": synced_bookings, "windows": scheduled_windows}


def sync_magicline_member_by_email(
    db: Database,
    settings: Settings,
    email: str,
) -> dict[str, int | str]:
    client = MagiclineClient(settings)
    try:
        customer = client.search_customer_by_email(email)
        if customer is None:
            return {"email": email, "members": 0, "bookings": 0, "windows": 0}
        contracts = client.list_customer_contracts(customer.id)
        bookings = [
            booking
            for booking in client.list_customer_bookings(customer.id)
            if is_access_booking(booking, settings)
        ]
        result = _sync_customer_payload(
            db=db,
            settings=settings,
            customer=customer,
            bookings=bookings,
            contracts=contracts,
        )
        return {"email": email, "members": 1, **result}
    finally:
        client.close()


def list_magicline_bookables(settings: Settings) -> list[dict[str, str | int | None]]:
    client = MagiclineClient(settings)
    try:
        bookables = client.list_bookable_appointments()
        return [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "category": item.get("category"),
                "duration": item.get("duration"),
            }
            for item in bookables
        ]
    finally:
        client.close()


def should_process_magicline_webhook(payload: dict[str, object], settings: Settings) -> bool:
    event_type = str(
        payload.get("eventType")
        or payload.get("event_type")
        or payload.get("type")
        or ""
    ).upper()
    if "APPOINTMENT" not in event_type and "BOOKING" not in event_type:
        return False

    title = payload.get("title")
    if isinstance(title, str):
        return title == settings.magicline_relevant_appointment_title

    appointment = payload.get("appointment")
    if isinstance(appointment, dict):
        appointment_title = appointment.get("title")
        if isinstance(appointment_title, str):
            return appointment_title == settings.magicline_relevant_appointment_title

    booking = payload.get("booking")
    if isinstance(booking, dict):
        booking_title = booking.get("title")
        if isinstance(booking_title, str):
            return booking_title == settings.magicline_relevant_appointment_title

    return True


def process_magicline_webhook(
    db: Database,
    settings: Settings,
    payload: dict[str, object],
) -> dict[str, int | str | bool]:
    payload_items = payload.get("payload")
    if isinstance(payload_items, list) and payload_items:
        normalized_payload = dict(payload)
        first_item = payload_items[0]
        if isinstance(first_item, dict):
            normalized_payload.setdefault("eventType", first_item.get("type"))
            normalized_payload.setdefault("eventId", payload.get("uuid"))
            content = first_item.get("content")
            if isinstance(content, dict):
                normalized_payload.update(content)
        payload = normalized_payload
    elif isinstance(payload_items, dict):
        normalized_payload = dict(payload)
        normalized_payload.setdefault("eventType", payload_items.get("type"))
        normalized_payload.setdefault("eventId", payload.get("uuid"))
        content = payload_items.get("content")
        if isinstance(content, dict):
            normalized_payload.update(content)
        payload = normalized_payload

    event_id = str(
        payload.get("eventId")
        or payload.get("id")
        or payload.get("event_id")
        or payload.get("uuid")
        or ""
    ).strip()
    if not event_id:
        raise ValueError("Webhook payload does not contain a stable event id.")

    event_type = str(payload.get("eventType") or payload.get("event_type") or "")
    created = db.record_webhook_event(
        provider="magicline",
        event_id=event_id,
        event_type=event_type or None,
        payload=payload,
    )
    if not created:
        return {"event_id": event_id, "event_type": event_type, "duplicate": True}

    if not should_process_magicline_webhook(payload, settings):
        return {
            "event_id": event_id,
            "event_type": event_type,
            "duplicate": False,
            "processed": False,
        }

    sync_result = sync_magicline_bookings(db, settings)
    return {
        "event_id": event_id,
        "event_type": event_type,
        "duplicate": False,
        "processed": True,
        **sync_result,
    }


def inspect_magicline_member_by_email(settings: Settings, email: str) -> dict[str, object]:
    client = MagiclineClient(settings)
    try:
        customer = client.search_customer_by_email(email)
        if customer is None:
            return {"email": email, "found": False}

        contracts = client.list_customer_contracts(customer.id)
        all_bookings = client.list_customer_bookings(customer.id)
        relevant_bookings = [
            booking for booking in all_bookings if is_access_booking(booking, settings)
        ]
        relevant_booking_clusters = _cluster_bookings(
            [
                {
                    "id": booking.booking_id,
                    "start_at": booking.start_date_time,
                    "end_at": booking.end_date_time,
                    "booking_status": booking.booking_status,
                }
                for booking in relevant_bookings
            ]
        )
        entitlements = derive_entitlements(contracts, settings)

        return {
            "email": email,
            "found": True,
            "customer": customer.model_dump(mode="json"),
            "entitlements": entitlements,
            "contract_rate_names": [
                contract.get("rateName")
                for contract in contracts
                if contract.get("contractStatus") == "ACTIVE" and contract.get("rateName")
            ],
            "active_module_names": [
                module.get("rateName")
                for contract in contracts
                if contract.get("contractStatus") == "ACTIVE"
                for module in contract.get("moduleContracts", [])
                if module.get("contractStatus") == "ACTIVE" and module.get("rateName")
            ],
            "active_flat_fee_names": [
                fee.get("rateName")
                for contract in contracts
                if contract.get("contractStatus") == "ACTIVE"
                for fee in contract.get("flatFeeContracts", [])
                if fee.get("contractStatus") == "ACTIVE" and fee.get("rateName")
            ],
            "all_booking_titles": sorted(
                {booking.title for booking in all_bookings if booking.title}
            ),
            "relevant_booking_count": len(relevant_bookings),
            "relevant_booking_cluster_count": len(relevant_booking_clusters),
            "relevant_booking_clusters": [
                {
                    "booking_ids": [booking["id"] for booking in cluster],
                    "starts_at": cluster[0]["start_at"].isoformat(),
                    "ends_at": cluster[-1]["end_at"].isoformat(),
                    "booking_count": len(cluster),
                }
                for cluster in relevant_booking_clusters
            ],
            "relevant_bookings": [
                booking.model_dump(mode="json")
                for booking in relevant_bookings
            ],
        }
    finally:
        client.close()


def provision_due_codes(db: Database, settings: Settings) -> int:
    nuki = NukiClient(settings)
    email_service = EmailService(settings, get_effective_smtp_config(db, settings))
    now = datetime.now(UTC)
    due = db.due_access_windows(now)
    count = 0
    try:
        for window in due:
            code = f"{secrets.randbelow(1_000_000):06d}"
            starts_at = _berlin(window["starts_at"], settings.timezone)
            ends_at = _berlin(window["ends_at"], settings.timezone)
            name = f"member-{window['member_id']}-cluster-{window['booking_id']}"
            try:
                nuki_auth_id = nuki.create_keypad_code(
                    name=name,
                    code=code,
                    allowed_from=window["starts_at"].isoformat(),
                    allowed_until=window["ends_at"].isoformat(),
                )
                code_id = db.store_access_code(
                    access_window_id=window["id"],
                    raw_code=code,
                    nuki_auth_id=nuki_auth_id,
                    status=AccessCodeStatus.PROVISIONED,
                    expires_at=window["ends_at"],
                )
                member_name = " ".join(
                    part
                    for part in [window.get("first_name"), window.get("last_name")]
                    if part
                ).strip() or "Mitglied"
                if window.get("email"):
                    try:
                        _prov_checks_url = build_checks_link(
                            member_id=int(window["member_id"]),
                            settings=settings,
                        )
                        emailed = email_service.send_access_code(
                            to_email=window["email"],
                            member_name=member_name,
                            code=code,
                            valid_from=starts_at.isoformat(),
                            valid_until=ends_at.isoformat(),
                            checks_url=_prov_checks_url,
                            check_in_url=(
                                build_check_in_link(
                                    access_window_id=int(window["id"]),
                                    ends_at=window["ends_at"],
                                    settings=settings,
                                )
                                if get_effective_check_in_settings(db, settings).get("enabled")
                                else None
                            ),
                            html_body=build_access_code_email_html(
                                db,
                                member_name=member_name,
                                code=code,
                                valid_from=starts_at.isoformat(),
                                valid_until=ends_at.isoformat(),
                                checks_url=_prov_checks_url,
                            ),
                        )
                    except Exception as exc:
                        create_operational_alert(
                            db=db,
                            settings=settings,
                            severity=AlertSeverity.ERROR,
                            kind="access-email-failed",
                            message=(
                                f"Failed to send access code email for access window "
                                f"{window['id']}: {exc}"
                            ),
                            payload={
                                "access_window_id": window["id"],
                                "member_id": window["member_id"],
                            },
                        )
                    else:
                        if emailed:
                            db.mark_code_emailed(code_id)
                        else:
                            create_operational_alert(
                                db=db,
                                settings=settings,
                                severity=AlertSeverity.WARNING,
                                kind="access-email-skipped",
                                message=(
                                    f"Access code email skipped for access window "
                                    f"{window['id']} because SMTP is not configured."
                                ),
                                payload={
                                    "access_window_id": window["id"],
                                    "member_id": window["member_id"],
                                },
                            )
                count += 1
            except Exception as exc:
                logger.exception("Provisioning failed for access_window=%s", window["id"])
                create_operational_alert(
                    db=db,
                    settings=settings,
                    severity=AlertSeverity.ERROR,
                    kind="code-provisioning-failed",
                    message=f"Access window {window['id']} failed: {exc}",
                    payload={"access_window_id": window["id"]},
                )
    finally:
        nuki.close()
    db.expire_finished_windows(now)
    return count


def resolve_public_check_in(
    *,
    db: Database,
    settings: Settings,
    token: str | None = None,
    email: str | None = None,
    code: str | None = None,
) -> dict[str, object]:
    if token:
        access_window_id = decode_check_in_token(token=token, settings=settings)
        window = db.get_check_in_window(access_window_id=access_window_id)
    else:
        if not email or not code:
            raise ValueError("Email and code are required.")
        window = db.verify_member_access_code(
            email=email,
            raw_code=code,
            now=datetime.now(UTC),
        )
    if not window:
        raise ValueError("Kein passender Trainingsblock gefunden.")
    session_token = issue_check_in_token(
        access_window_id=int(window["access_window_id"]),
        settings=settings,
        ttl_seconds=max(int((window["ends_at"] - datetime.now(UTC)).total_seconds()) + 86400, 3600),
    )
    return {
        "token": session_token,
        "entry_source": "mail-link" if token else "studio-qr",
        "settings": get_effective_check_in_settings(db, settings),
        "window": window,
    }


def submit_public_check_in(
    *,
    db: Database,
    settings: Settings,
    token: str,
    rules_accepted: bool,
    checklist: list[dict[str, object]],
    source: str,
) -> dict[str, object]:
    access_window_id = decode_check_in_token(token=token, settings=settings)
    window = db.get_check_in_window(access_window_id=access_window_id)
    if not window:
        raise ValueError("Trainingsblock nicht gefunden.")
    if not rules_accepted:
        raise ValueError("Hausregeln müssen bestätigt werden.")

    config = get_effective_check_in_settings(db, settings)
    expected_items = {
        str(item["id"]): str(item["label"])
        for item in config.get("checklist_items", [])
        if isinstance(item, dict)
    }
    normalized = []
    for item in checklist:
        item_id = str(item.get("id") or "")
        if item_id not in expected_items:
            continue
        normalized.append(
            {
                "id": item_id,
                "label": expected_items[item_id],
                "checked": bool(item.get("checked")),
            }
        )
    present_ids = {row["id"] for row in normalized}
    missing = [item_id for item_id in expected_items if item_id not in present_ids]
    if missing:
        raise ValueError("Checkliste ist unvollständig.")
    if not all(bool(item["checked"]) for item in normalized):
        raise ValueError("Alle Checklistenpunkte müssen bestätigt werden.")

    record = db.upsert_access_window_checkin(
        access_window_id=access_window_id,
        member_id=int(window["member_id"]),
        source=source,
        rules_accepted=True,
        checklist=normalized,
    )
    return {
        "confirmed": True,
        "success_message": config["success_message"],
        "check_in": record,
    }
