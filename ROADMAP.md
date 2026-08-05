# ROADMAP · OPENGYM · Ziel: 100 % Produktionsreife

Status: FREIGEGEBEN am 05.08.2026 durch Damien (mit Anpassungen, siehe Tageslog PO-STATUS.md).
Gewichte nach PO-Bewertungsraster (Summe 100). Reihenfolge = Abarbeitungsreihenfolge.

SICHERHEITSKRITISCH (Besonderheiten): Änderungen an Tür-, Nuki- oder Code-Rotations-Logik
werden entwickelt und getestet, aber NIE ohne explizite Freigabe von Damien deployt.
Vor jedem Deploy Fallback-Zugang klären. Container-Neustarts kurz halten, danach
Funktions-Check (Codes gültig? Worker läuft?).

## M1 Kernfunktionen härten und nachweisen · Gewicht 30
- [x] Test-Suite (pytest) vollständig laufen, Ergebnis im Tageslog protokollieren (Beleg: 107 passed, 05.08.2026)
- [x] main auf Produktionsstand bringen: fix/opengym-access-window mergen (Fast-Forward 44b543d..42872fb, KEIN Redeploy — laufende Container unverändert; origin gepusht)
- [x] .bak-Dateien aufräumen (Entscheidung Damien 05.08.2026: 3 Dateien gelöscht, paperless-Rollback-Punkt behalten)
- [ ] Kernpfade verifizieren: Buchungssperre 30 min, PIN-Versand nur für erste gebuchte Stunde, Rotation 101 Codes (5 innen / 96 außen), Sync-Intervall — Beleg je Pfad
- [ ] Ausfall-Detektor für eingefrorenen Cloud↔Schloss-Sync (Zusatz-Item Damien 05.08.2026):
      während eines Freezes nur stabile og-bh-Codes zustellen, Off-Peak-Codes fail-closed + Alert.
      Hintergrund: bei Router-Ausfall können frische Off-Peak-Codes fälschlich als gültig
      zugestellt werden, obwohl sie nie am Keypad ankommen (03.08.2026 live bestätigt → Lockout-Risiko).
      Stand: implementiert + getestet als Commit 42872fb (NUKI_REQUIRE_DEVICE_CONFIRMATION,
      default True = fail-closed; Legacy-Pfad über false). DEPLOY NOCH OFFEN: Tür/Nuki/Rotation-Change
      → braucht einzelne Freigabe von Damien, läuft NICHT über die Roadmap-Pauschale.
      Vor Deploy: Fallback-Zugang klären, danach Funktions-Check.

## M2 Betrieb & Stabilität · Gewicht 15
- [ ] /health-Endpoint im FastAPI-App + Healthchecks für opengym-service und opengym-worker in /opt/getimpulse/docker-compose.yml (Änderung nur mit Funktions-Check danach)
- [ ] Docker-Log-Rotation (max-size/max-file) für beide Container
- [ ] Restart-Runbook dokumentieren und einmal real testen: Neustart kurz, danach Codes gültig + Worker aktiv

## M3 Sicherheit · Gewicht 15
- [ ] Secrets-Audit: Git-Historie auf Secrets prüfen (Rest-.bak-Dateien im Repo-Verzeichnis laut Damien behalten, außer den 3 gelöschten)
- [ ] pip-audit / Dependency-Update (Updates nur mit Freigabe deployen)
- [ ] Oberflächen-Check mit Beleg: keine veröffentlichten Ports, nur getimpulse-network erreichbar

## M4 Backups & getesteter Restore · Gewicht 10
- [x] Nächtlicher pg_dump der opengym-DB → /opt/getimpulse/backups/opengym, Retention 14 Tage (Cron 03:15, Erstlauf verifiziert 05.08.2026: 267 KB, 29 Tabellen)
- [ ] Restore-Test mit Nachweis (Einspiel in Test-DB, Stichprobenvergleich)
- [ ] .env-/Secrets-Sicherung außerhalb des Repos (mode 600)
- [ ] Backup-Fehler-Alarm auf Telegram Topic 37 umstellen (aktuell: Mail an dfrigewski@gmail.com)

## M5 Monitoring, Logging, Alerting · Gewicht 10
- [ ] Bestehendes Alerting verifizieren (Guardian, Rotations-Check-Cron 10:30) — Beleg im Tageslog
- [ ] Backup-Job ins Alerting aufnehmen (Fehler → Telegram Topic 37)
- [ ] Uptime-/Web-Check für /app und /checks einrichten

## M6 Tests & CI · Gewicht 10
- [x] CI einrichten: GitHub Actions, nur pytest bei Push/PR, kein Deploy (Workflow .github/workflows/tests.yml, Commit d6cc0b6)
- [ ] Nachweis: Kernpfade (Rotation, Versand, Buchungssperre) sind durch Tests abgedeckt

## M7 Doku · Gewicht 10
- [ ] Betriebshandbuch: Betrieb, Neustart, Backup/Restore, Fallback-Zugang, Eskalationsweg
- [ ] README aktualisieren (Sync-Intervall, tatsächliche Compose-Datei /opt/getimpulse/docker-compose.yml, Branch-Modell)
- [ ] .env.example vollständig und aktuell halten

## Zurückgestellt
- Teilbereich Studio-Automations (getimpulse-nas, aktuell OFFLINE): ruht bis zur Rückkehr des NAS (Entscheidung Damien 05.08.2026: ruhen lassen, nur Erreichbarkeit prüfen).
