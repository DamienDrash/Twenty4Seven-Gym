"""Magicline sync, webhook processing, and access-window derivation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Settings
from ..db import Database
from ..enums import AccessWindowStatus, AlertSeverity
from ..magicline import (
    MagiclineClient,
    MagiclineCustomer,
    booking_effective_received_at,
    derive_entitlements,
    is_access_booking,
)
from ..nuki_client import NukiClient
from .alerts import create_operational_alert
from .settings import get_effective_nuki_config

logger = logging.getLogger(__name__)


# ── Booking clustering ────────────────────────────────────────────

def _cluster_bookings(bookings: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group adjacent/overlapping BOOKED bookings into clusters."""
    booked = sorted(
        (b for b in bookings if b["booking_status"] == "BOOKED"),
        key=lambda b: b["start_at"],
    )
    if not booked:
        return []

    clusters: list[list[dict[str, Any]]] = [[booked[0]]]
    current_end = booked[0]["end_at"]
    for booking in booked[1:]:
        if booking["start_at"] <= current_end:
            clusters[-1].append(booking)
            current_end = max(current_end, booking["end_at"])
        else:
            clusters.append([booking])
            current_end = booking["end_at"]
    return clusters


# ── Single-customer sync ─────────────────────────────────────────

def _sync_customer_payload(
    *,
    db: Database,
    settings: Settings,
    customer: MagiclineCustomer,
    bookings: list[Any],
    contracts: list[dict[str, Any]],
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
    representative_ids: list[int] = []

    nuki_cfg = get_effective_nuki_config(db, settings)
    nuki = NukiClient(settings.model_copy(update=nuki_cfg))
    try:
        for cluster in clusters:
            rep = cluster[0]
            representative_ids.append(rep["id"])
            dispatch_at = max(rep["start_at"] - timedelta(minutes=15), now_utc)
            ends_at = cluster[-1]["end_at"] + timedelta(minutes=30)

            window_id = db.upsert_access_window(
                member_id=member_id,
                booking_id=rep["id"],
                booking_ids=[b["id"] for b in cluster],
                booking_count=len(cluster),
                starts_at=rep["start_at"] - timedelta(minutes=15),
                ends_at=ends_at,
                dispatch_at=dispatch_at,
                status=AccessWindowStatus.SCHEDULED,
                access_reason="freies-training-booking-cluster",
            )
            _sync_nuki_validity(
                db=db, settings=settings, nuki=nuki,
                window_id=window_id, member_id=member_id,
                rep=rep, ends_at=ends_at,
            )
        db.prune_member_windows(member_id=member_id, keep_booking_ids=representative_ids)
    finally:
        nuki.close()

    return {"member_id": member_id, "bookings": synced_bookings, "windows": len(clusters)}


def _sync_nuki_validity(
    *,
    db: Database,
    settings: Settings,
    nuki: NukiClient,
    window_id: int,
    member_id: int,
    rep: dict[str, Any],
    ends_at: datetime,
) -> None:
    """If an active code exists and its expiry changed, update Nuki."""
    active_code = db.get_active_code_for_window(access_window_id=window_id)
    if not active_code or active_code.get("expires_at") == ends_at:
        return
    try:
        if active_code.get("nuki_auth_id") is not None:
            nuki.update_keypad_code(
                auth_id=int(active_code["nuki_auth_id"]),
                name=f"member-{member_id}-cluster-{rep['id']}",
                allowed_from=(rep["start_at"] - timedelta(minutes=15)).isoformat(),
                allowed_until=ends_at.isoformat(),
            )
        db.sync_window_code_expiry(access_window_id=window_id, expires_at=ends_at)
    except Exception as exc:
        logger.exception("Nuki validity update failed for aw=%s", window_id)
        create_operational_alert(
            db=db, settings=settings,
            severity=AlertSeverity.ERROR,
            kind="nuki-code-update-failed",
            message=f"Code-Update für Fenster {window_id} fehlgeschlagen: {exc}",
            payload={"access_window_id": window_id, "member_id": member_id},
        )


# ── Full sync ─────────────────────────────────────────────────────

def sync_magicline_bookings(db: Database, settings: Settings) -> dict[str, int]:
    client = MagiclineClient(settings)
    synced_members = synced_bookings = scheduled_windows = 0
    try:
        for customer, bookings, contracts in client.sync_candidates():
            synced_members += 1
            result = _sync_customer_payload(
                db=db, settings=settings,
                customer=customer, bookings=bookings, contracts=contracts,
            )
            synced_bookings += result["bookings"]
            scheduled_windows += result["windows"]
    finally:
        client.close()
    return {"members": synced_members, "bookings": synced_bookings, "windows": scheduled_windows}


def sync_magicline_member_by_email(
    db: Database, settings: Settings, email: str,
) -> dict[str, int | str]:
    client = MagiclineClient(settings)
    try:
        customer = client.search_customer_by_email(email)
        if customer is None:
            return {"email": email, "members": 0, "bookings": 0, "windows": 0}
        contracts = client.list_customer_contracts(customer.id)
        bookings = [
            b for b in client.list_customer_bookings(customer.id)
            if is_access_booking(b, settings)
        ]
        result = _sync_customer_payload(
            db=db, settings=settings,
            customer=customer, bookings=bookings, contracts=contracts,
        )
        return {"email": email, "members": 1, **result}
    finally:
        client.close()


def list_magicline_bookables(settings: Settings) -> list[dict[str, str | int | None]]:
    client = MagiclineClient(settings)
    try:
        return [
            {"id": i.get("id"), "title": i.get("title"),
             "category": i.get("category"), "duration": i.get("duration")}
            for i in client.list_bookable_appointments()
        ]
    finally:
        client.close()


def inspect_magicline_member_by_email(settings: Settings, email: str) -> dict[str, object]:
    client = MagiclineClient(settings)
    try:
        customer = client.search_customer_by_email(email)
        if customer is None:
            return {"email": email, "found": False}

        contracts = client.list_customer_contracts(customer.id)
        all_bookings = client.list_customer_bookings(customer.id)
        relevant = [b for b in all_bookings if is_access_booking(b, settings)]
        clusters = _cluster_bookings([
            {"id": b.booking_id, "start_at": b.start_date_time,
             "end_at": b.end_date_time, "booking_status": b.booking_status}
            for b in relevant
        ])
        return {
            "email": email, "found": True,
            "customer": customer.model_dump(mode="json"),
            "entitlements": derive_entitlements(contracts, settings),
            "contract_rate_names": [
                c.get("rateName") for c in contracts
                if c.get("contractStatus") == "ACTIVE" and c.get("rateName")
            ],
            "relevant_booking_count": len(relevant),
            "relevant_booking_cluster_count": len(clusters),
            "relevant_bookings": [b.model_dump(mode="json") for b in relevant],
        }
    finally:
        client.close()


# ── Webhook processing ────────────────────────────────────────────

def should_process_magicline_webhook(
    payload: dict[str, object], settings: Settings,
) -> bool:
    event_type = str(
        payload.get("eventType") or payload.get("event_type")
        or payload.get("type") or ""
    ).upper()
    if "APPOINTMENT" not in event_type and "BOOKING" not in event_type:
        return False

    for key in ("title", "appointment", "booking"):
        candidate = payload.get(key)
        if isinstance(candidate, str):
            return candidate == settings.magicline_relevant_appointment_title
        if isinstance(candidate, dict):
            title = candidate.get("title")
            if isinstance(title, str):
                return title == settings.magicline_relevant_appointment_title
    return True


def process_magicline_webhook(
    db: Database, settings: Settings, payload: dict[str, object],
) -> dict[str, int | str | bool]:
    payload = _normalize_webhook_payload(payload)

    event_id = str(
        payload.get("eventId") or payload.get("id")
        or payload.get("event_id") or payload.get("uuid") or ""
    ).strip()
    if not event_id:
        raise ValueError("Webhook payload does not contain a stable event id.")

    event_type = str(payload.get("eventType") or payload.get("event_type") or "")
    created = db.record_webhook_event(
        provider="magicline", event_id=event_id,
        event_type=event_type or None, payload=payload,
    )
    if not created:
        return {"event_id": event_id, "event_type": event_type, "duplicate": True}

    if not should_process_magicline_webhook(payload, settings):
        return {"event_id": event_id, "event_type": event_type, "duplicate": False, "processed": False}

    sync_result = sync_magicline_bookings(db, settings)
    return {"event_id": event_id, "event_type": event_type, "duplicate": False, "processed": True, **sync_result}


def _normalize_webhook_payload(payload: dict[str, object]) -> dict[str, object]:
    """Flatten nested Magicline webhook structures into a single dict."""
    items = payload.get("payload")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            out = dict(payload)
            out.setdefault("eventType", first.get("type"))
            out.setdefault("eventId", payload.get("uuid"))
            content = first.get("content")
            if isinstance(content, dict):
                out.update(content)
            return out
    elif isinstance(items, dict):
        out = dict(payload)
        out.setdefault("eventType", items.get("type"))
        out.setdefault("eventId", payload.get("uuid"))
        content = items.get("content")
        if isinstance(content, dict):
            out.update(content)
        return out
    return payload
