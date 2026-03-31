"""Legacy /check-in flow (kept for backward compat with old email links)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..db import Database
from .auth_tokens import decode_check_in_token, issue_check_in_token
from .settings import get_effective_check_in_settings


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
            email=email, raw_code=code, now=datetime.now(UTC),
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
    normalized: list[dict[str, Any]] = []
    for item in checklist:
        item_id = str(item.get("id") or "")
        if item_id not in expected_items:
            continue
        normalized.append({"id": item_id, "label": expected_items[item_id], "checked": bool(item.get("checked"))})

    missing = [k for k in expected_items if k not in {r["id"] for r in normalized}]
    if missing:
        raise ValueError("Checkliste ist unvollständig.")
    if not all(bool(item["checked"]) for item in normalized):
        raise ValueError("Alle Checklistenpunkte müssen bestätigt werden.")

    record = db.upsert_access_window_checkin(
        access_window_id=access_window_id,
        member_id=int(window["member_id"]),
        source=source, rules_accepted=True, checklist=normalized,
    )
    return {"confirmed": True, "success_message": config["success_message"], "check_in": record}
