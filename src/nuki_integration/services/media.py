"""Media file storage and URL resolution."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Settings

if TYPE_CHECKING:
    from fastapi import UploadFile


def get_media_url(settings: Settings, filename: str) -> str:
    base = settings.media_url_base.rstrip("/")
    return f"{base}/{filename.lstrip('/')}"


def save_media_file(settings: Settings, upload: UploadFile) -> str:
    dest_dir = Path(settings.media_storage_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "file").suffix
    filename = f"{secrets.token_hex(12)}{suffix}"
    dest_path = dest_dir / filename
    with dest_path.open("wb") as buffer:
        buffer.write(upload.file.read())
    return filename
