"""Externer Dead-Man's-Switch (healthchecks.io) für den Worker.

Warum das nicht mit Bordmitteln geht: Jeder Alarm dieses Systems — ntfy-Push,
Telegram, Mail, der Uptime-Cron — setzt voraus, dass der Server läuft. Stirbt die
Maschine, das Rechenzentrum oder nur der Worker-Container so, dass kein Prozess
mehr sendet, wird es **still**, und Stille sieht von außen exakt wie "alles in
Ordnung" aus. Genau das war der Ausfall vom 02.–05.09.2026: Fünf Tage lang keine
Zustellung, ohne dass jemand etwas merkte.

Die Umkehrung löst es: Der Worker meldet sich nach jedem erfolgreichen Zyklus bei
einem **fremden** Dienst. Bleibt die Meldung aus, alarmiert der — und zwar
unabhängig davon, was hier kaputt ist. Ausbleiben ist damit ein Signal, kein
Nichts.

Konfiguration: ``HEALTHCHECK_PING_URL`` (aus healthchecks.io). Ohne die Variable
tut dieses Modul nichts — kein Zwang, keine Fehler.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 8


def ping(settings, *, suffix: str = "", payload: str = "") -> bool:
    """Meldung an den Dead-Man's-Switch. Gibt True zurück, wenn sie ankam.

    ``suffix`` steuert die Semantik bei healthchecks.io:
      ``""``      Zyklus erfolgreich beendet  → Timer zurücksetzen
      ``"start"`` Zyklus beginnt              → Laufzeitmessung
      ``"fail"``  Zyklus fehlgeschlagen       → sofort alarmieren

    Schlägt der Ping selbst fehl (kein Internet, Dienst down), wird das nur
    geloggt: Der Worker darf an seiner eigenen Überwachung niemals scheitern.
    """
    # str() statt Vertrauen auf den Typ, und ALLES ab hier im try: Dieses Modul
    # darf unter keinen Umständen eine Exception in die Worker-Schleife tragen —
    # eine kaputte Überwachung, die den überwachten Dienst abschießt, wäre die
    # schlechteste denkbare Verschlechterung.
    url = str(getattr(settings, "healthcheck_ping_url", "") or "").strip()
    if not url:
        return False
    try:
        target = url.rstrip("/") + (f"/{suffix}" if suffix else "")
        import httpx
        resp = httpx.post(target, content=payload.encode("utf-8")[:10_000],
                          timeout=_TIMEOUT_SECONDS)
        if resp.status_code >= 400:
            logger.warning("deadman ping %s -> HTTP %s", suffix or "ok", resp.status_code)
            return False
        return True
    except Exception as exc:
        # Bewusst nur WARNING: ein fehlgeschlagener Ping ist ein Monitoring-
        # Problem, kein Betriebsproblem — er darf den Zyklus nicht beeinflussen.
        logger.warning("deadman ping %s failed: %s", suffix or "ok", exc)
        return False
