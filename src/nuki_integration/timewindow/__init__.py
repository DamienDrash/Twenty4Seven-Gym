"""Zeitfenster-PIN-Modell für die OpenGym-App (M2b).

Portiert aus der getesteten Referenz ``backend/services/nuki_pin``: 96 feste
Keypad-Slots (4 Codes je Off-Peak-Stunde), tägliche PIN-Rotation, Anti-Repeat
Tiefe 4. Ersetzt das per-Buchung-Provisioning des Workers.

STRIKT DRY-RUN: solange ``NUKI_DRY_RUN`` gesetzt ist, werden keine Live-Nuki-
Pushes ausgeführt; ohne SMTP werden keine Mails versandt.
"""
from . import pin_pool, store, rotation

__all__ = ["pin_pool", "store", "rotation"]
