from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from .exceptions import AuthenticationError

_PBKDF2_ROUNDS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ROUNDS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _, rounds_raw, salt, expected = password_hash.split("$", 3)
        rounds = int(rounds_raw)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds)
    return hmac.compare_digest(digest.hex(), expected)


def issue_token(*, subject: str, role: str, secret: str, ttl_seconds: int = 3600) -> str:
    payload = {"sub": subject, "role": role, "exp": int(time.time()) + ttl_seconds}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def decode_token(token: str, secret: str) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise AuthenticationError("Malformed token.") from exc
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise AuthenticationError("Invalid token signature.")
    payload = json.loads(base64.urlsafe_b64decode(body.encode("utf-8")))
    if int(payload["exp"]) < int(time.time()):
        raise AuthenticationError("Token expired.")
    return payload
