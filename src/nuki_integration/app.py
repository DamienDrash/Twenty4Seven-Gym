from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import decode_token, issue_token, verify_password
from .config import Settings, get_settings
from .db import Database
from .dependencies import get_current_user, get_database, get_runtime_settings, require_admin
from .models import (
    AdminActionRecord, AlertRecord, BrandingSettingsResponse, BrandingSettingsUpdateRequest,
    NpsResponseRecord, NpsStatsResponse,
    CheckInSettingsResponse, CheckInSettingsUpdateRequest, ChecksFunnelResponse,
    ChecksResolveRequest, ChecksSessionResponse, ChecksSubmitRequest,
    CompletePasswordResetRequest, EmailTemplateResponse, EmailTemplateUpdateRequest,
    ForgotPasswordRequest, FunnelStepCreateRequest, FunnelTemplateCreateRequest,
    FunnelTemplateResponse, LoginRequest, LoginResponse, MagiclineSettingsResponse,
    MagiclineSettingsUpdateRequest, MemberDetail, MemberSummary, NukiSettingsResponse,
    NukiSettingsUpdateRequest, PasswordResetRequest, PublicCheckInResolveRequest,
    PublicCheckInSessionResponse, PublicCheckInSubmitRequest, SMTPSettingsResponse,
    SMTPSettingsUpdateRequest, TelegramSettingsResponse, TelegramSettingsUpdateRequest,
    TelegramTestRequest, UserCreateRequest, UserRecord, UserSummary, UserUpdateRequest,
    HouseRulesResponse, HouseRulesCreateRequest, EmailTemplateVersionResponse, AccessWindowSummary,
    FunnelTemplateDetail,
)
from .notifications import EmailService, TelegramService
from .nuki_client import NukiClient
from .services import (
    build_access_code_email_html, build_password_reset_email_html, build_test_email_html,
    complete_password_reset, deactivate_access_window, delete_funnel_step,
    delete_funnel_template, deprovision_expired_codes, get_branding_settings,
    get_effective_check_in_settings, get_effective_magicline_config,
    get_effective_nuki_config, get_effective_smtp_config, get_effective_telegram_config,
    get_email_template, get_funnel_template, get_media_url, get_member_detail,
    get_active_funnel_for_type, inspect_magicline_member_by_email, issue_emergency_access_code,
    list_funnel_templates, list_magicline_bookables, process_magicline_webhook,
    provision_due_codes, request_password_reset, resend_access_code,
    resolve_checks_session, submit_checks_funnel,
    sync_magicline_bookings, sync_magicline_member_by_email,
    upsert_funnel_template_service, upsert_funnel_step_service,
    list_house_rules_versions, create_house_rules_version,
    list_template_versions, restore_template_version, get_active_house_rules,
)

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db = get_database()
    db.ensure_schema()
    db.ensure_schema_v2()
    db.bootstrap_admin(settings.bootstrap_admin_email, settings.bootstrap_admin_password)
    get_email_template(db)
    logger.info("Access platform starting")
    yield
    logger.info("Access platform shutting down")
    db.close()


app = FastAPI(
    title="Twenty4Seven-Gym",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

# ── SPA Shell routes ─────────────────────────────────────────────

@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
@app.get("/nps", include_in_schema=False)
@app.get("/nps/", include_in_schema=False)
@app.get("/reset-password", include_in_schema=False)
@app.get("/reset-password/", include_in_schema=False)
@app.get("/check-in", include_in_schema=False)
@app.get("/check-in/", include_in_schema=False)
@app.get("/checks", include_in_schema=False)
@app.get("/checks/", include_in_schema=False)
def frontend_shell() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "twenty4seven-gym", "status": "ok", "docs": "/docs"}


# ── Health ────────────────────────────────────────────────────────

@app.get("/healthz/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/healthz/ready")
def readiness(db: Database = Depends(get_database)) -> dict[str, str]:
    if not db.health_check():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable")
    return {"status": "ready"}


# ── Auth ──────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> LoginResponse:
    user = db.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
    return LoginResponse(access_token=issue_token(subject=user["email"], role=user["role"], secret=rs.jwt_secret, ttl_seconds=30*24*3600), role=user["role"])


@app.get("/me", response_model=UserRecord)
def me(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    return current_user


@app.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, bool]:
    request_password_reset(db=db, settings=rs, email=payload.email)
    return {"sent": True}


@app.post("/auth/reset-password")
def reset_password(payload: CompletePasswordResetRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, bool]:
    complete_password_reset(db=db, settings=rs, token=payload.token, new_password=payload.password)
    return {"reset": True}


# ── Admin: members ───────────────────────────────────────────────

@app.get("/admin/members", response_model=list[MemberSummary])
def admin_members(u: UserRecord = Depends(get_current_user), email: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0), db: Database = Depends(get_database)) -> list[MemberSummary]:
    return [MemberSummary.model_validate(i) for i in db.list_members(email_filter=email, limit=limit, offset=offset)]


@app.get("/admin/members/{member_id}", response_model=MemberDetail)
def admin_member_detail(member_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database)) -> MemberDetail:
    r = get_member_detail(db=db, member_id=member_id)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")
    return MemberDetail.model_validate(r)


# ── Admin: access windows ────────────────────────────────────────

@app.get("/admin/access-windows", response_model=list[AccessWindowSummary])
def admin_access_windows(u: UserRecord = Depends(get_current_user), status_filter: str | None = Query(default=None, alias="status"),
    member_id: int | None = Query(default=None),
    include_historical: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0), db: Database = Depends(get_database)) -> list[AccessWindowSummary]:
    return [AccessWindowSummary.model_validate(i) for i in db.list_access_windows(status_filter=status_filter, member_id=member_id, include_historical=include_historical, limit=limit, offset=offset)]


@app.post("/admin/access-windows/{aw_id}/resend")
def admin_aw_resend(aw_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    try:
        return resend_access_code(db=db, settings=rs, access_window_id=aw_id, actor_email=u.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/access-windows/{aw_id}/deactivate")
def admin_aw_deactivate(aw_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    try:
        return deactivate_access_window(db=db, settings=rs, access_window_id=aw_id, actor_email=u.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/access-windows/{aw_id}/emergency-code")
def admin_aw_emergency(aw_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    try:
        return issue_emergency_access_code(db=db, settings=rs, access_window_id=aw_id, actor_email=u.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── Admin: lock control ──────────────────────────────────────────

@app.get("/admin/lock/status")
def admin_lock_status(u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    nuki = NukiClient(rs.model_copy(update=get_effective_nuki_config(db, rs)))
    try:
        return nuki.get_lock_status()
    finally:
        nuki.close()


@app.post("/admin/lock/sync")
def admin_lock_sync(admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    nuki = NukiClient(rs.model_copy(update=get_effective_nuki_config(db, rs)))
    try:
        nuki.force_sync()
        return {"success": True}
    finally:
        nuki.close()


@app.post("/admin/remote-open")
def admin_remote_open(u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    from .services.access import remote_open_service
    return remote_open_service(db=db, settings=rs, actor_email=u.email)


# ── Admin: operational logs ──────────────────────────────────────

@app.get("/admin/alerts", response_model=list[AlertRecord])
def admin_alerts(u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database)) -> list[AlertRecord]:
    return [AlertRecord.model_validate(i) for i in db.list_alerts()]


@app.get("/admin/house-rules", response_model=HouseRulesResponse)
def admin_get_house_rules(
    _current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
) -> HouseRulesResponse:
    rules = get_active_house_rules(db=db)
    if not rules:
        raise HTTPException(status_code=404, detail="No active house rules found")
    return HouseRulesResponse.model_validate(rules)


@app.post("/admin/house-rules", response_model=HouseRulesResponse)
def admin_create_house_rules(
    payload: HouseRulesCreateRequest,
    current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
) -> HouseRulesResponse:
    rules = create_house_rules_version(
        db=db,
        title=payload.title,
        body_text=payload.body_text,
        body_html=payload.body_html,
        created_by=str(current_user.email),
    )
    return HouseRulesResponse.model_validate(rules)


@app.get("/admin/house-rules/versions", response_model=list[HouseRulesResponse])
def admin_get_house_rules_versions(
    _current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
) -> list[HouseRulesResponse]:
    return [HouseRulesResponse.model_validate(r) for r in list_house_rules_versions(db=db)]


@app.get("/admin/system/email-template/versions", response_model=list[EmailTemplateVersionResponse])
def admin_get_email_template_versions(
    template_type: str,
    _current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
) -> list[EmailTemplateVersionResponse]:
    return [EmailTemplateVersionResponse.model_validate(v) for v in list_template_versions(db=db, template_type=template_type)]


@app.post("/admin/system/email-template/restore/{version_id}")
def admin_restore_email_template_version(
    version_id: int,
    _current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
) -> dict[str, object]:
    restore_template_version(db=db, version_id=version_id)
    return {"restored": True}


@app.get("/admin/admin-actions", response_model=list[AdminActionRecord])
def admin_actions(
    u: UserRecord = Depends(get_current_user),
    limit: int = Query(default=100),
    offset: int = Query(default=0),
    db: Database = Depends(get_database)
) -> list[AdminActionRecord]:
    return [AdminActionRecord.model_validate(i) for i in db.list_admin_actions(limit=limit, offset=offset)]


# ── Admin: system settings ───────────────────────────────────────

@app.get("/admin/system/email-settings", response_model=SMTPSettingsResponse)
def admin_get_smtp(u: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> SMTPSettingsResponse:
    cfg = get_effective_smtp_config(db, rs)
    return SMTPSettingsResponse(smtp_host=cfg.host, smtp_port=cfg.port, smtp_username=cfg.username, smtp_use_tls=cfg.use_tls, smtp_from_email=cfg.from_email, has_password=bool(cfg.password))


@app.put("/admin/system/email-settings")
def admin_put_smtp(payload: SMTPSettingsUpdateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    db.upsert_setting("smtp", payload.model_dump(mode="json"))
    return {"updated": True}


@app.post("/admin/system/email-test-code")
def admin_test_email(payload: EmailTestRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, bool]:
    from .services.access import _send_code_email
    _send_code_email(db=db, settings=rs, to_email=payload.email, code="123456", starts_at=lifespan, ends_at=lifespan, member_name="Admin Test")
    return {"sent": True}


@app.get("/admin/system/email-template", response_model=EmailTemplateResponse)
def admin_get_email_tpl(u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> EmailTemplateResponse:
    return EmailTemplateResponse.model_validate(get_email_template(db))


@app.put("/admin/system/email-template")
def admin_put_email_tpl(payload: EmailTemplateUpdateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    db.upsert_setting("email_template", payload.model_dump(mode="json"))
    return {"updated": True}


@app.get("/admin/system/telegram-settings", response_model=TelegramSettingsResponse)
def admin_get_tg(u: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> TelegramSettingsResponse:
    cfg = get_effective_telegram_config(db, rs)
    return TelegramSettingsResponse(telegram_chat_id=cfg.chat_id, has_bot_token=bool(cfg.bot_token))


@app.put("/admin/system/telegram-settings")
def admin_put_tg(payload: TelegramSettingsUpdateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    db.upsert_setting("telegram", payload.model_dump(mode="json"))
    return {"updated": True}


@app.get("/admin/system/nuki-settings", response_model=NukiSettingsResponse)
def admin_get_nuki(u: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> NukiSettingsResponse:
    cfg = get_effective_nuki_config(db, rs)
    return NukiSettingsResponse(nuki_smartlock_id=cfg["nuki_smartlock_id"], nuki_dry_run=cfg["nuki_dry_run"], has_api_token=bool(cfg.get("nuki_api_token")))


@app.put("/admin/system/nuki-settings")
def admin_put_nuki(payload: NukiSettingsUpdateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    db.upsert_setting("nuki", payload.model_dump(mode="json"))
    return {"updated": True}


@app.get("/admin/system/magicline-settings", response_model=MagiclineSettingsResponse)
def admin_get_ml(u: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> MagiclineSettingsResponse:
    cfg = get_effective_magicline_config(db, rs)
    return MagiclineSettingsResponse(magicline_base_url=cfg["magicline_base_url"], magicline_studio_id=cfg["magicline_studio_id"], magicline_studio_name=cfg["magicline_studio_name"], magicline_relevant_appointment_title=cfg["magicline_relevant_appointment_title"], has_api_key=bool(cfg.get("magicline_api_key")), has_webhook_key=bool(cfg.get("magicline_webhook_api_key")))


@app.put("/admin/system/magicline-settings")
def admin_put_ml(payload: MagiclineSettingsUpdateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    db.upsert_setting("magicline", payload.model_dump(mode="json"))
    return {"updated": True}


@app.get("/admin/system/branding", response_model=BrandingSettingsResponse)
def admin_get_branding(u: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> BrandingSettingsResponse:
    return BrandingSettingsResponse.model_validate(get_branding_settings(db))


@app.put("/admin/system/branding")
def admin_put_branding(payload: BrandingSettingsUpdateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    db.upsert_setting("branding", payload.model_dump(mode="json"))
    return {"updated": True}


# ── Admin: funnels ───────────────────────────────────────────────

@app.get("/admin/funnels", response_model=list[FunnelTemplateResponse])
def admin_funnels(u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> list[FunnelTemplateResponse]:
    return [FunnelTemplateResponse.model_validate(i) for i in list_funnel_templates(db=db)]


@app.post("/admin/funnels", response_model=FunnelTemplateResponse)
def admin_create_funnel(payload: FunnelTemplateCreateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> FunnelTemplateResponse:
    r = upsert_funnel_template_service(db=db, payload=payload)
    return FunnelTemplateResponse(id=int(r["id"]), name=str(r["name"]), slug=str(r["slug"]), funnel_type=str(r["funnel_type"]), description=r.get("description"))


@app.delete("/admin/funnels/{tid}")
def admin_delete_funnel(tid: int, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    delete_funnel_template(db=db, template_id=tid)
    return {"deleted": True}


@app.get("/admin/funnels/{tid}", response_model=FunnelTemplateDetail)
def admin_funnel_detail(tid: int, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> FunnelTemplateDetail:
    r = get_funnel_template(db=db, template_id=tid)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funnel not found.")
    return FunnelTemplateDetail.model_validate({"template": r, "steps": r.get("steps", [])})


@app.post("/admin/funnels/{tid}/steps")
def admin_funnel_step_create(tid: int, payload: FunnelStepCreateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, int]:
    payload.template_id = tid
    r = upsert_funnel_step_service(db=db, payload=payload)
    return {"id": int(r["id"])}


@app.put("/admin/funnels/{tid}/steps/{sid}")
def admin_funnel_step_update(tid: int, sid: int, payload: FunnelStepCreateRequest, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    payload.template_id = tid
    upsert_funnel_step_service(db=db, payload=payload, step_id=sid)
    return {"updated": True}


@app.delete("/admin/funnels/{tid}/steps/{sid}")
def admin_funnel_step_delete(tid: int, sid: int, u: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    delete_funnel_step(db=db, step_id=sid)
    return {"deleted": True}


# ── Admin: sync & webhooks ───────────────────────────────────────

@app.post("/admin/sync")
def admin_trigger_sync(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    return sync_magicline_bookings(db, rs)


@app.post("/admin/sync/member")
def admin_trigger_member_sync(email: str = Query(...), _: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    return sync_magicline_member_by_email(db, rs, email)


@app.post("/webhooks/magicline")
@app.post("/webhook/magicline")
def magicline_webhook(payload: MagiclineWebhookEnvelope = Body(...), x_api_key: Annotated[str | None, Header(alias="X-API-KEY")] = None, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, int | str | bool]:
    if not rs.magicline_webhook_api_key or x_api_key != rs.magicline_webhook_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return process_magicline_webhook(db, rs, payload.model_dump(mode="json"))


# ── Public /checks ────────────────────────────────────────────────

@app.post("/public/checks/resolve", response_model=ChecksSessionResponse)
def public_checks_resolve(payload: ChecksResolveRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> ChecksSessionResponse:
    try:
        return ChecksSessionResponse.model_validate(resolve_checks_session(db=db, settings=rs, email=str(payload.email), code=payload.code.strip()))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc



@app.get("/public/checks/by-key", response_model=ChecksSessionResponse)
def checks_resolve_by_key(
    key: str = Query(...),
    db: Database = Depends(get_database),
    rs: Settings = Depends(get_runtime_settings),
) -> ChecksSessionResponse:
    try:
        session = resolve_checks_session(db=db, settings=rs, key=key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return ChecksSessionResponse.model_validate(session)
@app.get("/public/checks/session", response_model=ChecksSessionResponse)
def public_checks_session(token: str = Query(...), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> ChecksSessionResponse:
    try:
        return ChecksSessionResponse.model_validate(resolve_checks_session(db=db, settings=rs, token=token))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/public/checks/funnel/{funnel_type}", response_model=ChecksFunnelResponse)
def public_checks_funnel_get(funnel_type: str, db: Database = Depends(get_database)) -> ChecksFunnelResponse:
    funnel = get_active_funnel_for_type(db=db, funnel_type=funnel_type)
    if not funnel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Kein aktiver {funnel_type}-Funnel.")
    return ChecksFunnelResponse.model_validate(funnel)


@app.post("/public/checks/submit")
def public_checks_submit(payload: ChecksSubmitRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    try:
        return submit_checks_funnel(db=db, settings=rs, token=payload.token, window_id=payload.window_id, funnel_type=payload.funnel_type, steps_data=[s.model_dump() for s in payload.steps])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/admin/magicline/bookables")
def admin_ml_bookables(u: UserRecord = Depends(get_current_user), rs: Settings = Depends(get_runtime_settings)) -> list[dict[str, str | int | None]]:
    return list_magicline_bookables(rs)


@app.get("/admin/magicline/member-debug")
def admin_ml_debug(email: str = Query(...), u: UserRecord = Depends(get_current_user), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    return inspect_magicline_member_by_email(rs, email)


# ── Health & Routing ──────────────────────────────────────────────

@app.get("/healthz/ready")
def readiness(db: Database = Depends(get_database)) -> dict[str, str]:
    if not db.health_check():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable")
    return {"status": "ready"}


@app.get("/admin/nps/stats", response_model=NpsStatsResponse)
def admin_nps_stats(u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database)) -> NpsStatsResponse:
    return NpsStatsResponse.model_validate(db.get_nps_stats())

@app.get("/admin/nps/responses", response_model=list[NpsResponseRecord])
def admin_nps_responses(
    u: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[NpsResponseRecord]:
    return [NpsResponseRecord.model_validate(r) for r in db.list_nps_responses(limit=limit, offset=offset)]


@app.get("/admin/checks-log")
def admin_checks_log(
    u: UserRecord = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
) -> list[dict]:
    return db.list_checks_submissions(limit=limit, offset=offset)
@app.get("/{path:path}", include_in_schema=False)
def catch_all(path: str) -> object:
    # 1. List of prefixes that are definitely API or system routes
    api_prefixes = {"admin", "auth", "public", "healthz", "me", "webhook", "webhooks", "api"}
    first_segment = path.split("/")[0].lower()
    
    # 2. If it's a known API prefix or has a file extension, do NOT serve SPA shell
    if first_segment in api_prefixes or "." in path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        
    # 3. For these explicit frontend routes, serve the SPA shell
    # This is safer than a true catch-all
    frontend_routes = {"", "app", "checks", "reset-password", "check-in"}
    if first_segment in frontend_routes:
        return FileResponse(STATIC_DIR / "index.html")
        
    # 4. Fallback: 404 for anything else
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
