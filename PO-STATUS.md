# PO-STATUS · OPENGYM

## Phase
2 (Roadmap freigegeben, Umsetzung läuft)

## Fortschritt
62 % zur Produktionsreife (gewichtete Erfüllung mit Beleg; Update 05.08.2026 nach Freigabe-Umsetzung)
- Kernfunktionen 26/30 · Betrieb/Stabilität 8/15 · Sicherheit 10/15 · Backups 5/10 · Monitoring 7/10 · Tests/CI 8/10 · Doku 5/10

## Roadmap-Status
FREIGEGEBEN am 05.08.2026 durch Damien mit Anpassungen (Merge FF · .bak-Einzelfreigabe statt pauschal · pg_dump 14 Tage · CI pytest-only · NAS ruht). Zusätzliches Roadmap-Item „Ausfall-Detektor" eingeplant (M1, nicht ans Ende). Umsetzung der sofort freigegebenen Punkte am 05.08.2026 erfolgt (siehe Tageslog).

## Freigaben und Entscheidungen von Damien (05.08.2026)
1. Merge fix/opengym-access-window → main: Ja (Fast-Forward, kein Redeploy). ERLEDIGT.
2. .bak-Löschung korrigiert: nur datetime_utils.py.bak, .env.bak.smtp.20260711231837, docker-compose.yml.bak.guardian.20260710 löschen (permanent, untracked — bewusst). paperless/.env.bak.20260804120247 NICHT löschen (Rollback-Punkt laufende Paperless-Migration, erst nach Abnahme). ERLEDIGT.
3. pg_dump-Backup: Ja. Nächtlich nach /opt/getimpulse/backups/opengym, Retention 14 Tage, hohe Priorität. ERLEDIGT (Cron + Erstlauf verifiziert).
4. CI: Ja. GitHub Actions, nur pytest bei Push/PR, kein Deploy. ERLEDIGT.
5. Studio-Automations: ruhen bis NAS zurück (weiterhin offline). EINGEPLANT.
6. Zusatz-Item „Ausfall-Detektor" (eingefrorener Cloud↔Schloss-Sync): während Freezes nur stabile og-bh-Codes zustellen, Off-Peak fail-closed + Alert (Lockout-Risiko am 03.08.2026 live bestätigt). In M1 eingeplant. Implementiert als Commit 42872fb — aber Tür/Nuki/Rotation-Change → DEPLOY braucht einzelne Freigabe, läuft NICHT über die Roadmap-Pauschale.

## Offene Fragen
- Deploy-Freigabe für Commit 42872fb (Ausfall-Detektor, fail-closed): einzeln anfragen, mit Fallback-Zugang-Plan und Funktions-Check.

## ESKALIERT-Flags
keine

## Tageslog
- 05.08.2026 16:20 Watchdog-Erstlauf: Phase-0-Bestandsaufnahme abgeschlossen (siehe unten)
- 05.08.2026 16:25 ROADMAP.md-Entwurf + PO-STATUS.md angelegt, committet
- 05.08.2026 16:25 Einmalige Roadmap-Rückfragen (erlaubte Ausnahme) in Topic 37 gepostet
- 05.08.2026 16:48 Roadmap-Freigabe von Damien (mit 5 Anpassungen + Zusatz-Item Ausfall-Detektor)
- 05.08.2026 ~17:05 Umsetzung Freigabe-Punkte (Chaya, manuell statt Worker — Watchdog pausiert):
  - Merge FF main: 44b543d..42872fb (enthält auch Ausfall-Detektor-Commit), gepusht origin main + fix/opengym-access-window
  - .bak-Löschung: 3 freigegebene Dateien entfernt, paperless-Rollback-Punkt unangetastet
  - Backup: /opt/getimpulse/ops/opengym-backup/backup.sh + Cron 03:15; Erstlauf verifiziert (opengym-20260805-152159.sql.gz, 267 KB, 29 Tabellen, Trailer ok); Fehlerpfad sendet Alarm-Mail
  - Tests: Suite hermetisch gemacht (conftest force-set statt setdefault; Fix für .env-Leak NUKI_GUARDIAN_FALLBACK_INTERVAL_SECONDS) → 107 passed
  - CI: .github/workflows/tests.yml committet (d6cc0b6), pytest-only
  - ROADMAP.md: Status freigegeben, Item Ausfall-Detektor in M1, Checkboxen aktualisiert

## WORKER-ENDE
Kein PO-Worker gestartet — Freigabe-Umsetzung 05.08.2026 lief manuell (Watchdog-Job pausiert). Nächste Schritte (Kernpfade-Verifikation, Restore-Test, Healthchecks) können wieder über Worker laufen.

## Bestandsaufnahme (05.08.2026)

### Struktur & Repos
- Repo: DamienDrash/Twenty4Seven-Gym (GitHub via SSH) · Pfad /opt/getimpulse/opengym
- Branches: main (AKTIV, ausgecheckt, = 42872fb + d6cc0b6) · fix/opengym-access-window (gepusht, identisch bis d6cc0b6-Vorgänger) · fix/delivery-resilience-materialisation (nur lokal)
- main war 9 Commits hinter fix/opengym-access-window (Rotations-/Delivery-Hardening 27.07.–05.08.: worker crash-loop fix, fast-mode rotation, auto-reconcile, successor-guard, alert-flood fix, outage detector u. a.)
- 15 untracked .bak-Dateien in src/nuki_integration/ (Rest bleibt laut Damien) · gelöschte .env.bak-Dateien: 3 (05.08.2026)

### Betrieb
- Container opengym-service + opengym-worker: restart:always
- Gesteuert über /opt/getimpulse/docker-compose.yml (Compose-Projekt getimpulse), NICHT über die repo-eigene docker-compose.yml (twenty4seven-gym-* existieren nicht)
- Keine veröffentlichten Ports; https://getimpulse.de/opengym/app → 200, /checks → 200; kein /health-Endpoint (404)
- DB opengym auf db-service (Postgres 15.17 Alpine, getimpulse-network)

### Sicherheit
- Secrets in .env (600) bzw. /opt/getimpulse/.credentials/opengym_telegram.env (600, root, read-only gemountet); .gitignore schließt .env aus; keine Secrets im Repo
- Offen: Secrets-in-Git-Historie-Audit, Dependency-Updates (pip-audit)

### Backups
- Seit 05.08.2026: nächtlicher pg_dump 03:15 → /opt/getimpulse/backups/opengym (Retention 14 Tage, gzip, Trailer-Check, Alarm-Mail bei Fehler). Erstlauf verifiziert.
- Offen: Restore-Test, Backup-Alarm auf Telegram Topic 37

### Monitoring / Logging / Alerting
- Guardian-Alerts via Telegram (thread-aware), Auto-Dedup; Rotations-Check-Cron täglich 10:30 (/opt/getimpulse/ops/opengym-rotation-check/check.py)
- getimpulse-nas (Teilbereich Studio-Automations): OFFLINE (Ping + Port 443 fail) — wird weiter beobachtet, kein Blocker; Meldung nur bei Rückkehr

### Tests & CI
- Suite: 107 passed (05.08.2026, .venv-ci, Python 3.12.3); conftest hermetisch (env-force-set)
- CI: GitHub Actions Workflow tests.yml (pytest bei Push main/fix/** und PR, kein Deploy)

### Doku
- README.md + .env.example vorhanden und brauchbar; Betriebshandbuch fehlt; README-Sync-Intervall (30 min) gegen tatsächliche Konfiguration prüfen
