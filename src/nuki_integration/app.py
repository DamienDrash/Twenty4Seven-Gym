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
    AccessWindowSummary,
    AdminActionRecord,
    AlertRecord,
    CheckInSettingsResponse,
    CheckInSettingsUpdateRequest,
    ChecksFunnelResponse,
    ChecksResolveRequest,
    ChecksSessionResponse,
    ChecksSubmitRequest,
    CompletePasswordResetRequest,
    EmailTestRequest,
    ForgotPasswordRequest,
    FunnelStep,
    FunnelStepCreateRequest,
    FunnelTemplateCreateRequest,
    FunnelTemplateDetail,
    FunnelTemplateResponse,
    LoginRequest,
    LoginResponse,
    MagiclineSettingsResponse,
    MagiclineSettingsUpdateRequest,
    MagiclineWebhookEnvelope,
    MemberDetail,
    MemberSummary,
    NukiSettingsResponse,
    NukiSettingsUpdateRequest,
    PasswordResetRequest,
    PublicCheckInResolveRequest,
    PublicCheckInSessionResponse,
    PublicCheckInSubmitRequest,
    SMTPSettingsResponse,
    SMTPSettingsUpdateRequest,
    TelegramSettingsResponse,
    TelegramSettingsUpdateRequest,
    TelegramTestRequest,
    UserCreateRequest,
    UserRecord,
    UserSummary,
    UserUpdateRequest,
)
from .notifications import EmailService, TelegramService
from .nuki_client import NukiClient
from .services import (
    complete_password_reset,
    deactivate_access_window,
    delete_funnel_step,
    generate_qr_data_uri,
    generate_qr_png_bytes,
    get_effective_check_in_settings,
    get_effective_magicline_config,
    get_effective_nuki_config,
    get_effective_smtp_config,
    get_effective_telegram_config,
    get_funnel_template,
    get_media_url,
    get_member_detail,
    inspect_magicline_member_by_email,
    issue_emergency_access_code,
    list_funnel_templates,
    get_active_funnel_for_type,
    list_magicline_bookables,
    process_magicline_webhook,
    provision_due_codes,
    request_password_reset,
    resend_access_code,
    resolve_public_check_in,
    resolve_checks_session,
    save_media_file,
    submit_public_check_in,
    submit_checks_funnel,
    sync_magicline_bookings,
    sync_magicline_member_by_email,
    upsert_funnel_step_service,
    upsert_funnel_template_service,
)

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
        )
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown or inactive user.",
        )
    return UserRecord.model_validate(user)


def require_admin(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )
    return current_user


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db = get_database()
    db.ensure_schema()
    db.bootstrap_admin(settings.bootstrap_admin_email, settings.bootstrap_admin_password)
    logger.info("Access platform starting")
    yield
    logger.info("Access platform shutting down")
    db.close()


app = FastAPI(title="Studio Access Platform", version="0.1.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "opengym",
        "status": "ok",
        "docs": "/docs",
        "health": "/healthz/live",
    }


@app.get("/app", include_in_schema=False)
@app.get("/reset-password", include_in_schema=False)
@app.get("/check-in", include_in_schema=False)
def frontend_shell() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/checks", include_in_schema=False)
def checks_shell() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/public/checks/resolve", response_model=ChecksSessionResponse)
def public_checks_resolve(
    payload: ChecksResolveRequest,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> ChecksSessionResponse:
    try:
        result = resolve_checks_session(
            db=db,
            settings=runtime_settings,
            email=str(payload.email),
            code=payload.code.strip(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return ChecksSessionResponse.model_validate(result)


@app.get("/public/checks/session", response_model=ChecksSessionResponse)
def public_checks_session(
    token: str = Query(...),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> ChecksSessionResponse:
    try:
        result = resolve_checks_session(
            db=db,
            settings=runtime_settings,
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return ChecksSessionResponse.model_validate(result)


@app.get("/public/checks/funnel/{funnel_type}", response_model=ChecksFunnelResponse)
def public_checks_funnel_get(
    funnel_type: str,
    db: Database = Depends(get_database),
) -> ChecksFunnelResponse:
    funnel = get_active_funnel_for_type(db=db, funnel_type=funnel_type)
    if not funnel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Kein aktiver {funnel_type}-Funnel konfiguriert.",
        )
    return ChecksFunnelResponse(
        template_id=int(funnel["id"]),
        template_name=str(funnel["name"]),
        funnel_type=str(funnel["funnel_type"]),
        description=funnel.get("description"),
        steps=funnel.get("steps") or [],
    )


@app.post("/public/checks/window/{window_id}/checkin")
def public_checks_window_checkin(
    window_id: int,
    payload: ChecksSubmitRequest,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    try:
        return submit_checks_funnel(
            db=db,
            settings=runtime_settings,
            token=payload.token,
            window_id=window_id,
            funnel_type="checkin",
            steps_data=[s.model_dump() for s in payload.steps],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@app.post("/public/checks/window/{window_id}/checkout")
def public_checks_window_checkout(
    window_id: int,
    payload: ChecksSubmitRequest,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    try:
        return submit_checks_funnel(
            db=db,
            settings=runtime_settings,
            token=payload.token,
            window_id=window_id,
            funnel_type="checkout",
            steps_data=[s.model_dump() for s in payload.steps],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@app.get("/healthz/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


def _require_magicline_webhook_api_key(
    runtime_settings: Settings,
    provided_key: str | None,
) -> None:
    expected_key = runtime_settings.magicline_webhook_api_key.strip()
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Magicline webhook key not configured.",
        )
    if not provided_key or provided_key.strip() != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook API key.",
        )


@app.get("/healthz/ready")
def readiness(db: Database = Depends(get_database)) -> dict[str, str]:
    if not db.health_check():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        )
    return {"status": "ready"}


@app.post("/webhooks/magicline")
@app.post("/webhook/magicline")
def magicline_webhook(
    payload: MagiclineWebhookEnvelope = Body(...),
    x_api_key: Annotated[str | None, Header(alias="X-API-KEY")] = None,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, int | str | bool]:
    _require_magicline_webhook_api_key(runtime_settings, x_api_key)
    return process_magicline_webhook(db, runtime_settings, payload.model_dump(mode="json"))


@app.post("/auth/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> LoginResponse:
    user = db.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")
    access_token = issue_token(
        subject=user["email"],
        role=user["role"],
        secret=runtime_settings.jwt_secret,
    )
    return LoginResponse(access_token=access_token, role=user["role"])


@app.get("/me", response_model=UserRecord)
def me(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    return current_user


@app.post("/auth/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, bool]:
    return request_password_reset(db=db, settings=runtime_settings, email=str(payload.email))


@app.post("/auth/reset-password")
def reset_password(
    payload: CompletePasswordResetRequest,
    db: Database = Depends(get_database),
) -> dict[str, bool]:
    try:
        return complete_password_reset(db=db, token=payload.token, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/sync")
def admin_sync(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, int]:
    return sync_magicline_bookings(db, runtime_settings)


@app.post("/admin/sync/member")
def admin_sync_member(
    email: str = Query(...),
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, int | str]:
    return sync_magicline_member_by_email(db, runtime_settings, email)


@app.get("/admin/magicline/bookables")
def admin_magicline_bookables(
    _current_user: UserRecord = Depends(get_current_user),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> list[dict[str, str | int | None]]:
    return list_magicline_bookables(runtime_settings)


@app.get("/admin/magicline/member-debug")
def admin_magicline_member_debug(
    email: str = Query(...),
    _current_user: UserRecord = Depends(get_current_user),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    return inspect_magicline_member_by_email(runtime_settings, email)


@app.get("/admin/system/email-settings", response_model=SMTPSettingsResponse)
def admin_get_email_settings(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> SMTPSettingsResponse:
    smtp = get_effective_smtp_config(db, runtime_settings)
    return SMTPSettingsResponse(
        smtp_host=smtp.host,
        smtp_port=smtp.port,
        smtp_username=smtp.username,
        smtp_use_tls=smtp.use_tls,
        smtp_from_email=smtp.from_email or None,
        has_password=bool(smtp.password),
    )


@app.put("/admin/system/email-settings", response_model=SMTPSettingsResponse)
def admin_put_email_settings(
    payload: SMTPSettingsUpdateRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> SMTPSettingsResponse:
    value = payload.model_dump(mode="json")
    db.set_system_setting(key="smtp", value=value)
    return SMTPSettingsResponse(
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_username=payload.smtp_username,
        smtp_use_tls=payload.smtp_use_tls,
        smtp_from_email=payload.smtp_from_email,
        has_password=bool(payload.smtp_password),
    )


@app.post("/admin/system/email-test")
def admin_email_test(
    payload: EmailTestRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, bool | str]:
    smtp = get_effective_smtp_config(db, runtime_settings)
    email_service = EmailService(runtime_settings, smtp)
    sent = email_service.send_test_email(to_email=payload.to_email)
    return {"sent": sent, "to_email": str(payload.to_email)}


@app.get("/admin/system/telegram-settings", response_model=TelegramSettingsResponse)
def admin_get_telegram_settings(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> TelegramSettingsResponse:
    telegram = get_effective_telegram_config(db, runtime_settings)
    return TelegramSettingsResponse(
        telegram_chat_id=telegram.chat_id,
        has_bot_token=bool(telegram.bot_token),
    )


@app.put("/admin/system/telegram-settings", response_model=TelegramSettingsResponse)
def admin_put_telegram_settings(
    payload: TelegramSettingsUpdateRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> TelegramSettingsResponse:
    value = payload.model_dump(mode="json")
    db.set_system_setting(key="telegram", value=value)
    return TelegramSettingsResponse(
        telegram_chat_id=payload.telegram_chat_id,
        has_bot_token=bool(payload.telegram_bot_token),
    )


@app.post("/admin/system/telegram-test")
def admin_telegram_test(
    payload: TelegramTestRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, bool]:
    telegram = TelegramService(get_effective_telegram_config(db, runtime_settings))
    return {"sent": telegram.send_message(text=payload.message)}


def _template_response(row: dict[str, object]) -> FunnelTemplateResponse:
    return FunnelTemplateResponse(
        id=int(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        funnel_type=str(row["funnel_type"]),
        description=row.get("description"),
    )


@app.get("/admin/funnels", response_model=list[FunnelTemplateResponse])
def admin_list_funnels(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> list[FunnelTemplateResponse]:
    rows = list_funnel_templates(db=db)
    return [_template_response(row) for row in rows]


@app.post("/admin/funnels", response_model=FunnelTemplateResponse)
def admin_create_funnel(
    payload: FunnelTemplateCreateRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> FunnelTemplateResponse:
    row = upsert_funnel_template_service(db=db, payload=payload)
    return _template_response(row)


@app.put("/admin/funnels/{template_id}", response_model=FunnelTemplateResponse)
def admin_update_funnel(
    template_id: int,
    payload: FunnelTemplateCreateRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> FunnelTemplateResponse:
    row = upsert_funnel_template_service(db=db, payload=payload, template_id=template_id)
    return _template_response(row)


@app.get("/admin/funnels/{template_id}", response_model=FunnelTemplateDetail)
def admin_get_funnel(
    template_id: int,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> FunnelTemplateDetail:
    row = get_funnel_template(db=db, template_id=template_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Funnel not found.")
    template = _template_response(row)
    steps = [
        FunnelStep(
            id=int(step["id"]),
            template_id=int(step["template_id"]),
            step_order=int(step["step_order"]),
            title=str(step["title"]),
            body=step.get("body"),
            image_path=step.get("image_path"),
            requires_note=bool(step["requires_note"]),
            requires_photo=bool(step["requires_photo"]),
        )
        for step in row["steps"] or []
    ]
    return FunnelTemplateDetail(template=template, steps=steps)


@app.post("/admin/funnels/{template_id}/steps", response_model=FunnelStep)
def admin_create_funnel_step(
    template_id: int,
    payload: FunnelStepCreateRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> FunnelStep:
    step = upsert_funnel_step_service(db=db, payload=payload)
    return FunnelStep(
        id=int(step["id"]),
        template_id=int(step["template_id"]),
        step_order=int(step["step_order"]),
        title=str(step["title"]),
        body=step.get("body"),
        image_path=step.get("image_path"),
        requires_note=bool(step["requires_note"]),
        requires_photo=bool(step["requires_photo"]),
    )


@app.put("/admin/funnels/{template_id}/steps/{step_id}", response_model=FunnelStep)
def admin_update_funnel_step(
    template_id: int,
    step_id: int,
    payload: FunnelStepCreateRequest,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> FunnelStep:
    step = upsert_funnel_step_service(db=db, payload=payload, step_id=step_id)
    return FunnelStep(
        id=int(step["id"]),
        template_id=int(step["template_id"]),
        step_order=int(step["step_order"]),
        title=str(step["title"]),
        body=step.get("body"),
        image_path=step.get("image_path"),
        requires_note=bool(step["requires_note"]),
        requires_photo=bool(step["requires_photo"]),
    )


@app.delete("/admin/funnels/{template_id}/steps/{step_id}")
def admin_delete_funnel_step(
    template_id: int,
    step_id: int,
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> dict[str, bool]:
    delete_funnel_step(db=db, step_id=step_id)
    return {"deleted": True}


@app.post("/admin/media/upload")
def admin_media_upload(
    file: UploadFile = File(...),
    _admin: UserRecord = Depends(require_admin),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, str]:
    filename = save_media_file(runtime_settings, file)
    return {"url": get_media_url(runtime_settings, filename)}


@app.get("/admin/system/check-in-settings", response_model=CheckInSettingsResponse)
def admin_get_check_in_settings(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> CheckInSettingsResponse:
    return CheckInSettingsResponse.model_validate(
        get_effective_check_in_settings(db, runtime_settings)
    )


@app.put("/admin/system/check-in-settings", response_model=CheckInSettingsResponse)
def admin_put_check_in_settings(
    payload: CheckInSettingsUpdateRequest,
    admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> CheckInSettingsResponse:
    db.set_system_setting(key="check_in", value=payload.model_dump(mode="json"))
    db.create_admin_action(
        actor_email=str(admin.email),
        action="update-check-in-settings",
        payload={"enabled": payload.enabled, "checklist_count": len(payload.checklist_items)},
    )
    return CheckInSettingsResponse.model_validate(
        get_effective_check_in_settings(db, runtime_settings)
    )


@app.get("/admin/system/studio-links")
def admin_studio_links(
    _admin: UserRecord = Depends(require_admin),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, str]:
    checks_url = f"{runtime_settings.app_public_base_url.rstrip('/')}/checks"
    return {
        "checks_url": checks_url,
        "checks_qr_svg": generate_qr_data_uri(checks_url),
    }


@app.get("/admin/system/checks-qr.png")
def admin_checks_qr_png(
    _admin: UserRecord = Depends(require_admin),
    runtime_settings: Settings = Depends(get_runtime_settings),
    size: str = Query(default="medium", pattern="^(small|medium|large|print)$"),
) -> Response:
    checks_url = f"{runtime_settings.app_public_base_url.rstrip('/')}/checks"
    box_sizes = {"small": 8, "medium": 15, "large": 25, "print": 50}
    png_bytes = generate_qr_png_bytes(checks_url, box_size=box_sizes[size])
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="t247gym-checks-qr-{size}.png"'},
    )


@app.get("/admin/system/nuki-settings", response_model=NukiSettingsResponse)
def admin_get_nuki_settings(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> NukiSettingsResponse:
    cfg = get_effective_nuki_config(db, runtime_settings)
    return NukiSettingsResponse(
        nuki_smartlock_id=int(cfg["nuki_smartlock_id"]),
        nuki_dry_run=bool(cfg["nuki_dry_run"]),
        has_api_token=bool(cfg["nuki_api_token"]),
    )


@app.put("/admin/system/nuki-settings", response_model=NukiSettingsResponse)
def admin_put_nuki_settings(
    payload: NukiSettingsUpdateRequest,
    admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> NukiSettingsResponse:
    db.set_system_setting(key="nuki", value=payload.model_dump(mode="json"))
    db.create_admin_action(
        actor_email=str(admin.email),
        action="update-nuki-settings",
        payload={"smartlock_id": payload.nuki_smartlock_id, "dry_run": payload.nuki_dry_run},
    )
    cfg = get_effective_nuki_config(db, runtime_settings)
    return NukiSettingsResponse(
        nuki_smartlock_id=int(cfg["nuki_smartlock_id"]),
        nuki_dry_run=bool(cfg["nuki_dry_run"]),
        has_api_token=bool(cfg["nuki_api_token"]),
    )


@app.get("/admin/system/magicline-settings", response_model=MagiclineSettingsResponse)
def admin_get_magicline_settings(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> MagiclineSettingsResponse:
    cfg = get_effective_magicline_config(db, runtime_settings)
    return MagiclineSettingsResponse(
        magicline_base_url=str(cfg["magicline_base_url"]),
        magicline_studio_id=int(cfg["magicline_studio_id"]),
        magicline_studio_name=str(cfg["magicline_studio_name"]),
        magicline_relevant_appointment_title=str(cfg["magicline_relevant_appointment_title"]),
        has_api_key=bool(cfg["magicline_api_key"]),
        has_webhook_key=bool(cfg["magicline_webhook_api_key"]),
    )


@app.put("/admin/system/magicline-settings", response_model=MagiclineSettingsResponse)
def admin_put_magicline_settings(
    payload: MagiclineSettingsUpdateRequest,
    admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> MagiclineSettingsResponse:
    db.set_system_setting(key="magicline", value=payload.model_dump(mode="json"))
    db.create_admin_action(
        actor_email=str(admin.email),
        action="update-magicline-settings",
        payload={"studio_id": payload.magicline_studio_id, "base_url": payload.magicline_base_url},
    )
    cfg = get_effective_magicline_config(db, runtime_settings)
    return MagiclineSettingsResponse(
        magicline_base_url=str(cfg["magicline_base_url"]),
        magicline_studio_id=int(cfg["magicline_studio_id"]),
        magicline_studio_name=str(cfg["magicline_studio_name"]),
        magicline_relevant_appointment_title=str(cfg["magicline_relevant_appointment_title"]),
        has_api_key=bool(cfg["magicline_api_key"]),
        has_webhook_key=bool(cfg["magicline_webhook_api_key"]),
    )


@app.get("/admin/users", response_model=list[UserSummary])
def admin_list_users(
    _admin: UserRecord = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
) -> list[UserSummary]:
    return [UserSummary.model_validate(row) for row in db.list_users(limit=limit, offset=offset)]


@app.post("/admin/users", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def admin_create_user(
    payload: UserCreateRequest,
    admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> UserSummary:
    if db.get_user_by_email(str(payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists.")
    user = db.create_user(
        email=str(payload.email),
        password=payload.password,
        role=payload.role,
        is_active=payload.is_active,
    )
    db.create_admin_action(
        actor_email=str(admin.email),
        action="create-user",
        payload={
            "user_id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "is_active": user["is_active"],
        },
    )
    return UserSummary.model_validate(user)


@app.put("/admin/users/{user_id}", response_model=UserSummary)
def admin_update_user(
    user_id: int,
    payload: UserUpdateRequest,
    admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> UserSummary:
    existing = db.get_user_by_id(user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if existing["id"] == admin.id and not payload.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current admin cannot deactivate itself.",
        )
    user = db.update_user(user_id=user_id, role=payload.role, is_active=payload.is_active)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    db.create_admin_action(
        actor_email=str(admin.email),
        action="update-user",
        payload={
            "user_id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "is_active": user["is_active"],
        },
    )
    return UserSummary.model_validate(user)


@app.post("/admin/users/{user_id}/reset-password", response_model=UserSummary)
def admin_reset_user_password(
    user_id: int,
    payload: PasswordResetRequest,
    admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
) -> UserSummary:
    user = db.set_user_password(user_id=user_id, password=payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    db.create_admin_action(
        actor_email=str(admin.email),
        action="reset-user-password",
        payload={"user_id": user["id"], "email": user["email"]},
    )
    return UserSummary.model_validate(user)


@app.post("/admin/provision")
def admin_provision(
    _admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, int]:
    return {"provisioned": provision_due_codes(db, runtime_settings)}


@app.get("/admin/members", response_model=list[MemberSummary])
def admin_members(
    _current_user: UserRecord = Depends(get_current_user),
    email: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
) -> list[MemberSummary]:
    return [
        MemberSummary.model_validate(item)
        for item in db.list_members(email_filter=email, limit=limit, offset=offset)
    ]


@app.get("/admin/members/{member_id}", response_model=MemberDetail)
def admin_member_detail(
    member_id: int,
    _current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
) -> MemberDetail:
    detail = get_member_detail(db=db, member_id=member_id)
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")
    return MemberDetail.model_validate(detail)


@app.post("/public/check-in/resolve", response_model=PublicCheckInSessionResponse)
def public_check_in_resolve(
    payload: PublicCheckInResolveRequest,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> PublicCheckInSessionResponse:
    if not get_effective_check_in_settings(db, runtime_settings).get("enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check-in disabled.")
    try:
        resolved = resolve_public_check_in(
            db=db,
            settings=runtime_settings,
            email=str(payload.email),
            code=payload.code.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PublicCheckInSessionResponse.model_validate(resolved)


@app.get("/public/check-in/session", response_model=PublicCheckInSessionResponse)
def public_check_in_session(
    token: str = Query(...),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> PublicCheckInSessionResponse:
    if not get_effective_check_in_settings(db, runtime_settings).get("enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check-in disabled.")
    try:
        resolved = resolve_public_check_in(
            db=db,
            settings=runtime_settings,
            token=token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PublicCheckInSessionResponse.model_validate(resolved)


@app.post("/public/check-in/submit")
def public_check_in_submit(
    payload: PublicCheckInSubmitRequest,
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    if not get_effective_check_in_settings(db, runtime_settings).get("enabled"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Check-in disabled.")
    try:
        return submit_public_check_in(
            db=db,
            settings=runtime_settings,
            token=payload.token,
            rules_accepted=payload.rules_accepted,
            checklist=[item.model_dump(mode="json") for item in payload.checklist],
            source=payload.entry_source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/admin/access-windows", response_model=list[AccessWindowSummary])
def admin_access_windows(
    _current_user: UserRecord = Depends(get_current_user),
    status_filter: str | None = Query(default=None, alias="status"),
    member_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
) -> list[AccessWindowSummary]:
    return [
        AccessWindowSummary.model_validate(item)
        for item in db.list_access_windows(
            status_filter=status_filter,
            member_id=member_id,
            limit=limit,
            offset=offset,
        )
    ]


@app.post("/admin/access-windows/{access_window_id}/resend")
def admin_access_window_resend(
    access_window_id: int,
    current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    try:
        return resend_access_code(
            db=db,
            settings=runtime_settings,
            access_window_id=access_window_id,
            actor_email=current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/access-windows/{access_window_id}/deactivate")
def admin_access_window_deactivate(
    access_window_id: int,
    current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
) -> dict[str, object]:
    try:
        return deactivate_access_window(
            db=db,
            access_window_id=access_window_id,
            actor_email=current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/access-windows/{access_window_id}/emergency-code")
def admin_access_window_emergency_code(
    access_window_id: int,
    current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    try:
        return issue_emergency_access_code(
            db=db,
            settings=runtime_settings,
            access_window_id=access_window_id,
            actor_email=current_user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/admin/remote-open")
def admin_remote_open(
    admin: UserRecord = Depends(require_admin),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    nuki = NukiClient(runtime_settings.model_copy(update=get_effective_nuki_config(db, runtime_settings)))
    try:
        result = nuki.remote_open()
    finally:
        nuki.close()
    db.create_admin_action(
        actor_email=str(admin.email),
        action="remote-open",
        payload=result,
    )
    return {"opened": True, **result}


@app.get("/admin/lock/status")
def admin_lock_status(
    _current_user: UserRecord = Depends(get_current_user),
    db: Database = Depends(get_database),
    runtime_settings: Settings = Depends(get_runtime_settings),
) -> dict[str, object]:
    nuki = NukiClient(runtime_settings.model_copy(update=get_effective_nuki_config(db, runtime_settings)))
    try:
        return nuki.get_lock_status()
    finally:
        nuki.close()


@app.get("/admin/lock/log", response_model=list[AdminActionRecord])
def admin_lock_log(
    _current_user: UserRecord = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
) -> list[AdminActionRecord]:
    return [
        AdminActionRecord.model_validate(item)
        for item in db.list_lock_events(limit=limit, offset=offset)
    ]


@app.get("/admin/alerts", response_model=list[AlertRecord])
def admin_alerts(
    _current_user: UserRecord = Depends(get_current_user),
    severity: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
) -> list[AlertRecord]:
    return [
        AlertRecord.model_validate(item)
        for item in db.list_alerts(severity=severity, limit=limit, offset=offset)
    ]


@app.get("/admin/admin-actions", response_model=list[AdminActionRecord])
def admin_actions(
    _current_user: UserRecord = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
) -> list[AdminActionRecord]:
    return [
        AdminActionRecord.model_validate(item)
        for item in db.list_admin_actions(limit=limit, offset=offset)
    ]
