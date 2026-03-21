from __future__ import annotations

from functools import lru_cache

from .config import Settings, get_settings
from .db import Database


@lru_cache(maxsize=1)
def get_database() -> Database:
    settings = get_settings()
    db = Database(settings.database_url)
    db.open()
    return db


def get_runtime_settings() -> Settings:
    return get_settings()
