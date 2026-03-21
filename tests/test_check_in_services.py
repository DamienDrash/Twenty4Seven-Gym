# ruff: noqa: S101, S106

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from nuki_integration.services import (
    build_check_in_link,
    decode_check_in_token,
    generate_qr_data_uri,
    issue_check_in_token,
)


def test_check_in_token_roundtrip() -> None:
    settings = SimpleNamespace(jwt_secret="test-secret")
    token = issue_check_in_token(access_window_id=41, settings=settings, ttl_seconds=3600)
    assert decode_check_in_token(token=token, settings=settings) == 41


def test_build_check_in_link_contains_public_route() -> None:
    settings = SimpleNamespace(
        jwt_secret="test-secret",
        app_public_base_url="https://services.frigew.ski/opengym",
    )
    link = build_check_in_link(
        access_window_id=99,
        ends_at=datetime.now(UTC) + timedelta(hours=2),
        settings=settings,
    )
    assert link.startswith("https://services.frigew.ski/opengym/check-in?token=")


def test_generate_qr_data_uri_returns_svg_data() -> None:
    uri = generate_qr_data_uri("https://services.frigew.ski/opengym/check-in")
    assert uri.startswith("data:image/svg+xml;base64,")
