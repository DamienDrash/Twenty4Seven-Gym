# ROADMAP · OPENGYM · Ziel: 100 % Produktionsreife

Status: ENTWURF (05.08.2026, Watchdog-Erstlauf) — wartet auf Freigabe durch Damien.
Gewichte nach PO-Bewertungsraster (Summe 100). Reihenfolge = Abarbeitungsreihenfolge.

SICHERHEITSKRITISCH (Besonderheiten): Änderungen an Tür-, Nuki- oder Code-Rotations-Logik
werden entwickelt und getestet, aber NIE ohne explizite Freigabe von Damien deployt.
Vor jedem Deploy Fallback-Zugang klären. Container-Neustarts kurz halten, danach
Funktions-Check (Codes gültig? Worker läuft?).

## M1 Kernfunktionen härten und nachweisen · Gewicht 30
- [ ] Test-Suite (pytest) vollständig laufen, Ergebnis im Tageslog protokollieren (Beleg)
- [ ] main auf Produktionsstand bringen: fix/opengym-access-window mergen (nur Repo-Hygiene, KEIN Redeploy)
- [ ] Kernpfade verifizieren: Buchungssperre 30 min, PIN-Versand nur für erste gebuchte Stunde, Rotation 101 Codes (5 innen / 96 außen), Sync-Intervall — Beleg je Pfad
- [ ] .bak-Dateien aufräumen (laut Entscheidung Damien, Frage 2)

## M2 Betrieb & Stabilität · Gewicht 15
- [ ] /health-Endpoint im FastAPI-App + Healthchecks für opengym-service und opengym-worker in /opt/getimpulse/docker-compose.yml (Änderung nur mit Funktions-Check danach)
- [ ] Docker-Log-Rotation (max-size/max-file) für beide Container
- [ ] Restart-Runbook dokumentieren und einmal real testen: Neustart kurz, danach Codes gültig + Worker aktiv

## M3 Sicherheit · Gewicht 15
- [ ] Secrets-Audit: Git-Historie auf Secrets prüfen; .env.bak-Dateien entfernen/archivieren (laut Frage 2)
- [ ] pip-audit / Dependency-Update (Updates nur mit Freigabe deployen)
- [ ] Oberflächen-Check mit Beleg: keine veröffentlichten Ports, nur getimpulse-network erreichbar

## M4 Backups & getesteter Restore · Gewicht 10
- [ ] Nächtlicher pg_dump der opengym-DB (Ziel/Retention laut Frage 3; Default: /opt/getimpulse/backups/opengym, 14 Tage)
- [ ] Restore-Test mit Nachweis (Einspiel in Test-DB, Stichprobenvergleich)
- [ ] .env-/Secrets-Sicherung außerhalb des Repos (mode 600)

## M5 Monitoring, Logging, Alerting · Gewicht 10
- [ ] Bestehendes Alerting verifizieren (Guardian, Rotations-Check-Cron 10:30) — Beleg im Tageslog
- [ ] Backup-Job ins Alerting aufnehmen (Fehler → Telegram Topic 37)
- [ ] Uptime-/Web-Check für /app und /checks einrichten

## M6 Tests & CI · Gewicht 10
- [ ] CI einrichten (laut Frage 4; Default: GitHub Actions, nur pytest bei Push/PR, kein Deploy)
- [ ] Nachweis: Kernpfade (Rotation, Versand, Buchungssperre) sind durch Tests abgedeckt

## M7 Doku · Gewicht 10
- [ ] Betriebshandbuch: Betrieb, Neustart, Backup/Restore, Fallback-Zugang, Eskalationsweg
- [ ] README aktualisieren (Sync-Intervall, tatsächliche Compose-Datei /opt/getimpulse/docker-compose.yml, Branch-Modell)
- [ ] .env.example vollständig und aktuell halten

## Zurückgestellt
- Teilbereich Studio-Automations (getimpulse-nas, aktuell OFFLINE): ruht bis zur Rückkehr des NAS (Frage 5; Default: ruhen lassen, nur Erreichbarkeit prüfen).
