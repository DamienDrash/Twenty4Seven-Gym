"""Email template storage, assembly, and rendering."""
from __future__ import annotations
from typing import Any
from ..config import Settings
from ..db import Database
from .settings import get_branding_settings

# ── Template fragments ────────────────────────────────────────────

HEADER_HTML = """\
<style type="text/css">
  body,table,td,p,a,li{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%}
  table,td{mso-table-lspace:0pt;mso-table-rspace:0pt;border-collapse:collapse}
  img{-ms-interpolation-mode:bicubic;border:0;display:block;outline:none}
  *{box-sizing:border-box}
  :root{color-scheme:light only;supported-color-schemes:light}
  body{margin:0!important;padding:0!important;background-color:{body_bg_color}!important;color:#000!important;width:100%!important}
  @media only screen and (max-width:620px){
    .wrapper{width:100%!important;max-width:100%!important}
    .h1{font-size:27px!important;line-height:1.25!important}
    .ph{padding-left:24px!important;padding-right:24px!important}
    .p-hero{padding:36px 24px 28px!important}
    .p-sec{padding:28px 24px!important}
    .p-cta{padding:4px 24px 40px!important}
  }
</style>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{body_bg_color};">
<tr><td align="center" style="padding:28px 16px;">
<table role="presentation" class="wrapper" width="600" cellpadding="0" cellspacing="0"
       border="0" style="width:600px;max-width:600px;">
<tr>
  <td style="background-color:{header_bg_color};padding:22px 40px;text-align:center;">
    <a href="{logo_link_url}"
       style="font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:700;
              letter-spacing:4px;color:#fff;text-decoration:none;text-transform:uppercase;">
      {logo_placeholder}
    </a>
  </td>
</tr>"""

BODY_HTML = """\
<tr>
  <td style="background:#fff;padding:40px 56px;">
    <p style="font-family:Arial,sans-serif;font-size:15px;color:#3a3a3a;line-height:1.7;">
      Standard-Textnachricht — bitte im Admin-Bereich anpassen.
    </p>
  </td>
</tr>"""

FOOTER_HTML = """\
<tr>
  <td style="background:{footer_bg_color};padding:28px 40px 20px;text-align:center;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td align="center" style="padding-bottom:12px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center">
            <tr>{social_icons}</tr>
          </table>
        </td>
      </tr>
      <tr>
        <td align="center" style="font-family:Arial,sans-serif;font-size:12px;color:#7a7a7a;line-height:1.7;text-align:center;">{footer_text}</td>
      </tr>
    </table>
  </td>
</tr>
</table></td></tr></table>"""

ACCESS_CODE_BODY_HTML = """\
<tr>
  <td style="background:#fff;padding:52px 56px 36px;text-align:center;">
    <h1 style="font-family:Arial,sans-serif;font-size:36px;font-weight:700;
               color:#000;margin:0 0 22px;line-height:1.2;">
      Dein Zugangscode
    </h1>
    <p style="font-family:Arial,sans-serif;font-size:15px;color:#3a3a3a;
              margin:0 0 28px;line-height:1.7;">
      {greeting_html}
    </p>
    <div style="display:inline-block;background:#f0ede9;border:1px solid #e4e0db;
                padding:20px 44px;border-radius:6px;">
      <span style="font-family:Arial,sans-serif;font-size:34px;font-weight:700;
                   color:#000;letter-spacing:10px;">{code}</span>
    </div>
  </td>
</tr>
<tr><td style="background:#fff;padding:0 56px;">
  <hr style="border:0;border-top:1px solid #e4e0db;margin:0;">
</td></tr>
<tr>
  <td style="background:#fff;padding:28px 56px 32px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="padding-bottom:10px;font-family:Arial,sans-serif;font-size:12px;
                   font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#7a7a7a;">
          Gültig von</td>
        <td style="padding-bottom:10px;font-family:Arial,sans-serif;font-size:14px;
                   font-weight:700;color:#000;text-align:right;">{valid_from}</td>
      </tr>
      <tr>
        <td style="font-family:Arial,sans-serif;font-size:12px;font-weight:700;
                   text-transform:uppercase;letter-spacing:1px;color:#7a7a7a;">
          Gültig bis</td>
        <td style="font-family:Arial,sans-serif;font-size:14px;font-weight:700;
                   color:#000;text-align:right;">{valid_until}</td>
      </tr>
    </table>
  </td>
</tr>
{checks_row}"""

ACCESS_CODE_CHECKS_ROW = """\
<tr><td style="background:#fff;padding:0 56px;">
  <hr style="border:0;border-top:1px solid #e4e0db;margin:0;">
</td></tr>
<tr>
  <td style="background:#fff;padding:4px 56px 52px;text-align:center;">
    <p style="font-family:Arial,sans-serif;font-size:14px;color:#3a3a3a;
              margin:0 0 20px;line-height:1.7;">
      {below_code_text}
    </p>
    <a href="{checks_url}"
       style="display:inline-block;background:{accent_color};color:#fff;
              font-family:Arial,sans-serif;font-size:15px;font-weight:700;
              letter-spacing:1px;text-transform:uppercase;text-decoration:none;
              padding:16px 48px;border-radius:6px;">
      {cta_button_text}
    </a>
  </td>
</tr>"""


_DEFAULT_BLOCK_ORDER: list[str] = ["greeting", "code", "validity", "cta"]

_SEP = '<tr><td style="background:#fff;padding:0 56px;"><hr style="border:0;border-top:1px solid #e4e0db;margin:0;"></td></tr>'

_BLOCK_GREETING = (
    '<tr>'
    '<td style="background:#fff;padding:52px 56px 28px;text-align:center;">'
    '<h1 style="font-family:Arial,sans-serif;font-size:36px;font-weight:700;'
    'color:#000;margin:0 0 22px;line-height:1.2;">Dein Zugangscode</h1>'
    '<p style="font-family:Arial,sans-serif;font-size:15px;color:#3a3a3a;'
    'margin:0;line-height:1.7;">{greeting_html}</p>'
    '</td></tr>'
)

_BLOCK_CODE = (
    '<tr>'
    '<td style="background:#fff;padding:28px 56px;text-align:center;">'
    '<div style="display:inline-block;background:#f0ede9;border:1px solid #e4e0db;'
    'padding:20px 44px;border-radius:6px;">'
    '<span style="font-family:Arial,sans-serif;font-size:34px;font-weight:700;'
    'color:#000;letter-spacing:10px;">{code}</span>'
    '</div>'
    '</td></tr>'
)

_BLOCK_VALIDITY = (
    '<tr>'
    '<td style="background:#fff;padding:28px 56px;">'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
    '<tr>'
    '<td style="padding-bottom:10px;font-family:Arial,sans-serif;font-size:12px;'
    'font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#7a7a7a;">'
    'G\u00fcltig von</td>'
    '<td style="padding-bottom:10px;font-family:Arial,sans-serif;font-size:14px;'
    'font-weight:700;color:#000;text-align:right;">{valid_from}</td>'
    '</tr>'
    '<tr>'
    '<td style="font-family:Arial,sans-serif;font-size:12px;font-weight:700;'
    'text-transform:uppercase;letter-spacing:1px;color:#7a7a7a;">'
    'G\u00fcltig bis</td>'
    '<td style="font-family:Arial,sans-serif;font-size:14px;font-weight:700;'
    'color:#000;text-align:right;">{valid_until}</td>'
    '</tr>'
    '</table>'
    '</td></tr>'
)

_BLOCK_CTA = (
    '<tr>'
    '<td style="background:#fff;padding:8px 56px 52px;text-align:center;">'
    '<p style="font-family:Arial,sans-serif;font-size:14px;color:#3a3a3a;'
    'margin:0 0 20px;line-height:1.7;">{below_code_text}</p>'
    '<a href="{checks_url}"'
    ' style="display:inline-block;background:{accent_color};color:#fff;'
    'font-family:Arial,sans-serif;font-size:15px;font-weight:700;'
    'letter-spacing:1px;text-transform:uppercase;text-decoration:none;'
    'padding:16px 48px;border-radius:6px;">'
    '{cta_button_text}'
    '</a>'
    '</td></tr>'
)


RESET_BODY_HTML = """\
<tr>
  <td style="background:#fff;padding:52px 56px 36px;text-align:center;">
    <h1 style="font-family:Arial,sans-serif;font-size:36px;font-weight:700;
               color:#000;margin:0 0 22px;line-height:1.2;">
      Passwort zurücksetzen
    </h1>
    <p style="font-family:Arial,sans-serif;font-size:15px;color:#3a3a3a;
              margin:0;line-height:1.7;">
      Du hast ein neues Passwort für dein Studio-Access-Konto angefordert.<br><br>
      Klicke auf den Button, um ein neues Passwort zu vergeben.
      Der Link ist <strong>60 Minuten</strong> gültig.
    </p>
  </td>
</tr>
<tr>
  <td style="background:#fff;padding:4px 56px 52px;text-align:center;">
    <a href="{reset_url}"
       style="display:inline-block;background:#b5ac9e;color:#fff;
              font-family:Arial,sans-serif;font-size:15px;font-weight:700;
              letter-spacing:1px;text-transform:uppercase;text-decoration:none;
              padding:16px 48px;border-radius:6px;">
      Passwort setzen
    </a>
  </td>
</tr>"""

_BUILTIN_TEMPLATE: dict[str, str] = {
    "header_html": HEADER_HTML,
    "body_html": BODY_HTML,
    "footer_html": FOOTER_HTML,
    "access_code_body_html": ACCESS_CODE_BODY_HTML,
    "reset_body_html": RESET_BODY_HTML,
}


# ── SVG path data for /media/social-icon/{name} endpoint ─────────

_SOCIAL_SVG: dict[str, tuple[str, str]] = {
    "instagram": (
        "0 0 24 24",
        '<rect x="2" y="2" width="20" height="20" rx="5" fill="none" stroke="{c}" stroke-width="1.8"/>'
        '<circle cx="12" cy="12" r="4.2" fill="none" stroke="{c}" stroke-width="1.8"/>'
        '<circle cx="17.3" cy="6.7" r="1.1" fill="{c}"/>',
    ),
    "facebook": (
        "0 0 24 24",
        '<path d="M24 12.1C24 5.4 18.6 0 12 0S0 5.4 0 12.1c0 6 4.4 11 10.1 11.9V15.6H7.1v-3.5h3'
        'V9.4c0-3 1.8-4.6 4.5-4.6 1.3 0 2.6.2 2.6.2v2.9h-1.5c-1.4 0-1.9.9-1.9 1.8v2.2h3.2'
        'l-.5 3.5h-2.7V24C19.6 23.1 24 18.1 24 12.1z" fill="{c}"/>',
    ),
    "tiktok": (
        "0 0 24 26",
        '<path d="M19.3 5.1A4.6 4.6 0 0 1 14.8.5h-3.4v14.9a2.8 2.8 0 0 1-2.8 2.5'
        ' 2.8 2.8 0 0 1-2.8-2.8 2.8 2.8 0 0 1 2.8-2.8c.3 0 .5 0 .8.1V9'
        'a6.2 6.2 0 0 0-.8-.1 6.2 6.2 0 0 0-6.2 6.2 6.2 6.2 0 0 0 6.2 6.2'
        ' 6.2 6.2 0 0 0 6.2-6.2V7.5a7.9 7.9 0 0 0 4.6 1.5V5.6a4.6 4.6 0 0 1-3.1-1.5z" fill="{c}"/>',
    ),
    "youtube": (
        "0 0 24 24",
        '<path d="M22.5 6.6s-.2-1.7-1-2.4c-.9-1-2-1-2.4-1.1C16.5 3 12 3 12 3s-4.5 0-7.1.1'
        'c-.5.1-1.5.1-2.4 1.1C1.7 4.9 1.5 6.6 1.5 6.6S1.2 8.5 1.2 10.4v1.8c0 1.9.3 3.8.3 3.8'
        's.2 1.7 1 2.4c.9 1 2.1.9 2.7 1 1.9.2 8.3.2 8.3.2s4.5 0 7.1-.2c.5-.1 1.5-.1 2.4-1.1'
        '.8-.7 1-2.4 1-2.4s.3-1.9.3-3.8V10.4c0-1.9-.3-3.8-.3-3.8z" fill="{c}"/>'
        '<polygon points="9.7,15.5 9.7,8.4 16.1,12" fill="{bg}"/>',
    ),
}


def render_social_svg(name: str, icon_color: str, bg_color: str) -> str:
    """Return SVG markup for a social icon with given colors."""
    viewbox, paths = _SOCIAL_SVG[name]
    inner = paths.replace("{c}", icon_color).replace("{bg}", bg_color)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" width="22" height="22">{inner}</svg>'


# ── Settings helpers ──────────────────────────────────────────────

def get_email_template(db: Database) -> dict[str, str]:
    raw = db.get_system_setting("email_template")
    if raw is None:
        db.set_system_setting(key="email_template", value=_BUILTIN_TEMPLATE)
        return dict(_BUILTIN_TEMPLATE)
    return {
        "header_html": str(raw.get("header_html") or HEADER_HTML),
        "body_html": str(raw.get("body_html") or BODY_HTML),
        "footer_html": str(raw.get("footer_html") or FOOTER_HTML),
        "access_code_body_html": str(raw.get("access_code_body_html") or ACCESS_CODE_BODY_HTML),
        "reset_body_html": str(raw.get("reset_body_html") or RESET_BODY_HTML),
    }


def get_email_content(db: Database) -> dict[str, Any]:
    """Read structured email content from DB."""
    raw = db.get_system_setting("email_content") or {}
    return {
        "greeting_text": raw.get("greeting_text") or "Hallo {member_name},\n\nhier ist dein persönlicher Zugangscode für das Freie Training:",
        "below_code_text": raw.get("below_code_text") or "Bitte melde dich vor und nach dem Training über den Check-In/Out-Link an.",
        "cta_button_text": raw.get("cta_button_text") or "Check-In / Check-Out",
        "block_order": raw.get("block_order") or list(_DEFAULT_BLOCK_ORDER),
    }


# ── Rendering helpers ─────────────────────────────────────────────

def _social_icon_td(name: str, url: str, bg_color: str, base_url: str) -> str:
    icon_url = f"{base_url}/media/social-icon/{name}"
    return (
        f'<td style="padding:0 8px;">'
        f'<a href="{url}" title="{name.capitalize()}" '
        f'style="display:inline-block;background:{bg_color};border-radius:50%;'
        f'width:38px;height:38px;line-height:0;padding:8px;'
        f'text-decoration:none;">'
        f'<img src="{icon_url}" alt="{name.capitalize()}" width="22" height="22" '
        f'style="display:block;">'
        f'</a></td>'
    )


def _assemble_email_html(
    db: Database,
    settings: "Settings",
    body_content: str,
) -> str:
    branding = get_branding_settings(db)
    base_url = settings.app_public_base_url.rstrip("/")

    header_bg = (branding.get("header_bg_color") or "#000000").strip() or "#000000"
    body_bg = (branding.get("body_bg_color") or "#f0ede9").strip() or "#f0ede9"
    footer_bg = (branding.get("footer_bg_color") or "#000000").strip() or "#000000"

    logo_link = (branding.get("logo_link_url") or "https://getimpulse.de/").strip() or "https://getimpulse.de/"
    if branding.get("logo_url", "").strip():
        logo_placeholder = (
            f'<img src="{base_url}/media/logo" alt="Logo" '
            f'style="max-width:200px;height:auto;display:block;margin:0 auto;">'
        )
    else:
        logo_placeholder = "GETIMPULSE"

    header = (
        HEADER_HTML
        .replace("{body_bg_color}", body_bg)
        .replace("{header_bg_color}", header_bg)
        .replace("{logo_link_url}", logo_link)
        .replace("{logo_placeholder}", logo_placeholder)
    )

    social_parts = []
    social_bg = (branding.get("social_icon_bg_color") or "#333333").strip() or "#333333"
    for name in ("instagram", "facebook", "tiktok", "youtube"):
        url = (branding.get(f"{name}_url") or "").strip()
        if url:
            social_parts.append(_social_icon_td(name, url, social_bg, base_url))
    social_html = "".join(social_parts)

    footer_text_raw = (branding.get("footer_text") or "Heidestraße 11, 10557 Berlin<br>030 30106609").strip()
    footer = (
        FOOTER_HTML
        .replace("{footer_bg_color}", footer_bg)
        .replace("{social_icons}", social_html)
        .replace("{footer_text}", footer_text_raw.replace("\n", "<br>"))
    )

    return (
        "<!DOCTYPE html>"
        '<html lang="de"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only">'
        '<meta name="supported-color-schemes" content="light">'
        "</head><body>"
        f"{header}{body_content}{footer}"
        "</body></html>"
    )


# ── Public builders ───────────────────────────────────────────────

def build_access_code_email_html(
    db: Database,
    settings: "Settings",
    *,
    member_name: str,
    code: str,
    valid_from: str,
    valid_until: str,
    checks_url: str | None,
) -> str:
    content = get_email_content(db)
    branding = get_branding_settings(db)
    accent = (branding.get("accent_color") or "#b5ac9e").strip() or "#b5ac9e"

    greeting_raw = content["greeting_text"].replace("{member_name}", member_name)
    greeting_html = greeting_raw.replace("\n", "<br>")

    block_htmls: dict[str, str] = {
        "greeting": _BLOCK_GREETING.replace("{greeting_html}", greeting_html),
        "code": _BLOCK_CODE.replace("{code}", code),
        "validity": (
            _BLOCK_VALIDITY
            .replace("{valid_from}", valid_from)
            .replace("{valid_until}", valid_until)
        ),
    }
    if checks_url:
        block_htmls["cta"] = (
            _BLOCK_CTA
            .replace("{checks_url}", checks_url)
            .replace("{accent_color}", accent)
            .replace("{below_code_text}", content["below_code_text"])
            .replace("{cta_button_text}", content["cta_button_text"])
        )

    block_order = content.get("block_order") or _DEFAULT_BLOCK_ORDER
    parts: list[str] = []
    for block_name in block_order:
        html = block_htmls.get(block_name)
        if html is None:
            continue
        if parts:
            parts.append(_SEP)
        parts.append(html)

    body = "".join(parts)
    return _assemble_email_html(db, settings, body)


def build_password_reset_email_html(
    db: Database,
    settings: "Settings",
    *,
    reset_url: str,
) -> str:
    body = RESET_BODY_HTML.replace("{reset_url}", reset_url)
    return _assemble_email_html(db, settings, body)


def build_test_email_html(db: Database, settings: "Settings") -> str:
    tpl = get_email_template(db)
    return _assemble_email_html(db, settings, tpl["body_html"])
