from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .enums import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = Field(default="bearer")  # noqa: S105
    role: str


class UserRecord(BaseModel):
    id: int
    email: EmailStr
    role: str
    is_active: bool


class UserSummary(BaseModel):
    id: int
    email: EmailStr
    role: UserRole
    is_active: bool


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    role: UserRole
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    role: UserRole
    is_active: bool


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=12)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class CompletePasswordResetRequest(BaseModel):
    token: str = Field(min_length=20)
    password: str = Field(min_length=12)


class MemberSummary(BaseModel):
    id: int
    magicline_customer_id: int
    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    status: str | None = None
    last_synced_at: datetime | None = None


class BookingRecord(BaseModel):
    id: int
    magicline_booking_id: int
    title: str
    booking_status: str
    appointment_status: str | None = None
    participant_status: str | None = None
    start_at: datetime
    end_at: datetime
    source_received_at: datetime


class AccessCodeRecord(BaseModel):
    id: int
    access_window_id: int
    nuki_auth_id: int | None = None
    code_last4: str
    status: str
    is_emergency: bool
    emailed_at: datetime | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    replaced_by_code_id: int | None = None
    created_at: datetime


class AccessWindowDetail(BaseModel):
    id: int
    member_id: int
    booking_id: int
    booking_ids: list[int] = Field(default_factory=list)
    booking_count: int
    starts_at: datetime
    ends_at: datetime
    dispatch_at: datetime
    status: str
    access_reason: str
    check_in_required: bool = False
    check_in_confirmed_at: datetime | None = None
    check_in_source: str | None = None
    check_in_checklist: list[dict] = Field(default_factory=list)


class MemberDetail(BaseModel):
    member: MemberSummary
    bookings: list[BookingRecord]
    access_windows: list[AccessWindowDetail]
    access_codes: list[AccessCodeRecord]


class AccessWindowSummary(BaseModel):
    id: int
    member_id: int
    booking_id: int
    booking_count: int
    starts_at: datetime
    ends_at: datetime
    dispatch_at: datetime
    status: str
    access_reason: str
    check_in_required: bool = False
    check_in_confirmed_at: datetime | None = None
    check_in_source: str | None = None


class AlertRecord(BaseModel):
    id: int
    severity: str
    kind: str
    message: str
    created_at: datetime


class AdminActionRecord(BaseModel):
    id: int
    actor_email: EmailStr
    action: str
    access_window_id: int | None = None
    access_code_id: int | None = None
    payload: dict = Field(default_factory=dict)
    created_at: datetime


class SMTPSettingsUpdateRequest(BaseModel):
    smtp_host: str
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_email: EmailStr


class SMTPSettingsResponse(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_use_tls: bool
    smtp_from_email: EmailStr | None = None
    has_password: bool


class EmailTestRequest(BaseModel):
    to_email: EmailStr


class TelegramSettingsUpdateRequest(BaseModel):
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


class TelegramSettingsResponse(BaseModel):
    telegram_chat_id: str
    has_bot_token: bool


class TelegramTestRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class NukiSettingsUpdateRequest(BaseModel):
    nuki_api_token: str = ""
    nuki_smartlock_id: int = 0
    nuki_dry_run: bool = True


class NukiSettingsResponse(BaseModel):
    nuki_smartlock_id: int
    nuki_dry_run: bool
    has_api_token: bool


class MagiclineSettingsUpdateRequest(BaseModel):
    magicline_base_url: str = ""
    magicline_api_key: str = ""
    magicline_webhook_api_key: str = ""
    magicline_studio_id: int = 0
    magicline_studio_name: str = ""
    magicline_relevant_appointment_title: str = "Freies Training"


class MagiclineSettingsResponse(BaseModel):
    magicline_base_url: str
    magicline_studio_id: int
    magicline_studio_name: str
    magicline_relevant_appointment_title: str
    has_api_key: bool
    has_webhook_key: bool


class CheckInChecklistItem(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)


class CheckInSettingsUpdateRequest(BaseModel):
    enabled: bool = False
    title: str = Field(min_length=1, max_length=140)
    intro: str = Field(min_length=1, max_length=1000)
    rules_heading: str = Field(min_length=1, max_length=120)
    rules_body: str = Field(min_length=1, max_length=5000)
    checklist_heading: str = Field(min_length=1, max_length=120)
    checklist_items: list[CheckInChecklistItem] = Field(default_factory=list, max_length=20)
    success_message: str = Field(min_length=1, max_length=1000)


class CheckInSettingsResponse(CheckInSettingsUpdateRequest):
    studio_check_in_url: str
    studio_qr_svg: str


class PublicCheckInResolveRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class PublicCheckInChecklistAnswer(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    checked: bool


class PublicCheckInSubmitRequest(BaseModel):
    token: str = Field(min_length=20)
    rules_accepted: bool
    entry_source: str = Field(min_length=1, max_length=40)
    checklist: list[PublicCheckInChecklistAnswer] = Field(default_factory=list, max_length=20)


class PublicCheckInWindow(BaseModel):
    access_window_id: int
    member_first_name: str | None = None
    member_email: EmailStr | None = None
    starts_at: datetime
    ends_at: datetime
    status: str
    confirmed_at: datetime | None = None
    source: str | None = None
    is_confirmed: bool


class PublicCheckInSessionResponse(BaseModel):
    token: str
    entry_source: str
    settings: CheckInSettingsResponse
    window: PublicCheckInWindow


class AccessWindowCheckInRecord(BaseModel):
    access_window_id: int
    confirmed_at: datetime
    source: str
    rules_accepted: bool
    checklist: list[dict] = Field(default_factory=list)


class FunnelTemplateSummary(BaseModel):
    id: int
    name: str
    slug: str
    funnel_type: str


class FunnelStep(BaseModel):
    id: int
    template_id: int
    step_order: int
    title: str
    body: str | None = None
    image_path: str | None = None
    requires_note: bool
    requires_photo: bool


class FunnelTemplateDetail(BaseModel):
    template: FunnelTemplateSummary
    steps: list[FunnelStep]


class FunnelTemplateCreateRequest(BaseModel):
    name: str
    slug: str
    funnel_type: str
    description: str | None = None


class FunnelStepCreateRequest(BaseModel):
    template_id: int
    step_order: int
    title: str
    body: str | None = None
    image_path: str | None = None
    requires_note: bool = False
    requires_photo: bool = False


class FunnelTemplateResponse(FunnelTemplateSummary):
    description: str | None = None
class FunnelStepEvent(BaseModel):
    step_id: int
    status: str
    note: str | None = None
    photo_path: str | None = None


class FunnelSubmission(BaseModel):
    id: int
    access_window_id: int
    template_id: int
    entry_source: str
    success: bool
    created_at: datetime
    steps: list[FunnelStepEvent]


class ChecksResolveRequest(BaseModel):
    """Public /checks login: email + current access code."""
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class ChecksWindowInfo(BaseModel):
    """Summary of a single access window as returned to the member."""
    id: int
    starts_at: datetime
    ends_at: datetime
    status: str
    booking_count: int
    access_reason: str
    checkin_confirmed_at: datetime | None = None
    checkout_confirmed_at: datetime | None = None
    has_checkin_funnel: bool = False
    has_checkout_funnel: bool = False


class ChecksSessionResponse(BaseModel):
    """Full session payload returned after successful resolve."""
    token: str
    member_name: str
    member_email: str
    windows: list[ChecksWindowInfo]


class ChecksFunnelStepData(BaseModel):
    """One step answer submitted by the member."""
    step_id: int
    checked: bool = False
    note: str = ""


class ChecksSubmitRequest(BaseModel):
    """Payload for checkin or checkout funnel submission."""
    token: str = Field(min_length=20)
    window_id: int
    funnel_type: str = Field(pattern="^(checkin|checkout)$")
    steps: list[ChecksFunnelStepData] = Field(default_factory=list)


class ChecksFunnelStep(BaseModel):
    """One step of a funnel template as returned to the public UI."""
    id: int
    template_id: int
    step_order: int
    title: str
    body: str | None = None
    image_path: str | None = None
    requires_note: bool
    requires_photo: bool


class ChecksFunnelResponse(BaseModel):
    """Active funnel template with all steps."""
    template_id: int
    template_name: str
    funnel_type: str
    description: str | None = None
    steps: list[ChecksFunnelStep]


class MagiclineWebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    event_id: str | None = Field(default=None, alias="eventId")
    event_type: str | None = Field(default=None, alias="eventType")
    uuid: str | None = None
    entity_id: int | None = Field(default=None, alias="entityId")
    payload: list[dict] | dict | None = None


class MagiclineCustomer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    email: str | None = None
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    status: str | None = None
    created_datetime: datetime | None = Field(default=None, alias="createdDateTime")
    additional_information_field_assignments: list[dict] = Field(
        default_factory=list,
        alias="additionalInformationFieldAssignments",
    )


class MagiclineBooking(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    booking_id: int = Field(alias="bookingId")
    booking_status: str = Field(alias="bookingStatus")
    start_date_time: datetime = Field(alias="startDateTime")
    end_date_time: datetime = Field(alias="endDateTime")
    title: str | None = None
    duration: int | None = None
    category: str | None = None
    appointment_status: str | None = Field(default=None, alias="appointmentStatus")
    participant_status: str | None = Field(default=None, alias="participantStatus")


class ProvisioningResult(BaseModel):
    access_window_id: int
    member_email: str | None
    code_last4: str
    dispatched: bool


class EmailTemplateUpdateRequest(BaseModel):
    header_html: str
    body_html: str
    footer_html: str


class EmailTemplateResponse(BaseModel):
    header_html: str
    body_html: str
    footer_html: str
