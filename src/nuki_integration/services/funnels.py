"""Funnel template and step CRUD operations."""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..models import FunnelStepCreateRequest, FunnelTemplateCreateRequest


def list_funnel_templates(*, db: Database) -> list[dict[str, Any]]:
    return db.list_funnel_templates()


def get_funnel_template(*, db: Database, template_id: int) -> dict[str, Any] | None:
    return db.get_funnel_template_detail(template_id=template_id)


def upsert_funnel_template_service(
    *, db: Database, payload: FunnelTemplateCreateRequest, template_id: int | None = None,
) -> dict[str, Any]:
    return db.upsert_funnel_template(
        template_id=template_id,
        name=payload.name, slug=payload.slug,
        funnel_type=payload.funnel_type, description=payload.description,
    )


def upsert_funnel_step_service(
    *, db: Database, payload: FunnelStepCreateRequest, step_id: int | None = None,
) -> dict[str, Any]:
    return db.upsert_funnel_step(
        step_id=step_id,
        template_id=payload.template_id,
        step_order=payload.step_order,
        title=payload.title, body=payload.body,
        image_path=payload.image_path,
        requires_note=payload.requires_note,
        requires_photo=payload.requires_photo,
        step_type=payload.step_type,
        is_mandatory=payload.is_mandatory,
        video_url=payload.video_url,
        house_rules_id=payload.house_rules_id,
    )


def delete_funnel_step(*, db: Database, step_id: int) -> None:
    db.delete_funnel_step(step_id=step_id)
