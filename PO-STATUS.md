# PO-STATUS · OPENGYM

## Phase
1 (Roadmap-Entwurf erstellt, wartet auf Freigabe durch Damien)

## Fortschritt
54 % zur Produktionsreife (Bestandsaufnahme-Baseline 05.08.2026; gewichtete Erfüllung, Belegprüfung folgt nach Freigabe)
- Kernfunktionen 20/30 · Betrieb/Stabilität 8/15 · Sicherheit 10/15 · Backups 0/10 · Monitoring 7/10 · Tests/CI 4/10 · Doku 5/10

## Roadmap-Status
Entwurf erstellt am 05.08.2026 (Watchdog-Erstlauf). Rückfragen an Damien in Topic 37 gepostet. Noch NICHT freigegeben — keine Umsetzung vor Freigabe.

## Freigaben und Entscheidungen von Damien
(keine bisher)

## Offene Fragen
Fragen 1–5 vom 05.08.2026 (gepostet in Topic 37): Merge nach main · .bak-Aufräumen · DB-Backup · CI · NAS-Teilbereich

## ESKALIERT-Flags
keine

## Tageslog
- 05.08.2026 16:20 Watchdog-Erstlauf: Phase-0-Bestandsaufnahme abgeschlossen (siehe unten)
- 05.08.2026 16:25 ROADMAP.md-Entwurf + PO-STATUS.md angelegt, committet
- 05.08.2026 16:25 Einmalige Roadmap-Rückfragen (erlaubte Ausnahme) in Topic 37 gepostet

## WORKER-ENDE
Kein Worker gestartet — Erstlauf, Phase 1; Umsetzung beginnt erst nach Roadmap-Freigabe durch Damien.

## Bestandsaufnahme (05.08.2026)

### Struktur & Repos
- Repo: DamienDrash/Twenty4Seven-Gym (GitHub via SSH) · Pfad /opt/getimpulse/opengym
- Branches: main · fix/opengym-access-window (AKTIV, ausgecheckt; Prod-Image davon gebaut am 03.08. 16:25) · fix/delivery-resilience-materialisation (nur lokal)
- main liegt 8 Commits hinter fix/opengym-access-window (Rotations-/Delivery-Hardening 27.07.–03.08.: worker crash-loop fix, fast-mode rotation, auto-reconcile, successor-guard, alert-flood fix u. a.)
- 15+ untracked .bak-Dateien in src/nuki_integration/ · 2 .env.bak-Dateien (root, 600) im Repo-Verzeichnis

### Betrieb
- Container opengym-service + opengym-worker: Up 46 h, 0 Restarts, restart:always
- Gesteuert über /opt/getimpulse/docker-compose.yml (Compose-Projekt getimpulse), NICHT über die repo-eigene docker-compose.yml (twenty4seven-gym-* existieren nicht)
- Keine veröffentlichten Ports; https://getimpulse.de/opengym/app → 200, /checks → 200; kein /health-Endpoint (404)
- Logs letzte 6 h: keine Fehler in service/worker; Worker-Log JSON, ~0,5 MB/2 Tage (unkritisch)
- DB opengym auf db-service (Postgres 16, getimpulse-network)

### Sicherheit
- Secrets in .env (600) bzw. /opt/getimpulse/.credentials/opengym_telegram.env (600, root, read-only gemountet); .gitignore schließt .env aus; keine Secrets im Repo
- Offen: .bak-/Archivdateien aufräumen, Secrets-in-Git-Historie-Audit, Dependency-Updates (pip-audit)

### Backups
- KEINE gefunden: kein Backup-Verzeichnis, kein pg_dump-Cron für opengym → größter Gap (0/10)

### Monitoring / Logging / Alerting
- Guardian-Alerts via Telegram (thread-aware), Auto-Dedup; Rotations-Check-Cron täglich 10:30 (/opt/getimpulse/ops/opengym-rotation-check/check.py)
- getimpulse-nas (Teilbereich Studio-Automations): OFFLINE (Ping + Port 443 fail) — wird weiter beobachtet, kein Blocker; Meldung nur bei Rückkehr

### Tests & CI
- 13 Testdateien vorhanden (rotation, pin_pool, guardian, delivery_resilience, worker_cycle, business_hours u. a.); Suite-Lauf steht aus
- Kein CI (.github/workflows fehlt)

### Doku
- README.md + .env.example vorhanden und brauchbar; Betriebshandbuch fehlt; README-Sync-Intervall (30 min) gegen tatsächliche Konfiguration prüfen
