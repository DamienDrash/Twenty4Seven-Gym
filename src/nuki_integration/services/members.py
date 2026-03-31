"""Member detail aggregation for admin views."""

from __future__ import annotations

from typing import Any

from ..db import Database


def get_member_detail(*, db: Database, member_id: int) -> dict[str, Any] | None:
    member = db.get_member_by_id(member_id=member_id)
    if not member:
        return None
    return {
        "member": member,
        "bookings": db.list_member_bookings(member_id=member_id),
        "access_windows": db.list_member_access_windows(member_id=member_id),
        "access_codes": db.list_member_access_codes(member_id=member_id),
    }
