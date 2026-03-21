from __future__ import annotations

import logging
import secrets
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
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
from .notifications import EmailService, SMTPConfig, TelegramConfig, TelegramService
from .nuki_client import NukiClient

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


def generate_qr_data_uri(url: str) -> str:
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgImage)
    svg_bytes = image.to_string()
    return f"data:image/svg+xml;base64,{b64encode(svg_bytes).decode('ascii')}"


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
    email_service.send_password_reset_email(to_email=str(user["email"]), reset_url=reset_url)
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
                emailed = email_service.send_access_code(
                    to_email=str(window["email"]),
                    member_name=_member_name(window),
                    code=code,
                    valid_from=_berlin(window["starts_at"], settings.timezone).isoformat(),
                    valid_until=_berlin(window["ends_at"], settings.timezone).isoformat(),
                    check_in_url=(
                        build_check_in_link(
                            access_window_id=int(window["id"]),
                            ends_at=window["ends_at"],
                            settings=settings,
                        )
                        if check_in_settings.get("enabled")
                        else None
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
                        emailed = email_service.send_access_code(
                            to_email=window["email"],
                            member_name=member_name,
                            code=code,
                            valid_from=starts_at.isoformat(),
                            valid_until=ends_at.isoformat(),
                            check_in_url=(
                                build_check_in_link(
                                    access_window_id=int(window["id"]),
                                    ends_at=window["ends_at"],
                                    settings=settings,
                                )
                                if get_effective_check_in_settings(db, settings).get("enabled")
                                else None
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
