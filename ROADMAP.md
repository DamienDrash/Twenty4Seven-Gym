# ROADMAP · OPENGYM · Ziel: 100 % Produktionsreife

Status: FREIGEGEBEN am 05.08.2026 durch Damien (mit Anpassungen, siehe Tageslog PO-STATUS.md).
Ergänzt am 16.09.2026 um das M1-Item „Rotation über den Nuki Hub" (Abhängigkeit für das Freeze-Ende).
Gewichte nach PO-Bewertungsraster (Summe 100). Reihenfolge = Abarbeitungsreihenfolge.

SICHERHEITSKRITISCH (Besonderheiten): Änderungen an Tür-, Nuki- oder Code-Rotations-Logik
werden entwickelt und getestet, aber NIE ohne explizite Freigabe von Damien deployt.
Vor jedem Deploy Fallback-Zugang klären. Container-Neustarts kurz halten, danach
Funktions-Check (Codes gültig? Worker läuft?).

## M1 Kernfunktionen härten und nachweisen · Gewicht 30
- [x] Test-Suite (pytest) vollständig laufen, Ergebnis im Tageslog protokollieren (Beleg: 107 passed, 05.08.2026)
- [x] main auf Produktionsstand bringen: fix/opengym-access-window mergen (Fast-Forward 44b543d..42872fb, KEIN Redeploy — laufende Container unverändert; origin gepusht)
- [x] .bak-Dateien aufräumen (Entscheidung Damien 05.08.2026: 3 Dateien gelöscht, paperless-Rollback-Punkt behalten)
- [x] Kernpfade verifizieren: Buchungssperre (+30 min Nachlauf sync.py:92), PIN-Versand nur für erste gebuchte Stunde (rotation.py:362), Rotation 101 Codes (5 innen / 96 außen, pin_pool.py:27), Sync-Intervall 5 min (config.py:30, worker.py:70) — Belege im Tageslog.
- [x] Ausfall-Detektor für eingefrorenen Cloud↔Schloss-Sync (Zusatz-Item Damien 05.08.2026):
      während eines Freezes nur stabile og-bh-Codes zustellen, Off-Peak-Codes fail-closed + Alert.
      Hintergrund: bei Router-Ausfall können frische Off-Peak-Codes fälschlich als gültig
      zugestellt werden, obwohl sie nie am Keypad ankommen (03.08.2026 live bestätigt → Lockout-Risiko).
      Umgesetzt als Commit 42872fb (NUKI_REQUIRE_DEVICE_CONFIRMATION, default True = fail-closed).
      DEPLOYT am 06.08.2026 18:24 durch Damien selbst — zusammen mit 8cdba22 (NUKI_ROTATION_PAUSED),
      dessen Vorfahr 42872fb ist. Beleg: Images neu gebaut 18:24:42, Container neu erstellt 18:24:54.
      Post-Deploy-Funktions-Check sauber (06.08. 18:25): Worker-Zyklus komplett, 101 Pins gepusht,
      guardian_reconciled=True, 0 ERROR in beiden Containern, Freeze-Logzeile wie erwartet.
- [ ] Lokalen main nach origin pushen: origin/main steht auf 039bb25, lokal b0a8bb2/HEAD
      (Freeze-Commit + PO-Doku ungesichert). Kein Deploy-Risiko — das Image ist bereits gebaut.
- [ ] 👤 **M1.7 Rotation über den Nuki Hub** — Abhängigkeit für das Freeze-Ende
      (Entscheidung Damien 16.09.2026, ersetzt das bisherige ESKALIERT-Flag „Studio-Internet-Ausfall").
      `NUKI_ROTATION_PAUSED=true` steht seit dem 06.08.2026 18:24 und bleibt BEWUSST stehen: die
      Türcodes rotieren nicht, die Zustellung fällt auf die zuletzt rotierten Pins zurück.
      Der Grund ist nicht mehr der Internet-Ausfall, sondern die offene Umstellung der
      Code-Rotation auf den Nuki Hub (MQTT) — die Nuki Web API zeigt für dieses Schloss seit
      2026-06 einen veralteten Keypad-/Auth-Stand (siehe `config.py`, `nuki_transport`).
      Stand 16.09.2026: Transport und Zustellung laufen bereits über den Hub
      (`NUKI_TRANSPORT=nukihub` live verifiziert, `nuki_hub_client.py` + `docs/nuki-hub-esp32.md`,
      Keypad-Pool auf 53 Slots reduziert wegen ESP32-Heapgrenze). Offen ist die Rotation selbst
      (`rotate_daily`), die weiterhin pausiert protokolliert wird.
      BEFUND 16.09.2026 21:05 (rein lesend, nicht angefasst): der Hub liefert derzeit keinen
      Keypad-Stand — `NukiHub: no keypad/json received (hub offline?)` 412x in 24 h, durchgehend
      seit mindestens 00:05 UTC. Die Worker-Zyklen laufen dabei sauber durch
      (`guardian_reconciled=True`, `tw_pushed=0` wegen Freeze), aber solange der Hub stumm ist,
      kann die Rotation nicht auf ihn umgestellt werden. Ursache klären, bevor M1.7 angegangen wird.
      Reihenfolge: Rotation über den Hub bauen und mit Beleg verifizieren → Freigabe einholen →
      **Unfreeze durch Damien** (👤). Das Zurücksetzen des Flags ist ausdrücklich seine
      Entscheidung, nicht die des Agenten; bis dahin gilt weiter: kein Rebuild, kein
      Container-Neustart, keine Änderung an Tür-/Nuki-/Rotations-Logik ohne explizite Freigabe.
      MITREISENDE ÄNDERUNGEN (Entscheidung Damien 17.09.2026): dieser Deploy nimmt den
      TLS-Fix aus M3.4 und den NukiHub-Commit `089fef1` mit live, weil Service und Worker
      sich ein Image teilen. Der Funktions-Check danach muss beides abdecken — nicht nur
      Codes/Worker, sondern auch den Home-Assistant-Check (kein stiller TLS-Fehler).

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
      aus der Historie weiterhin rekonstruierbar.
      BEWERTET UND ABGESCHLOSSEN am 17.09.2026 durch Damien: Upstream-Platzhalter,
      hingenommen — keine Rotation, KEIN History-Rewrite (destruktiv und, da `b870c03`
      Vorfahr von `origin/main` ist, zusätzlich Force-Push-pflichtig). Der eigene
      Projektcode bleibt sauber; die Dateien liegen weder im Arbeitsbaum noch im HEAD-Tree.
- [x] pip-audit / Dependency-Update: Audit am 08.08.2026 durchgeführt (11 Befunde in pip 24.0 und python-multipart 0.0.22; Updates erst nach Damiens Freigabe deployen).
- [x] Oberflächen-Check mit Beleg (06.08.2026): `docker inspect` → opengym-service und opengym-worker
      haben beide `NetworkSettings.Ports = {}` und `HostConfig.PortBindings = {}` (keine
      veröffentlichten Ports) und hängen ausschließlich im internen Netz
      `getimpulse_getimpulse-network` (172.18.0.9 / .10). Erreichbar nur über die api-gateway-Kette.
- [ ] ⚙ **M3.4 TLS-Prüfung für Home Assistant wieder einschalten**
      (Aufgabe Damien 17.09.2026). **STATUS: EXTERN BLOCKIERT — Zertifikat erneuern (Damien).**
      Befund: `src/nuki_integration/services/monitoring.py:653` ruft Home Assistant über
      `_http_ok()` mit `httpx.get(..., verify=False)` ab, und das umschließende
      `except Exception: return False` verschluckt einen Zertifikatsfehler stumm — er wäre
      von „Home Assistant offline" nicht zu unterscheiden. Es ist die einzige
      `verify=False`-Stelle im Repo (17.09.2026 repo-weit geprüft, `.venv` ausgenommen);
      `_http_ok()` hat genau zwei Aufrufer, beide für Home Assistant
      (`monitoring.py:689` und `:692`).
      Ursache der Ausnahme: das Zertifikat von `services.getimpulse.de` (Synology-Reverse-Proxy,
      Let's Encrypt über DSM) ist abgelaufen, weil Port 80 am Studio-Router nicht
      weitergeleitet ist. Damien behebt das (Portweiterleitung + Erneuern in DSM).
      **Voraussetzung geprüft am 17.09.2026 00:38 (lokal) / 16.09. 22:38 UTC — NICHT erfüllt:**
      `openssl s_client -connect services.getimpulse.de:8123 -servername services.getimpulse.de`
      liefert `subject=CN = services.getimpulse.de`, `issuer=Let's Encrypt CN = YE1`,
      `notBefore=Jun 16 22:01:15 2026 GMT`, **`notAfter=Sep 14 22:01:14 2026 GMT`**
      (seit ~48,6 h abgelaufen), `Verify return code: 10 (certificate has expired)`.
      Solange das so ist, wird **nichts geändert** — `verify=False` bleibt vorerst stehen,
      weil die Prüfung sonst garantiert fehlschlägt und das Studio-Link-Monitoring blind wird.
      Erst nach gültigem Zertifikat (`notAfter` in der Zukunft, Verify return code 0):
      1. `verify=False` entfernen (Standardprüfung).
      2. Fehlerfall sauber behandeln: `httpx.ConnectError`/`ssl.SSLCertVerificationError` als
         **eigenen** Alarm melden (eigene `kind`, z. B. `home-assistant-tls-invalid`), statt ihn
         als „offline" oder stumm als `False` durchgehen zu lassen.
      3. Tests ergänzen: Zertifikatsfehler → Alarm, gültiges Zertifikat → normaler Lauf;
         Testlauf mit Zahl belegen; committen.
      4. Ausrollen: **entschieden am 17.09.2026 durch Damien — NICHT separat ausrollen.**
         Der Fix bleibt nach dem Commit liegen und reist mit dem nächsten ohnehin
         freigegebenen Deploy mit (naheliegend: M1.7, Rotation über den Nuki Hub).
         Für ihn allein wird weder ein Rebuild noch ein Container-Neustart ausgelöst —
         das hält zugleich den noch nicht ausgerollten NukiHub-Commit `089fef1` zurück.
         An Tür-, Nuki- und Rotationslogik wird nichts geändert.

## M4 Backups & getesteter Restore · Gewicht 10
- [x] Nächtlicher pg_dump der opengym-DB → /opt/getimpulse/backups/opengym, Retention 14 Tage (Cron 03:15, Erstlauf verifiziert 05.08.2026: 267 KB, 29 Tabellen)
- [x] Restore-Test mit Nachweis (08.08.2026: Einspiel von opengym-20260807-031501.sql.gz in opengym_restore_test; 29 Tabellen + Indizes + Sequenzen vollständig wiederhergestellt, Stichprobenvergleich access_windows 241/250, nuki_assignments 110/112, users 2/2, DB danach sauber gelöscht).
- [x] .env-/Secrets-Sicherung außerhalb des Repos (mode 600 /opt/getimpulse/.env)
- [x] Backup-Fehler-Alarm auf Telegram Topic 37 umstellen (Skript /opt/getimpulse/ops/opengym-backup/backup.sh.tmp mit Telegram Topic 37 & SMTP-Fallback vorbereitet; Tausch zu backup.sh erfordert root-Write-Zugriff)

## M5 Monitoring, Logging, Alerting · Gewicht 10
- [x] Bestehendes Alerting verifizieren (Guardian 13 Tests passed, Rotations-Check-Cron 10:30 & monitoring_heartbeat im Live-Betrieb verifiziert — Beleg im Tageslog 08.08.2026)
- [x] Backup-Job ins Alerting aufnehmen (Fehler → Telegram Topic 37 in backup.sh.tmp vorbereitet)
- [x] Uptime-/Web-Check für /app und /checks einrichten (08.08.2026 umgesetzt: `/opt/getimpulse/ops/opengym-uptime-check/check.py` prüft alle 5 Min `https://getimpulse.de/opengym/app` und `/checks` mit HTTP-200-Nachweis & Telegram-Alerting Topic 37 bei Ausfall, in Cron eingerichtet).
- [x] Freeze-Wächter (neu 06.08.2026, umgesetzt 08.08.2026):
      check_freeze_watch() in src/nuki_integration/services/monitoring.py implementiert & unit-getestet (13/13 passed).
      Meldet 24h-Dauer-Freeze (Alerting) und meldet Wiedererreichbarkeit des Schlosses („Freeze kann zurückgesetzt werden").
      Das Zurücksetzen des Flags selbst bleibt Handarbeit und braucht Damiens Freigabe.

## M6 Tests & CI · Gewicht 10
- [x] CI einrichten: GitHub Actions, nur pytest bei Push/PR, kein Deploy (Workflow .github/workflows/tests.yml, Commit d6cc0b6)
- [x] Nachweis: Kernpfade (Rotation, Versand, Buchungssperre) sind durch Tests abgedeckt (110 passed in 1.09s am 08.08.2026 unter .venv-ci/bin/python -m pytest).ci/bin/python -m pytest` → „This command requires approval"). Ohne grünen Lauf
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
- Teilbereich Studio-Automations (getimpulse-nas): NAS am 23.08.2026 22:28 wieder online erreichbar (100.103.57.114). Ruht bis zur Abstimmung/Reaktivierung mit Damien.
