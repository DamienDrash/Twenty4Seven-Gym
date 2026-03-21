from __future__ import annotations

import json
import sys

from .config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.nuki_smartlock_id:
        print("ERROR: NUKI_SMARTLOCK_ID must be configured.", file=sys.stderr)
        sys.exit(1)
    print(
        json.dumps(
                {
                    "status": "not-implemented-in-phase-1",
                    "smartlockId": settings.nuki_smartlock_id,
                    "message": (
                        "Webhook setup is retained for compatibility but "
                        "not implemented in the new access-core slice."
                    ),
                },
                indent=2,
            )
    )
