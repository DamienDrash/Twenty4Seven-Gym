from __future__ import annotations
from functools import lru_cache
from fastapi import Depends, Header, HTTPException, status
from .auth import decode_token
from .config import Settings, get_settings
from .db import Database
from .exceptions import AuthenticationError
from .models import UserRecord


@lru_cache(maxsize=1)
def get_database() -> Database:
    settings = get_settings()
    db = Database(settings.database_url)
    db.open()
    return db


def get_runtime_settings() -> Settings:
    return get_settings()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Database = Depends(get_database),
    rs: Settings = Depends(get_runtime_settings),
) -> UserRecord:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_token(token, rs.jwt_secret)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    user = db.get_user_by_email(payload["sub"])
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive.")
    return UserRecord(id=user["id"], email=user["email"], role=user["role"], is_active=user["is_active"])


def require_admin(current_user: UserRecord = Depends(get_current_user)) -> UserRecord:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user

