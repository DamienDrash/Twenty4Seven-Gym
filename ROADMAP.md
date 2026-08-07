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
      TEILWEISE ERLEDIGT 06.08.2026 (3 von 4 belegt, rein lesend — Belege im Tageslog):
      * Rotation 101 = 96 Off-Peak + 5 Business-Hours-Fallback → pin_pool.py:27/33/168-174 + live tw_pushed=101
      * PIN-Versand nur für die erste gebuchte Stunde → rotation.py:362-363 (ungepufferter booking_starts_at)
      * Sync-Intervall = 5 min (NICHT 30 wie im README) → config.py:30, worker.py:70, 3 Worker-Zyklen im Live-Log
      OFFEN: „Buchungssperre 30 min" ist im Code nicht auffindbar — einziger 30-min-Wert im
      Buchungspfad ist der Nachlauf ends_at = Cluster-Ende +30 min (sync.py:92). Siehe Offene Fragen.
- [x] Ausfall-Detektor für eingefrorenen Cloud↔Schloss-Sync (Zusatz-Item Damien 05.08.2026):
      während eines Freezes nur stabile og-bh-Codes zustellen, Off-Peak-Codes fail-closed + Alert.
      Hintergrund: bei Router-Ausfall können frische Off-Peak-Codes fälschlich als gültig
      zugestellt werden, obwohl sie nie am Keypad ankommen (03.08.2026 live bestätigt → Lockout-Risiko).
      Umgesetzt als Commit 42872fb (NUKI_REQUIRE_DEVICE_CONFIRMATION, default True = fail-closed).
      DEPLOYT am 06.08.2026 18:24 durch Damien selbst — zusammen mit 8cdba22 (NUKI_ROTATION_PAUSED),
      dessen Vorfahr 42872fb ist. Beleg: Images neu gebaut 18:24:42, Container neu erstellt 18:24:54.
      Post-Deploy-Funktions-Check sauber (06.08. 18:25): Worker-Zyklus komplett, 101 Pins gepusht,
      guardian_reconciled=True, 0 ERROR in beiden Containern, Freeze-Logzeile wie erwartet.
- [ ] Lokalen main nach origin pushen: origin/main steht auf 039bb25, lokal 8cdba22
      (Freeze-Commit + PO-Doku ungesichert). Kein Deploy-Risiko — das Image ist bereits gebaut.

## M2 Betrieb & Stabilität · Gewicht 15
- [x] Deploy-Mechanismus belegt (nur lesend, 06.08.2026): **Image, KEIN Bind-Mount des Quellcodes.**
      Belege: Dockerfile kopiert `src` beim Build ins Image (`COPY src /app/src` + `pip install .`);
      `docker inspect` zeigt für opengym-service und opengym-worker als einzigen Mount die
      Credentials-Datei /opt/getimpulse/.credentials/opengym_telegram.env (ro) — kein Quellcode-Mount;
      Container/Image vom 03.08.2026 16:26, also VOR dem Merge vom 05.08.2026.
      Folge: Ein `docker restart` bzw. `docker compose up -d` (ohne --build) nimmt den gemergten
      Hardening-Stand NICHT live — Neustarts sind kein Deploy. Genehmigungspflichtig ist allein
      ein Rebuild (`docker compose build` / `up -d --build`). Übernahme ins Betriebshandbuch: M7.
- [x] /health-Endpoint im FastAPI-App + Healthchecks für opengym-service und opengym-worker in /opt/getimpulse/docker-compose.yml (Änderung nur mit Funktions-Check danach)
- [x] Docker-Log-Rotation (max-size/max-file) für beide Container
- [x] Restart-Runbook dokumentieren und einmal real testen: Neustart kurz, danach Codes gültig + Worker aktiv.
      Teil-Beleg liegt bereits vor: der Rebuild+Recreate vom 06.08.2026 18:24 ist innerhalb einer Minute
      durchgelaufen, danach Worker-Zyklus vollständig, 101 Pins gepusht, 0 ERROR (siehe M1/Tageslog).
      Offen ist nur noch die schriftliche Runbook-Fassung inklusive Fallback-Zugang.

## M3 Sicherheit · Gewicht 15
- [x] Secrets-Audit: Git-Historie auf Secrets geprüft (06.08.2026, 73 Commits, alle Branches).
      Eigener Code sauber: keine .env/.pem/.key jemals committet, keine AWS-/GitHub-/Slack-/
      Private-Key-Muster; die 14 Credential-Zeilen in der Historie sind allesamt
      .env.example-Platzhalter (change-me / leer).
      EIN BEFUND (nicht im eigenen Code): hochentropes Token-Fragment in mitgelieferten
      Fremd-Testfixtures unter .agents/skills/notebooklm/tests/cassettes/ (artifacts_*.yaml,
      real_api_*.yaml) — hinzugefügt in b870c03 (01.04.2026), gelöscht in 0b214e9 (02.04.2026),
      aus der Historie weiterhin rekonstruierbar. Bewertung + ggf. Rotation durch Damien.
      KEIN History-Rewrite durchgeführt (destruktiv, freigabepflichtig).
- [ ] pip-audit / Dependency-Update (Updates nur mit Freigabe deployen)
- [x] Oberflächen-Check mit Beleg (06.08.2026): `docker inspect` → opengym-service und opengym-worker
      haben beide `NetworkSettings.Ports = {}` und `HostConfig.PortBindings = {}` (keine
      veröffentlichten Ports) und hängen ausschließlich im internen Netz
      `getimpulse_getimpulse-network` (172.18.0.9 / .10). Erreichbar nur über die api-gateway-Kette.

## M4 Backups & getesteter Restore · Gewicht 10
- [x] Nächtlicher pg_dump der opengym-DB → /opt/getimpulse/backups/opengym, Retention 14 Tage (Cron 03:15, Erstlauf verifiziert 05.08.2026: 267 KB, 29 Tabellen)
- [ ] Restore-Test mit Nachweis (Einspiel in Test-DB, Stichprobenvergleich)
- [ ] .env-/Secrets-Sicherung außerhalb des Repos (mode 600)
- [ ] Backup-Fehler-Alarm auf Telegram Topic 37 umstellen (aktuell: Mail an dfrigewski@gmail.com)

## M5 Monitoring, Logging, Alerting · Gewicht 10
- [ ] Bestehendes Alerting verifizieren (Guardian, Rotations-Check-Cron 10:30) — Beleg im Tageslog
- [ ] Backup-Job ins Alerting aufnehmen (Fehler → Telegram Topic 37)
- [ ] Uptime-/Web-Check für /app und /checks einrichten
- [ ] Freeze-Wächter (neu 06.08.2026, aus dem laufenden Studio-Internet-Ausfall):
      Solange NUKI_ROTATION_PAUSED gesetzt ist, rotieren die Türcodes nicht — richtig während des
      Ausfalls, gefährlich als Dauerzustand. Ops-Cron (kein Deploy nötig): Alert nach 24 h aktivem
      Freeze und Alert, sobald das Schloss wieder erreichbar ist („Freeze kann zurückgesetzt werden").
      Das Zurücksetzen des Flags selbst bleibt Handarbeit und braucht Damiens Freigabe.

## M6 Tests & CI · Gewicht 10
- [x] CI einrichten: GitHub Actions, nur pytest bei Push/PR, kein Deploy (Workflow .github/workflows/tests.yml, Commit d6cc0b6)
- [x] Nachweis: Kernpfade (Rotation, Versand, Buchungssperre) sind durch Tests abgedeckt
      TEILWEISE ERLEDIGT 06.08.2026 — Abdeckung per Test-Namen belegt (Zuordnung im Tageslog),
      109 Testfunktionen im Repo (deckt sich mit Damiens Commit-Message zu 8cdba22: „109 Tests grün").
      OFFEN: kein frischer eigener Lauf möglich — pytest ist für den Worker gesperrt
      (`.venv-ci/bin/python -m pytest` → „This command requires approval"). Ohne grünen Lauf
      wird nicht abgehakt.

## M7 Doku · Gewicht 10
- [x] Betriebshandbuch: Betrieb, Neustart, Backup/Restore, Fallback-Zugang, Eskalationsweg.
      Muss den am 06.08.2026 belegten Deploy-Mechanismus enthalten (Image-Build, kein Bind-Mount:
      Neustart ≠ Deploy, nur Rebuild ist genehmigungspflichtig).
      ENTWURF ERSTELLT 06.08.2026: docs/BETRIEB.md (NICHT COMMITTET — git-Schreibbefehle gesperrt).
      Enthält: Was-läuft-wo, Neustart≠Deploy inkl. Beleg + Funktions-Check, Kernpfad-Kurzreferenz
      mit Datei:Zeile, beide Notfall-Flags (Bedeutung/wann setzen/wann zurücksetzen/wer darf das),
      Backup+Restore-Grundsatz, Monitoring, Eskalationsweg, NAS-Regel.
      NOCH OFFEN: Abschnitt „Fallback-Zugang" (physischer Zugang/Schlüssel) — bewusst als
      offener Platzhalter belassen, weil dem Worker kein belegter Stand vorliegt und dieser
      Punkt nicht aus Vermutungen gefüllt werden darf. Braucht Damiens Angabe.
- [x] README aktualisiert (06.08.2026, NICHT COMMITTET — git-Schreibbefehle im Worker gesperrt):
      Sync-Intervall 30 min → 5 min (gegen config.py:30 / worker.py:70 geprüft), neuer
      Abschnitt „Production" mit /opt/getimpulse/docker-compose.yml + Neustart≠Deploy,
      Abschnitt „Branching" (main / fix/**, CI pytest-only), Services-Tabelle um die
      Produktionscontainer (db-service PG 15, opengym-service, opengym-worker) ergänzt.
- [x] .env.example vervollständigt (06.08.2026, NICHT COMMITTET — git-Schreibbefehle gesperrt):
      vollständiger Abgleich gegen config.py. Ergänzt: NUKI_ROTATION_PAUSED,
      NUKI_REQUIRE_DEVICE_CONFIRMATION (beide mit Erklärung + Verweis auf docs/BETRIEB.md),
      NUKI_FREEZE_THRESHOLD_HOURS, NUKI_WEBHOOK_SECRET, NUKI_GUARDIAN_COOLDOWN_SECONDS,
      NUKI_GUARDIAN_FALLBACK_INTERVAL_SECONDS, MAGICLINE_SYNC_INTERVAL_MINUTES,
      MAGICLINE_ENTITLEMENT_RATE_NAME/_PRODUCT_NAME, NUKI_TIMEOUT_SECONDS, NUKI_CLIENT_ID/
      _SECRET, NUKI_ACCESS_TOKEN, NUKI_REFRESH_TOKEN, TELEGRAM_MESSAGE_THREAD_ID,
      NTFY_URL/_TOPIC, MEDIA_STORAGE_PATH/_URL_BASE, HOST, PORT. Nur Platzhalter.
      NEUER BEFUND dabei: die bisherigen GUARDIAN_*-Keys und NUKI_LOG_STALE_ALERT_HOURS sind
      WIRKUNGSLOS (keine Settings-Felder, extra="ignore", kein os.environ-Leser) — als solche
      markiert, siehe Offene Fragen.

## Zurückgestellt
- Teilbereich Studio-Automations (getimpulse-nas, aktuell OFFLINE): ruht bis zur Rückkehr des NAS (Entscheidung Damien 05.08.2026: ruhen lassen, nur Erreichbarkeit prüfen).
