"""Access code lifecycle: generate, provision, resend, deactivate, emergency."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from ..config import Settings
from ..db import Database
from ..enums import AccessCodeStatus, AccessWindowStatus, AlertSeverity
from ..notifications import EmailService
from ..nuki_client import NukiClient
from .alerts import create_operational_alert, notify_telegram
from .auth_tokens import build_check_in_link, build_checks_link
from .email_builder import build_access_code_email_html
from .formatting import fmt_dt_de, member_display_name, to_berlin
from .settings import get_effective_check_in_settings, get_effective_nuki_config, get_effective_smtp_config

logger = logging.getLogger(__name__)


# ── Code generation ───────────────────────────────────────────────

def _generate_secure_nuki_code(db: Database) -> str:
    """
    Generate a 6-digit code (digits 1-9 only, Nuki Keypad safe).
    Avoids trivial patterns and recent reuse within 180 days.
    """
    for _ in range(100):
        digits = [str(secrets.randbelow(9) + 1) for _ in range(6)]
        code = "".join(digits)
        if code in ("123456", "654321") or len(set(code)) == 1:
            continue
        if not db.is_code_recently_used(code):
            return code
    return "".join(str(secrets.randbelow(9) + 1) for _ in range(6))


# ── Internal provisioning helper ──────────────────────────────────

def _issue_window_code(
    *,
    db: Database,
    settings: Settings,
    window: dict[str, object],
    code: str,
    is_emergency: bool,
) -> int:
    """Create a Nuki keypad code, store it, and email the member."""
    nuki_cfg = get_effective_nuki_config(db, settings)
    effective_settings = settings.model_copy(update=nuki_cfg)
    nuki = NukiClient(effective_settings)
    smtp = get_effective_smtp_config(db, settings)
    email_service = EmailService(settings, smtp)
    check_in_cfg = get_effective_check_in_settings(db, settings)

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
        _send_code_email(
            db=db,
            settings=settings,
            email_service=email_service,
            window=window,
            code=code,
            code_id=code_id,
            check_in_enabled=bool(check_in_cfg.get("enabled")),
        )
        return code_id
    finally:
        nuki.close()


def _send_code_email(
    *,
    db: Database,
    settings: Settings,
    email_service: EmailService,
    window: dict[str, object],
    code: str,
    code_id: int,
    check_in_enabled: bool,
) -> None:
    """Send the access-code email and update DB status."""
    if not window.get("email"):
        return

    name = member_display_name(window)
    valid_from = fmt_dt_de(to_berlin(window["starts_at"], settings.timezone))
    valid_until = fmt_dt_de(to_berlin(window["ends_at"], settings.timezone))
    checks_url = build_checks_link(member_id=int(window["member_id"]), settings=settings)

    try:
        emailed = email_service.send_access_code(
            to_email=str(window["email"]),
            member_name=name,
            code=code,
            valid_from=valid_from,
            valid_until=valid_until,
            checks_url=checks_url,
            check_in_url=(
                build_check_in_link(
                    access_window_id=int(window["id"]),
                    ends_at=window["ends_at"],
                    settings=settings,
                )
                if check_in_enabled
                else None
            ),
            html_body=build_access_code_email_html(
                db, settings,
                member_name=name, code=code,
                valid_from=valid_from, valid_until=valid_until,
                checks_url=checks_url,
            ),
        )
    except Exception as exc:
        create_operational_alert(
            db=db, settings=settings,
            severity=AlertSeverity.ERROR,
            kind="access-email-failed",
            message=f"E-Mail für Fenster {window['id']} fehlgeschlagen: {exc}",
            payload={"access_window_id": int(window["id"]), "member_id": int(window["member_id"])},
        )
        return

    if emailed:
        db.mark_code_emailed(code_id)
    else:
        create_operational_alert(
            db=db, settings=settings,
            severity=AlertSeverity.WARNING,
            kind="access-email-skipped",
            message=f"E-Mail für Fenster {window['id']} übersprungen (SMTP nicht konfiguriert).",
            payload={"access_window_id": int(window["id"]), "member_id": int(window["member_id"])},
        )


# ── Batch provisioning (worker loop) ─────────────────────────────

def provision_due_codes(db: Database, settings: Settings) -> int:
    """Provision codes for all due access windows. Called by the worker."""
    nuki_cfg = get_effective_nuki_config(db, settings)
    effective_settings = settings.model_copy(update=nuki_cfg)
    nuki = NukiClient(effective_settings)
    smtp = get_effective_smtp_config(db, settings)
    email_service = EmailService(settings, smtp)
    check_in_cfg = get_effective_check_in_settings(db, settings)
    now = datetime.now(UTC)
    due = db.due_access_windows(now)
    count = 0

    try:
        for window in due:
            code = _generate_secure_nuki_code(db)
            try:
                nuki_auth_id = nuki.create_keypad_code(
                    name=f"member-{window['member_id']}-cluster-{window['booking_id']}",
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
                _send_code_email(
                    db=db, settings=settings,
                    email_service=email_service,
                    window=window, code=code, code_id=code_id,
                    check_in_enabled=bool(check_in_cfg.get("enabled")),
                )
                count += 1
            except Exception as exc:
                logger.exception("Provisioning failed for access_window=%s", window["id"])
                create_operational_alert(
                    db=db, settings=settings,
                    severity=AlertSeverity.ERROR,
                    kind="code-provisioning-failed",
                    message=f"Fenster {window['id']} fehlgeschlagen: {exc}",
                    payload={"access_window_id": window["id"]},
                )
    finally:
        nuki.close()

    db.expire_finished_windows(now)
    return count


def deprovision_expired_codes(db: Database, settings: Settings) -> int:
    """Remove expired codes from the Nuki Smartlock."""
    nuki_cfg = get_effective_nuki_config(db, settings)
    effective_settings = settings.model_copy(update=nuki_cfg)
    nuki = NukiClient(effective_settings)

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, nuki_auth_id, access_window_id
            FROM access_codes
            WHERE status = %s AND nuki_auth_id IS NOT NULL
            """,
            (AccessCodeStatus.EXPIRED,),
        )
        expired = cur.fetchall()

    count = 0
    try:
        for row in expired:
            try:
                nuki.delete_keypad_code(auth_id=int(row["nuki_auth_id"]))
                with db.connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE access_codes SET nuki_auth_id = NULL WHERE id = %s",
                        (row["id"],),
                    )
                conn.commit()
                count += 1
            except Exception as exc:
                logger.error("Failed to delete expired Nuki code %s: %s", row["nuki_auth_id"], exc)
    finally:
        nuki.close()

    return count


# ── Admin actions ─────────────────────────────────────────────────

def resend_access_code(
    *,
    db: Database,
    settings: Settings,
    access_window_id: int,
    actor_email: str,
) -> dict[str, object]:
    window = db.get_access_window_detail(access_window_id=access_window_id)
    if not window:
        raise ValueError("Zugangsfenster nicht gefunden.")
    if window["status"] not in {AccessWindowStatus.SCHEDULED, AccessWindowStatus.ACTIVE}:
        raise ValueError("Zugangsfenster ist nicht aktiv oder geplant.")

    previous_code = db.get_active_code_for_window(access_window_id=access_window_id)
    if previous_code:
        db.mark_code_replaced(code_id=int(previous_code["id"]))

    replacement = f"{secrets.randbelow(1_000_000):06d}"
    new_code_id = _issue_window_code(
        db=db, settings=settings, window=window,
        code=replacement, is_emergency=False,
    )
    if previous_code:
        db.mark_code_replaced(
            code_id=int(previous_code["id"]),
            replaced_by_code_id=new_code_id,
        )
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
        raise ValueError("Zugangsfenster nicht gefunden.")

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
        raise ValueError("Zugangsfenster nicht gefunden.")
    if window["ends_at"] < datetime.now(UTC):
        raise ValueError("Zugangsfenster bereits abgelaufen.")

    previous_code = db.get_active_code_for_window(access_window_id=access_window_id)
    if previous_code:
        db.mark_code_replaced(code_id=int(previous_code["id"]))

    emergency = f"{secrets.randbelow(1_000_000):06d}"
    new_code_id = _issue_window_code(
        db=db, settings=settings, window=window,
        code=emergency, is_emergency=True,
    )
    if previous_code:
        db.mark_code_replaced(
            code_id=int(previous_code["id"]),
            replaced_by_code_id=new_code_id,
        )
    db.create_admin_action(
        actor_email=actor_email,
        action="issue-emergency-code",
        access_window_id=access_window_id,
        access_code_id=new_code_id,
        payload={"replaced_code_id": previous_code["id"] if previous_code else None},
    )
    try:
        notify_telegram(
            db=db, settings=settings,
            text=(
                f"[Twenty4Seven-Gym] WARNING emergency-code-created\n"
                f"access_window={access_window_id}\n"
                f"actor={actor_email}\n"
                f"member_id={window['member_id']}"
            ),
        )
    except Exception:
        logger.exception("Telegram notification failed for emergency code aw=%s", access_window_id)

    return {
        "access_window_id": access_window_id,
        "code_id": new_code_id,
        "replaced_code_id": previous_code["id"] if previous_code else None,
        "is_emergency": True,
        "sent": True,
    }
