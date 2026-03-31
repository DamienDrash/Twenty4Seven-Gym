from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import decode_token, issue_token, verify_password
from .config import Settings, get_settings
from .db import Database
from .dependencies import get_database, get_runtime_settings
from .enums import UserRole
from .exceptions import AuthenticationError
from .logging_setup import configure_logging
from .models import (
    AccessWindowSummary, AdminActionRecord, AlertRecord,
    BrandingSettingsResponse, BrandingSettingsUpdateRequest,
    CheckInSettingsResponse, CheckInSettingsUpdateRequest,
    ChecksFunnelResponse, ChecksResolveRequest, ChecksSessionResponse,
    ChecksSubmitRequest, CompletePasswordResetRequest,
    EmailTemplateResponse, EmailTemplateUpdateRequest, EmailTestRequest,
    ForgotPasswordRequest, FunnelStep, FunnelStepCreateRequest,
    FunnelTemplateCreateRequest, FunnelTemplateDetail, FunnelTemplateResponse,
    LoginRequest, LoginResponse,
    MagiclineSettingsResponse, MagiclineSettingsUpdateRequest,
    MagiclineWebhookEnvelope, MemberDetail, MemberSummary,
    NukiSettingsResponse, NukiSettingsUpdateRequest,
    PasswordResetRequest, PublicCheckInResolveRequest,
    PublicCheckInSessionResponse, PublicCheckInSubmitRequest,
    SMTPSettingsResponse, SMTPSettingsUpdateRequest,
    TelegramSettingsResponse, TelegramSettingsUpdateRequest, TelegramTestRequest,
    UserCreateRequest, UserRecord, UserSummary, UserUpdateRequest,
)
from .notifications import EmailService, TelegramService
from .nuki_client import NukiClient
from .services import (
    build_access_code_email_html, build_password_reset_email_html, build_test_email_html,
    complete_password_reset, deactivate_access_window, delete_funnel_step,
    generate_qr_data_uri, generate_qr_png_bytes,
    get_active_funnel_for_type, get_branding_settings,
    get_effective_check_in_settings, get_effective_magicline_config,
    get_effective_nuki_config, get_effective_smtp_config, get_effective_telegram_config,
    get_email_template, get_funnel_template, get_media_url, get_member_detail,
    inspect_magicline_member_by_email, issue_emergency_access_code,
    list_funnel_templates, list_magicline_bookables,
    process_magicline_webhook, provision_due_codes,
    request_password_reset, resend_access_code,
    resolve_checks_session, resolve_public_check_in,
    save_media_file, submit_checks_funnel, submit_public_check_in,
    sync_magicline_bookings, sync_magicline_member_by_email,
    upsert_funnel_step_service, upsert_funnel_template_service,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")
    return authorization.split(" ", 1)[1]


def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> UserRecord:
    token = _require_bearer(authorization)
    try:
        payload = decode_token(token, runtime_settings.jwt_secret)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = db.get_user_by_email(payload["sub"])
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown or inactive user.")
    return UserRecord.model_validate(user)


def require_admin(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required.")
    return current_user


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db = get_database()
    db.ensure_schema()
    db.bootstrap_admin(settings.bootstrap_admin_email, settings.bootstrap_admin_password)
    get_email_template(db)
    logger.info("Access platform starting")
    yield
    logger.info("Access platform shutting down")
    db.close()


app = FastAPI(title="Studio Access Platform", version="2.0.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

media_path = Path(settings.media_storage_path).resolve()
media_path.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_path), name="media")


# ── Shell routes ──────────────────────────────────────────────────

@app.get("/")
def root() -> dict[str, str]:
    return {"service": "twenty4seven-gym", "status": "ok", "docs": "/docs"}


@app.get("/app", include_in_schema=False)
@app.get("/reset-password", include_in_schema=False)
@app.get("/check-in", include_in_schema=False)
@app.get("/checks", include_in_schema=False)
def frontend_shell() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


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
    return LoginResponse(access_token=issue_token(subject=user["email"], role=user["role"], secret=rs.jwt_secret), role=user["role"])


@app.get("/me", response_model=UserRecord)
def me(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    return current_user


@app.post("/auth/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, bool]:
    return request_password_reset(db=db, settings=rs, email=str(payload.email))


@app.post("/auth/reset-password")
def reset_password(payload: CompletePasswordResetRequest, db: Database = Depends(get_database)) -> dict[str, bool]:
    try:
        return complete_password_reset(db=db, token=payload.token, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── Webhook ───────────────────────────────────────────────────────

def _require_webhook_key(rs: Settings, key: str | None) -> None:
    expected = rs.magicline_webhook_api_key.strip()
    if not expected:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook key not configured.")
    if not key or key.strip() != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook API key.")


@app.post("/webhooks/magicline")
@app.post("/webhook/magicline")
def magicline_webhook(
    payload: MagiclineWebhookEnvelope = Body(...),
    x_api_key: Annotated[str | None, Header(alias="X-API-KEY")] = None,
    db: Database = Depends(get_database),
    rs: Settings = Depends(get_runtime_settings),
) -> dict[str, int | str | bool]:
    _require_webhook_key(rs, x_api_key)
    return process_magicline_webhook(db, rs, payload.model_dump(mode="json"))


# ── Public /checks ────────────────────────────────────────────────

@app.post("/public/checks/resolve", response_model=ChecksSessionResponse)
def public_checks_resolve(payload: ChecksResolveRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> ChecksSessionResponse:
    try:
        return ChecksSessionResponse.model_validate(resolve_checks_session(db=db, settings=rs, email=str(payload.email), code=payload.code.strip()))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
    return ChecksFunnelResponse(template_id=int(funnel["id"]), template_name=str(funnel["name"]),
        funnel_type=str(funnel["funnel_type"]), description=funnel.get("description"), steps=funnel.get("steps") or [])


@app.post("/public/checks/window/{window_id}/checkin")
def public_checks_checkin(
    window_id: int, payload: ChecksSubmitRequest,
    db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    try:
        return submit_checks_funnel(db=db, settings=rs, token=payload.token, window_id=window_id,
            funnel_type="checkin", steps_data=[s.model_dump() for s in payload.steps])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/public/checks/window/{window_id}/checkout")
def public_checks_checkout(
    window_id: int, payload: ChecksSubmitRequest,
    db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    try:
        return submit_checks_funnel(db=db, settings=rs, token=payload.token, window_id=window_id,
            funnel_type="checkout", steps_data=[s.model_dump() for s in payload.steps])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── Legacy /check-in ──────────────────────────────────────────────

@app.post("/public/check-in/resolve", response_model=PublicCheckInSessionResponse)
def public_check_in_resolve(payload: PublicCheckInResolveRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> PublicCheckInSessionResponse:
    if not get_effective_check_in_settings(db, rs).get("enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check-in disabled.")
    try:
        return PublicCheckInSessionResponse.model_validate(resolve_public_check_in(db=db, settings=rs, email=str(payload.email), code=payload.code.strip()))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/public/check-in/session", response_model=PublicCheckInSessionResponse)
def public_check_in_session(token: str = Query(...), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> PublicCheckInSessionResponse:
    if not get_effective_check_in_settings(db, rs).get("enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check-in disabled.")
    try:
        return PublicCheckInSessionResponse.model_validate(resolve_public_check_in(db=db, settings=rs, token=token))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/public/check-in/submit")
def public_check_in_submit(payload: PublicCheckInSubmitRequest, db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    if not get_effective_check_in_settings(db, rs).get("enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check-in disabled.")
    try:
        return submit_public_check_in(db=db, settings=rs, token=payload.token, rules_accepted=payload.rules_accepted,
            checklist=[i.model_dump(mode="json") for i in payload.checklist], source=payload.entry_source)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── Admin: sync & provision ───────────────────────────────────────

@app.post("/admin/sync")
def admin_sync(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, int]:
    return sync_magicline_bookings(db, rs)


@app.post("/admin/sync/member")
def admin_sync_member(email: str = Query(...), _: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, int | str]:
    return sync_magicline_member_by_email(db, rs, email)


@app.post("/admin/provision")
def admin_provision(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, int]:
    return {"provisioned": provision_due_codes(db, rs)}


# ── Admin: members ────────────────────────────────────────────────

@app.get("/admin/members", response_model=list[MemberSummary])
def admin_members(u: UserRecord = Depends(get_current_user), email: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=200), offset: int = Query(default=0, ge=0), db: Database = Depends(get_database)) -> list[MemberSummary]:
    return [MemberSummary.model_validate(i) for i in db.list_members(email_filter=email, limit=limit, offset=offset)]


@app.get("/admin/members/{member_id}", response_model=MemberDetail)
def admin_member_detail(member_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database)) -> MemberDetail:
    d = get_member_detail(db=db, member_id=member_id)
    if not d:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")
    return MemberDetail.model_validate(d)


# ── Admin: access windows ────────────────────────────────────────

@app.get("/admin/access-windows", response_model=list[AccessWindowSummary])
def admin_access_windows(u: UserRecord = Depends(get_current_user), status_filter: str | None = Query(default=None, alias="status"),
    member_id: int | None = Query(default=None), limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0), db: Database = Depends(get_database)) -> list[AccessWindowSummary]:
    return [AccessWindowSummary.model_validate(i) for i in db.list_access_windows(status_filter=status_filter, member_id=member_id, limit=limit, offset=offset)]


@app.post("/admin/access-windows/{aw_id}/resend")
def admin_aw_resend(aw_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    try:
        return resend_access_code(db=db, settings=rs, access_window_id=aw_id, actor_email=u.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/access-windows/{aw_id}/deactivate")
def admin_aw_deactivate(aw_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database)) -> dict[str, object]:
    try:
        return deactivate_access_window(db=db, access_window_id=aw_id, actor_email=u.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/access-windows/{aw_id}/emergency-code")
def admin_aw_emergency(aw_id: int, u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    try:
        return issue_emergency_access_code(db=db, settings=rs, access_window_id=aw_id, actor_email=u.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ── Admin: lock ───────────────────────────────────────────────────

@app.post("/admin/remote-open")
def admin_remote_open(admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    nuki = NukiClient(rs.model_copy(update=get_effective_nuki_config(db, rs)))
    try:
        result = nuki.remote_open()
    finally:
        nuki.close()
    db.create_admin_action(actor_email=str(admin.email), action="remote-open", payload=result)
    return {"opened": True, **result}


@app.get("/admin/lock/status")
def admin_lock_status(u: UserRecord = Depends(get_current_user), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    nuki = NukiClient(rs.model_copy(update=get_effective_nuki_config(db, rs)))
    try:
        return nuki.get_lock_status()
    finally:
        nuki.close()


@app.get("/admin/lock/log", response_model=list[AdminActionRecord])
def admin_lock_log(u: UserRecord = Depends(get_current_user), limit: int = Query(default=100), offset: int = Query(default=0), db: Database = Depends(get_database)) -> list[AdminActionRecord]:
    return [AdminActionRecord.model_validate(i) for i in db.list_lock_events(limit=limit, offset=offset)]


# ── Admin: alerts & actions ───────────────────────────────────────

@app.get("/admin/alerts", response_model=list[AlertRecord])
def admin_alerts(u: UserRecord = Depends(get_current_user), severity: str | None = Query(default=None), limit: int = Query(default=100), offset: int = Query(default=0), db: Database = Depends(get_database)) -> list[AlertRecord]:
    return [AlertRecord.model_validate(i) for i in db.list_alerts(severity=severity, limit=limit, offset=offset)]


@app.get("/admin/admin-actions", response_model=list[AdminActionRecord])
def admin_actions(u: UserRecord = Depends(get_current_user), limit: int = Query(default=100), offset: int = Query(default=0), db: Database = Depends(get_database)) -> list[AdminActionRecord]:
    return [AdminActionRecord.model_validate(i) for i in db.list_admin_actions(limit=limit, offset=offset)]


# ── Admin: system settings ───────────────────────────────────────

@app.get("/admin/system/email-settings", response_model=SMTPSettingsResponse)
def admin_get_smtp(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> SMTPSettingsResponse:
    s = get_effective_smtp_config(db, rs)
    return SMTPSettingsResponse(smtp_host=s.host, smtp_port=s.port, smtp_username=s.username, smtp_use_tls=s.use_tls, smtp_from_email=s.from_email or None, has_password=bool(s.password))


@app.put("/admin/system/email-settings", response_model=SMTPSettingsResponse)
def admin_put_smtp(payload: SMTPSettingsUpdateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> SMTPSettingsResponse:
    db.set_system_setting(key="smtp", value=payload.model_dump(mode="json"))
    return SMTPSettingsResponse(smtp_host=payload.smtp_host, smtp_port=payload.smtp_port, smtp_username=payload.smtp_username, smtp_use_tls=payload.smtp_use_tls, smtp_from_email=payload.smtp_from_email, has_password=bool(payload.smtp_password))


@app.post("/admin/system/email-test")
def admin_email_test(payload: EmailTestRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, bool | str]:
    svc = EmailService(rs, get_effective_smtp_config(db, rs))
    return {"sent": svc.send_test_email(to_email=payload.to_email, html_body=build_test_email_html(db, rs)), "to_email": str(payload.to_email)}


@app.post("/admin/system/email-test-code")
def admin_email_test_code(payload: EmailTestRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, bool | str]:
    svc = EmailService(rs, get_effective_smtp_config(db, rs))
    url = f"{rs.app_public_base_url.rstrip('/')}/checks"
    return {"sent": svc.send_access_code(to_email=payload.to_email, member_name="Test Mitglied", code="12345",
        valid_from="24. März 2026, 10:00 Uhr", valid_until="25. März 2026, 08:00 Uhr", checks_url=url,
        html_body=build_access_code_email_html(db, rs, member_name="Test Mitglied", code="12345",
            valid_from="24. März 2026, 10:00 Uhr", valid_until="25. März 2026, 08:00 Uhr", checks_url=url)), "to_email": str(payload.to_email)}


@app.get("/admin/system/email-template", response_model=EmailTemplateResponse)
def admin_get_tpl(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> EmailTemplateResponse:
    return EmailTemplateResponse(**get_email_template(db))


@app.put("/admin/system/email-template", response_model=EmailTemplateResponse)
def admin_put_tpl(payload: EmailTemplateUpdateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> EmailTemplateResponse:
    db.set_system_setting(key="email_template", value=payload.model_dump(mode="json"))
    return EmailTemplateResponse(**payload.model_dump())


@app.get("/admin/system/telegram-settings", response_model=TelegramSettingsResponse)
def admin_get_tg(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> TelegramSettingsResponse:
    t = get_effective_telegram_config(db, rs)
    return TelegramSettingsResponse(telegram_chat_id=t.chat_id, has_bot_token=bool(t.bot_token))


@app.put("/admin/system/telegram-settings", response_model=TelegramSettingsResponse)
def admin_put_tg(payload: TelegramSettingsUpdateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> TelegramSettingsResponse:
    db.set_system_setting(key="telegram", value=payload.model_dump(mode="json"))
    return TelegramSettingsResponse(telegram_chat_id=payload.telegram_chat_id, has_bot_token=bool(payload.telegram_bot_token))


@app.post("/admin/system/telegram-test")
def admin_tg_test(payload: TelegramTestRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> dict[str, bool]:
    return {"sent": TelegramService(get_effective_telegram_config(db, rs)).send_message(text=payload.message)}


@app.get("/admin/system/nuki-settings", response_model=NukiSettingsResponse)
def admin_get_nuki(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> NukiSettingsResponse:
    c = get_effective_nuki_config(db, rs)
    return NukiSettingsResponse(nuki_smartlock_id=int(c["nuki_smartlock_id"]), nuki_dry_run=bool(c["nuki_dry_run"]), has_api_token=bool(c["nuki_api_token"]))


@app.put("/admin/system/nuki-settings", response_model=NukiSettingsResponse)
def admin_put_nuki(payload: NukiSettingsUpdateRequest, admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> NukiSettingsResponse:
    db.set_system_setting(key="nuki", value=payload.model_dump(mode="json"))
    db.create_admin_action(actor_email=str(admin.email), action="update-nuki-settings", payload={"smartlock_id": payload.nuki_smartlock_id, "dry_run": payload.nuki_dry_run})
    c = get_effective_nuki_config(db, rs)
    return NukiSettingsResponse(nuki_smartlock_id=int(c["nuki_smartlock_id"]), nuki_dry_run=bool(c["nuki_dry_run"]), has_api_token=bool(c["nuki_api_token"]))


@app.get("/admin/system/magicline-settings", response_model=MagiclineSettingsResponse)
def admin_get_ml(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> MagiclineSettingsResponse:
    c = get_effective_magicline_config(db, rs)
    return MagiclineSettingsResponse(magicline_base_url=str(c["magicline_base_url"]), magicline_studio_id=int(c["magicline_studio_id"]),
        magicline_studio_name=str(c["magicline_studio_name"]), magicline_relevant_appointment_title=str(c["magicline_relevant_appointment_title"]),
        has_api_key=bool(c["magicline_api_key"]), has_webhook_key=bool(c["magicline_webhook_api_key"]))


@app.put("/admin/system/magicline-settings", response_model=MagiclineSettingsResponse)
def admin_put_ml(payload: MagiclineSettingsUpdateRequest, admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> MagiclineSettingsResponse:
    db.set_system_setting(key="magicline", value=payload.model_dump(mode="json"))
    db.create_admin_action(actor_email=str(admin.email), action="update-magicline-settings", payload={"studio_id": payload.magicline_studio_id})
    c = get_effective_magicline_config(db, rs)
    return MagiclineSettingsResponse(magicline_base_url=str(c["magicline_base_url"]), magicline_studio_id=int(c["magicline_studio_id"]),
        magicline_studio_name=str(c["magicline_studio_name"]), magicline_relevant_appointment_title=str(c["magicline_relevant_appointment_title"]),
        has_api_key=bool(c["magicline_api_key"]), has_webhook_key=bool(c["magicline_webhook_api_key"]))


@app.get("/admin/system/check-in-settings", response_model=CheckInSettingsResponse)
def admin_get_checkin(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> CheckInSettingsResponse:
    return CheckInSettingsResponse.model_validate(get_effective_check_in_settings(db, rs))


@app.put("/admin/system/check-in-settings", response_model=CheckInSettingsResponse)
def admin_put_checkin(payload: CheckInSettingsUpdateRequest, admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database), rs: Settings = Depends(get_runtime_settings)) -> CheckInSettingsResponse:
    db.set_system_setting(key="check_in", value=payload.model_dump(mode="json"))
    db.create_admin_action(actor_email=str(admin.email), action="update-check-in-settings", payload={"enabled": payload.enabled})
    return CheckInSettingsResponse.model_validate(get_effective_check_in_settings(db, rs))


@app.get("/admin/system/branding", response_model=BrandingSettingsResponse)
def admin_get_branding(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> BrandingSettingsResponse:
    return BrandingSettingsResponse(**get_branding_settings(db))


@app.put("/admin/system/branding", response_model=BrandingSettingsResponse)
def admin_put_branding(payload: BrandingSettingsUpdateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> BrandingSettingsResponse:
    db.set_system_setting(key="branding", value=payload.model_dump(exclude_unset=True))
    return BrandingSettingsResponse(**get_branding_settings(db))


@app.get("/admin/system/studio-links")
def admin_studio_links(_: UserRecord = Depends(require_admin), rs: Settings = Depends(get_runtime_settings)) -> dict[str, str]:
    url = f"{rs.app_public_base_url.rstrip('/')}/checks"
    return {"checks_url": url, "checks_qr_svg": generate_qr_data_uri(url)}


@app.get("/admin/system/checks-qr.png")
def admin_checks_qr_png(_: UserRecord = Depends(require_admin), rs: Settings = Depends(get_runtime_settings), size: str = Query(default="medium", pattern="^(small|medium|large|print)$")) -> Response:
    url = f"{rs.app_public_base_url.rstrip('/')}/checks"
    box = {"small": 8, "medium": 15, "large": 25, "print": 50}[size]
    return Response(content=generate_qr_png_bytes(url, box_size=box), media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="t247gym-checks-qr-{size}.png"'})


# ── Admin: users ──────────────────────────────────────────────────

@app.get("/admin/users", response_model=list[UserSummary])
def admin_list_users(_: UserRecord = Depends(require_admin), limit: int = Query(default=100), offset: int = Query(default=0), db: Database = Depends(get_database)) -> list[UserSummary]:
    return [UserSummary.model_validate(r) for r in db.list_users(limit=limit, offset=offset)]


@app.post("/admin/users", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def admin_create_user(payload: UserCreateRequest, admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> UserSummary:
    if db.get_user_by_email(str(payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists.")
    user = db.create_user(email=str(payload.email), password=payload.password, role=payload.role, is_active=payload.is_active)
    db.create_admin_action(actor_email=str(admin.email), action="create-user", payload={"email": user["email"], "role": user["role"]})
    return UserSummary.model_validate(user)


@app.put("/admin/users/{user_id}", response_model=UserSummary)
def admin_update_user(user_id: int, payload: UserUpdateRequest, admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> UserSummary:
    existing = db.get_user_by_id(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if existing["id"] == admin.id and not payload.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate yourself.")
    user = db.update_user(user_id=user_id, role=payload.role, is_active=payload.is_active)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    db.create_admin_action(actor_email=str(admin.email), action="update-user", payload={"email": user["email"], "role": user["role"]})
    return UserSummary.model_validate(user)


@app.post("/admin/users/{user_id}/reset-password", response_model=UserSummary)
def admin_reset_user_pw(user_id: int, payload: PasswordResetRequest, admin: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> UserSummary:
    user = db.set_user_password(user_id=user_id, password=payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    db.create_admin_action(actor_email=str(admin.email), action="reset-user-password", payload={"email": user["email"]})
    return UserSummary.model_validate(user)


# ── Admin: funnels ────────────────────────────────────────────────

@app.get("/admin/funnels", response_model=list[FunnelTemplateResponse])
def admin_list_funnels(_: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> list[FunnelTemplateResponse]:
    return [FunnelTemplateResponse(id=int(r["id"]), name=str(r["name"]), slug=str(r["slug"]), funnel_type=str(r["funnel_type"])) for r in list_funnel_templates(db=db)]


@app.post("/admin/funnels", response_model=FunnelTemplateResponse)
def admin_create_funnel(payload: FunnelTemplateCreateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> FunnelTemplateResponse:
    r = upsert_funnel_template_service(db=db, payload=payload)
    return FunnelTemplateResponse(id=int(r["id"]), name=str(r["name"]), slug=str(r["slug"]), funnel_type=str(r["funnel_type"]), description=r.get("description"))


@app.get("/admin/funnels/{tid}", response_model=FunnelTemplateDetail)
def admin_get_funnel(tid: int, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> FunnelTemplateDetail:
    r = get_funnel_template(db=db, template_id=tid)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funnel not found.")
    tpl = FunnelTemplateResponse(id=int(r["id"]), name=str(r["name"]), slug=str(r["slug"]), funnel_type=str(r["funnel_type"]), description=r.get("description"))
    steps = [FunnelStep(id=int(s["id"]), template_id=int(s["template_id"]), step_order=int(s["step_order"]), title=str(s["title"]),
        body=s.get("body"), image_path=s.get("image_path"), requires_note=bool(s["requires_note"]), requires_photo=bool(s["requires_photo"])) for s in r["steps"] or []]
    return FunnelTemplateDetail(template=tpl, steps=steps)


@app.post("/admin/funnels/{tid}/steps", response_model=FunnelStep)
def admin_create_step(tid: int, payload: FunnelStepCreateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> FunnelStep:
    s = upsert_funnel_step_service(db=db, payload=payload)
    return FunnelStep(id=int(s["id"]), template_id=int(s["template_id"]), step_order=int(s["step_order"]), title=str(s["title"]),
        body=s.get("body"), image_path=s.get("image_path"), requires_note=bool(s["requires_note"]), requires_photo=bool(s["requires_photo"]))


@app.put("/admin/funnels/{tid}/steps/{sid}", response_model=FunnelStep)
def admin_update_step(tid: int, sid: int, payload: FunnelStepCreateRequest, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> FunnelStep:
    s = upsert_funnel_step_service(db=db, payload=payload, step_id=sid)
    return FunnelStep(id=int(s["id"]), template_id=int(s["template_id"]), step_order=int(s["step_order"]), title=str(s["title"]),
        body=s.get("body"), image_path=s.get("image_path"), requires_note=bool(s["requires_note"]), requires_photo=bool(s["requires_photo"]))


@app.delete("/admin/funnels/{tid}/steps/{sid}")
def admin_delete_step(tid: int, sid: int, _: UserRecord = Depends(require_admin), db: Database = Depends(get_database)) -> dict[str, bool]:
    delete_funnel_step(db=db, step_id=sid)
    return {"deleted": True}


# ── Admin: media & Magicline debug ────────────────────────────────

@app.post("/admin/media/upload")
def admin_media_upload(file: UploadFile = File(...), _: UserRecord = Depends(require_admin), rs: Settings = Depends(get_runtime_settings)) -> dict[str, str]:
    return {"url": get_media_url(rs, save_media_file(rs, file))}


@app.get("/admin/magicline/bookables")
def admin_ml_bookables(u: UserRecord = Depends(get_current_user), rs: Settings = Depends(get_runtime_settings)) -> list[dict[str, str | int | None]]:
    return list_magicline_bookables(rs)


@app.get("/admin/magicline/member-debug")
def admin_ml_debug(email: str = Query(...), u: UserRecord = Depends(get_current_user), rs: Settings = Depends(get_runtime_settings)) -> dict[str, object]:
    return inspect_magicline_member_by_email(rs, email)
