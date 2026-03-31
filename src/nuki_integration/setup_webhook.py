"""Webhook setup stub — retained for CLI entry point compatibility."""
from __future__ import annotations
import json, sys
from .config import get_settings

def main() -> None:
    s = get_settings()
    print(json.dumps({"status": "not-implemented", "smartlockId": s.nuki_smartlock_id}, indent=2))
    sys.exit(0)
