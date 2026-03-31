"""Member-facing /checks flow: session resolution and funnel submission."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Settings
from ..db import Database
from .auth_tokens import decode_checks_token, issue_checks_token


def resolve_checks_session(
    *,
    db: Database,
    settings: Settings,
    token: str | None = None,
    email: str | None = None,
    code: str | None = None,
) -> dict[str, object]:
    """Authenticate a member and return their upcoming windows."""
    if token:
        member_id = decode_checks_token(token=token, settings=settings)
        member = db.get_member_by_id(member_id=member_id)
        if not member:
            raise ValueError("Mitglied nicht gefunden.")
    else:
        if not email or not code:
            raise ValueError("E-Mail und Code sind erforderlich.")
        verified = db.verify_member_access_code(
            email=email, raw_code=code.strip(), now=datetime.now(UTC),
        )
        if not verified:
            raise ValueError("Code ungültig oder kein aktives Zugangsfenster gefunden.")
        member_id = int(verified["member_id"])
        member = db.get_member_by_id(member_id=member_id)
        if not member:
            raise ValueError("Mitglied nicht gefunden.")

    has_checkin = db.get_funnel_by_type("checkin") is not None
    has_checkout = db.get_funnel_by_type("checkout") is not None

    windows = db.list_member_windows_with_status(
        member_id=member_id,
        from_dt=datetime.now(UTC) - timedelta(hours=1),
    )

    name = (
        " ".join(str(p) for p in [member.get("first_name"), member.get("last_name")] if p).strip()
        or str(member.get("email") or "Member")
    )
    return {
        "token": issue_checks_token(member_id=member_id, settings=settings),
        "member_name": name,
        "member_email": str(member.get("email") or ""),
        "windows": [
            {**w, "has_checkin_funnel": has_checkin, "has_checkout_funnel": has_checkout}
            for w in windows
        ],
    }


def get_active_funnel_for_type(
    *, db: Database, funnel_type: str,
) -> dict[str, Any] | None:
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
    """Validate and persist a checkin or checkout funnel submission."""
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

    # Validate required notes
    for step in funnel.get("steps") or []:
        if not step.get("requires_note"):
            continue
        step_id = int(step["id"])
        answer = next((d for d in steps_data if int(d.get("step_id", 0)) == step_id), None)
        note = (answer.get("note") or "").strip() if answer else ""
        if not note:
            raise ValueError(f"Schritt '{step['title']}' erfordert eine Notiz.")

    submission = db.create_funnel_submission(
        access_window_id=window_id,
        template_id=int(funnel["id"]),
        entry_source=f"checks-{funnel_type}",
        success=True,
    )
    for sd in steps_data:
        sid = int(sd.get("step_id", 0))
        if sid not in step_map:
            continue
        db.create_funnel_step_event(
            submission_id=int(submission["id"]),
            step_id=sid, status="completed",
            note=sd.get("note") or None, photo_path=None,
        )

    checklist_payload = [
        {"step_id": d.get("step_id"), "checked": d.get("checked", False), "note": d.get("note", "")}
        for d in steps_data
    ]

    if funnel_type == "checkin":
        record = db.upsert_access_window_checkin(
            access_window_id=window_id, member_id=member_id,
            source="checks-funnel", rules_accepted=True, checklist=checklist_payload,
        )
    else:
        record = db.upsert_window_checkout(
            access_window_id=window_id, member_id=member_id,
            source="checks-funnel", checklist=checklist_payload,
        )

    return {
        "submitted": True,
        "funnel_type": funnel_type,
        "window_id": window_id,
        "confirmed_at": record.get("confirmed_at"),
    }
