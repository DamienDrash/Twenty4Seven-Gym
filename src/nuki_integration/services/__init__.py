"""Service layer — re-exports for backward compatibility.

Import from sub-modules directly when writing new code.
"""

from .access import (
    deactivate_access_window,
    deprovision_expired_codes,
    issue_emergency_access_code,
    provision_due_codes,
    resend_access_code,
)
from .auth_tokens import (
    build_check_in_link,
    build_checks_link,
    decode_check_in_token,
    decode_checks_token,
    issue_check_in_token,
    issue_checks_token,
)
from .checkin import (
    resolve_public_check_in,
    submit_public_check_in,
)
from .checks import (
    get_active_funnel_for_type,
    resolve_checks_session,
    submit_checks_funnel,
)
from .email_builder import (
    build_access_code_email_html,
    build_password_reset_email_html,
    build_test_email_html,
    get_email_template,
)
from .funnels import (
    delete_funnel_step,
    get_funnel_template,
    list_funnel_templates,
    upsert_funnel_step_service,
    upsert_funnel_template_service,
)
from .media import (
    get_media_url,
    save_media_file,
)
from .members import get_member_detail
from .password import (
    complete_password_reset,
    request_password_reset,
)
from .qr import (
    generate_qr_data_uri,
    generate_qr_png_bytes,
)
from .settings import (
    get_branding_settings,
    get_effective_check_in_settings,
    get_effective_magicline_config,
    get_effective_nuki_config,
    get_effective_smtp_config,
    get_effective_telegram_config,
)
from .sync import (
    inspect_magicline_member_by_email,
    list_magicline_bookables,
    process_magicline_webhook,
    sync_magicline_bookings,
    sync_magicline_member_by_email,
)

__all__ = [
    # access
    "deactivate_access_window",
    "deprovision_expired_codes",
    "issue_emergency_access_code",
    "provision_due_codes",
    "resend_access_code",
    # auth_tokens
    "build_check_in_link",
    "build_checks_link",
    "decode_check_in_token",
    "decode_checks_token",
    "issue_check_in_token",
    "issue_checks_token",
    # checkin
    "resolve_public_check_in",
    "submit_public_check_in",
    # checks
    "get_active_funnel_for_type",
    "resolve_checks_session",
    "submit_checks_funnel",
    # email_builder
    "build_access_code_email_html",
    "build_password_reset_email_html",
    "build_test_email_html",
    "get_email_template",
    # funnels
    "delete_funnel_step",
    "get_funnel_template",
    "list_funnel_templates",
    "upsert_funnel_step_service",
    "upsert_funnel_template_service",
    # media
    "get_media_url",
    "save_media_file",
    # members
    "get_member_detail",
    # password
    "complete_password_reset",
    "request_password_reset",
    # qr
    "generate_qr_data_uri",
    "generate_qr_png_bytes",
    # settings
    "get_branding_settings",
    "get_effective_check_in_settings",
    "get_effective_magicline_config",
    "get_effective_nuki_config",
    "get_effective_smtp_config",
    "get_effective_telegram_config",
    # sync
    "inspect_magicline_member_by_email",
    "list_magicline_bookables",
    "process_magicline_webhook",
    "sync_magicline_bookings",
    "sync_magicline_member_by_email",
]
