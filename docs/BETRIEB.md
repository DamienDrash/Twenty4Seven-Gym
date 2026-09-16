# Betriebshandbuch · OPENGYM

Stand: 06.09.2026. Gilt für die Produktionsinstanz auf dem getimpulse-Server.

> **SICHERHEITSKRITISCH.** Dieses System steuert physischen Zugang zum Studio.
> Änderungen an Tür-, Nuki- oder Code-Rotations-Logik werden entwickelt und getestet,
> aber **nie ohne explizite Freigabe von Damien deployt**. Vor jedem Deploy den
> Fallback-Zugang klären (siehe [Fallback-Zugang](#fallback-zugang)).

---

## 1. Was wo läuft

| Komponente | Container | Rolle |
|---|---|---|
| API + Admin-UI | `opengym-service` | FastAPI auf Port 8080 (containerintern), Admin `/app`, Member `/checks` |
| Hintergrund-Worker | `opengym-worker` | Magicline-Sync, Rotation, Zustellung, Wächter |
| Datenbank | `db-service` | PostgreSQL, Datenbank `opengym` |

**Gesteuert wird der Stack von `/opt/getimpulse/docker-compose.yml`** (Compose-Projekt
`getimpulse`) — **nicht** von der repo-eigenen `docker-compose.yml`. Container namens
`twenty4seven-gym-*` existieren nicht.

Beide Container laufen mit `restart: always` und haben **keine veröffentlichten Ports**
(`Ports {}` / `PortBindings {}`); erreichbar sind sie ausschließlich über die
api-gateway-Kette im internen Netz `getimpulse_getimpulse-network`.

Einziger Bind-Mount: `/opt/getimpulse/.credentials/opengym_telegram.env` (read-only,
mode 600, root) — die Telegram-Credentials landen so nie in der `docker-compose.yml`
und nie im Log.

---

## 2. Neustart ≠ Deploy

Das ist die wichtigste Betriebsregel und sie ist belegt (06.08.2026, rein lesend):

- Das `Dockerfile` **kopiert den Quellcode beim Build ins Image** (`COPY src /app/src`
  + `pip install .`).
- `docker inspect` zeigt für `opengym-service` und `opengym-worker` als einzigen Mount
  die Credentials-Datei — **es gibt keinen Quellcode-Bind-Mount**.

Daraus folgt:

| Aktion | Nimmt neuen Code live? | Freigabe nötig? |
|---|---|---|
| `docker restart opengym-worker` | **Nein** | Nein — aber kurz halten + Funktions-Check |
| `docker compose up -d` (ohne `--build`) | **Nein** | Nein — Funktions-Check |
| `docker compose build` / `up -d --build` | **Ja — das ist der Deploy** | **Ja, Damien** |

Ein Neustart kann also den im Repo gemergten Stand **nicht** ungewollt live nehmen.
Genehmigungspflichtig ist allein der Rebuild.

### Neustart-Prozedur

1. Neustart so kurz wie möglich halten.
2. **Sofort danach Funktions-Check** (Pflicht):

```bash
docker ps --filter name=opengym --format '{{.Names}} | {{.Status}}'
docker logs opengym-worker --tail 20
```

Erwartetes Bild eines gesunden Zyklus (alle ~5 Minuten eine solche Zeile):

```
worker cycle: expired_db=0 deleted_nuki=0 orphans_removed=0 windows=162
  tw_slots=197 tw_assigned=0 tw_no_code=0 tw_delivered=0 tw_blocked=0
  tw_pushed=53 guardian_reconciled=True guardian_repaired=0 dry_run=False
```

Prüfpunkte: `tw_pushed=53` (alle Codes auf dem Schloss; historisch 101), `guardian_reconciled=True`,
`dry_run=False`, keine `ERROR`-Zeilen, kein Restart-Loop.

---

## 3. Kernpfade (Kurzreferenz für die Fehlersuche)

| Pfad | Wert | Quelle |
|---|---|---|
| Code-Pool auf dem Keypad | **53** = 48 Off-Peak + 5 Business-Hours-Fallback (historisch 101) | `timewindow/pin_pool.py:168` (`expected_slot_count`) |
| Off-Peak-Pool | 2 Codes je Off-Peak-Stunde (`POOL_PER_HOUR`) → 24×2 = 48 (historisch 4/h = 96) | `timewindow/pin_pool.py:35` |
| Fallback-Pool | 5 Codes `og-bh-p0..p4`, 08:00–21:00, Mo–Sa | `timewindow/pin_pool.py:44`, `:148` |
| Hardware-Grenze | 200 Codes (`KEYPAD_CODE_LIMIT`), Grenzwächter `assert_within_budget` | `timewindow/pin_pool.py:28`, `:194` |
| PIN-Format | genau 6 Ziffern 1–9, darf nicht mit `12` beginnen | `timewindow/pin_pool.py:46-59` |
| Zugangsfenster (= die „Buchungssperre 30 min") | Buchungsstart **−15 min** bis Cluster-Ende **+30 min**. Der 30-min-Wert ist ein **Nachlauf**, keine Sperre — so am 17.09.2026 von Damien bestätigt. | `services/sync.py:91-100` |
| PIN gilt für | **die erste gebuchte Stunde** (ungepuffert) | `timewindow/rotation.py:362-363` |
| Sync-/Worker-Intervall | **5 Minuten** (`MAGICLINE_SYNC_INTERVAL_MINUTES`, Default 5) | `config.py:30`, `worker.py:70` |
| Rotation | täglich, create-first: erst neuen Code anlegen + Bestätigung abwarten, **dann** alten löschen | `timewindow/rotation.py:147-156` |

Wichtig zur E-Mail-Gültigkeit: Die Mail nennt das **echte Türfenster des
ausgelieferten Codes**, nicht das gepufferte Zugangsfenster — sonst verspricht sie mehr
Zutritt, als das Schloss öffnet (Vorfall 24.07.2026, siehe `rotation.py:459-463`).

---

## 4. Notfall-Flags

Beide Flags werden in der `.env` gesetzt und wirken erst **nach einem Rebuild bzw.
Container-Recreate** (der Quellcode liegt im Image; die `.env` wird beim Start gelesen).
Setzen und Zurücksetzen ist **Handarbeit und braucht Damiens Freigabe** — kein Skript,
kein Cron und kein Wächter darf diese Flags selbst verändern.

### `NUKI_ROTATION_PAUSED` (Default: `false`)

**Bedeutung.** `true` friert das Schloss ein: `rotate_daily` macht **keinerlei**
Lock-Änderungen (kein create/delete/rotate), es werden keine neuen PINs erzeugt. Die
Codes (53 Slots, historisch 101) bleiben physisch unverändert auf dem Keypad gültig. Die Zustellung läuft
weiter, sie fällt auf die zuletzt rotierten PINs zurück (= die eingefrorenen
Schloss-Codes). Das Flag hat **Vorrang vor `force`** — am eingefrorenen Schloss darf
nichts mutieren.
Quelle: `config.py:60`, `timewindow/rotation.py:160-169`.

**Wann setzen.** Bei einem Studio-Internet-/Router-Ausfall, also wenn die
Cloud↔Schloss-Verbindung steht, aber das Schloss offline ist. Ohne den Freeze würde die
tägliche Rotation Codes erzeugen, die das offline Keypad nie erreichen.

**Wann zurücksetzen.** Sobald die Leitung wieder steht **und** das Schloss über die
Nuki-API wieder erreichbar/bestätigend ist. Danach Rebuild + Funktions-Check; die
normale Tagesrotation läuft dann wieder an.

**Risiko als Dauerzustand.** Statische Türcodes. Der Freeze ist während des Ausfalls
richtig, als Dauerzustand ein Sicherheitsrisiko — deshalb der Freeze-Wächter
(Alarm nach 24 h, Alarm bei Rückkehr der Erreichbarkeit).

**Erkennbar im Log** (jeder Worker-Zyklus):

```
WARNING rotate_daily: PAUSED (NUKI_ROTATION_PAUSED) for <Datum> — no lock changes;
delivery falls back to the last rotated pins (frozen lock codes).
```

### `NUKI_REQUIRE_DEVICE_CONFIRMATION` (Default: `true` = fail-closed)

**Bedeutung.** `true` stellt einen Code **nur** zu, wenn er vom Gerät bestätigt ist
(`updateDate` vorhanden). Ein Code, der in der Cloud-Autorisierungsliste steht, aber
nie am physischen Keypad angekommen ist, wird **nicht** zugestellt — er wäre ein toter
Code und würde das Mitglied aussperren. Stattdessen: fail closed + Alarm, Retry im
nächsten Zyklus. `false` stellt das alte Verhalten „zustellen, sobald in der Cloud
vorhanden" wieder her.
Quelle: `config.py:52`, `timewindow/rotation.py:415-450`.

**Wann setzen.** Steht auf `true` und soll dort bleiben. Hintergrund: am 03.08.2026 live
bestätigtes Lockout-Risiko bei eingefrorenem Cloud↔Schloss-Sync.

**Wann zurücksetzen (`false`).** Nur als bewusste Notfall-Entscheidung von Damien, wenn
die Gerätebestätigung dauerhaft kaputt ist und Zustellung wichtiger ist als die
Lockout-Absicherung. Nicht als Routine-Workaround.

**Verwandt:** `NUKI_FREEZE_THRESHOLD_HOURS` (Default 3) steuert nur die **Formulierung**
des Alarms (echter Freeze vs. kurzzeitige create→confirm-Lücke), nicht das Gate selbst.

---

## 5. Backup & Restore

**Backup.** Nächtlicher `pg_dump` der DB `opengym` um **03:15** nach
`/opt/getimpulse/backups/opengym`, gzip, Retention 14 Tage, Trailer-Check, Alarm bei
Fehler. Skript: `/opt/getimpulse/ops/opengym-backup/backup.sh` (Cron).

**Restore-Grundsatz.** Ein Dump wird **nie direkt über die Produktions-DB** eingespielt.
Immer zuerst in eine separate Test-Datenbank auf `db-service` (z. B.
`opengym_restore_test`), dort Tabellenzahl und Stichprobenzeilen gegen die Produktion
vergleichen, und erst nach dieser Prüfung über einen Restore in die Produktion
entscheiden — der ist ein Eingriff mit Freigabepflicht.

**Verboten im laufenden Betrieb:** `DROP`, `TRUNCATE`, Löschen von Volumes oder
Backup-Dateien. Auch nicht zum „Aufräumen" von Test-Datenbanken — die bleiben stehen
und werden zur Freigabe gemeldet.

---

## 6. Monitoring & Alerting

| Kanal | Was |
|---|---|
| Telegram Topic 37 (Gruppe `-1004316584883`) | Wächter-/Betriebsalarme; Credentials in `/opt/getimpulse/.credentials/opengym_telegram.env` |
| Rotations-Check-Cron 10:30 | `/opt/getimpulse/ops/opengym-rotation-check/check.py` |
| Worker-Log | ein `worker cycle:`-Eintrag alle ~5 min; `guardian_reconciled=True` erwartet |
| Externer Dead-Man's-Switch (healthchecks.io) | `HEALTHCHECK_PING_URL` im Worker; meldet nach jedem erfolgreichen Zyklus `/start`, Payload-Zusammenfassung und `/fail` bei Exceptions |

Alarme sind dedupliziert/mit Cooldown (2 h für blockierte Zustellungen, 6 h für
Degradations-Warnungen), damit ein dauerhaft blockiertes Fenster nicht bei jedem Tick
neu alarmiert.

---

## 7. Fallback-Zugang

Angabe von Damien (17.09.2026): **Nuki App und physischer Schlüssel.**

Kommt niemand über das Keypad hinein, gibt es zwei Wege, die unabhängig von
Rotation, Codes und Cloud↔Schloss-Sync funktionieren:

1. **Nuki App** — öffnet das Schloss direkt (Bluetooth vor Ort, oder remote über den
   Hub). Unabhängig von den Keypad-Codes, funktioniert also auch bei eingefrorener
   Rotation oder leerem Code-Pool.
2. **Physischer Schlüssel** — der letzte Rückfallweg, unabhängig von Strom, Netz und
   Software.

> Wer App-Zugriff hat und wo der Schlüssel hinterlegt ist, steht bewusst **nicht** in
> dieser Datei (sie liegt im Repo). Diese Angaben laufen über Damien.

Vor jedem Deploy, der Tür-, Nuki- oder Rotationslogik berührt, ist zu prüfen, dass
mindestens einer dieser beiden Wege verfügbar ist (siehe Kopf dieses Dokuments).

Zusätzlich als **Software-Fallback** aus dem Code belegt:

- Die **5 Business-Hours-Fallback-Codes** (`og-bh-p0..p4`, 08:00–21:00 Mo–Sa) sind
  stabil vormaterialisiert und überstehen einen Cloud↔Schloss-Ausfall — sie sind der
  Grund, warum der Ausfall-Detektor während eines Freezes noch zustellen kann.
- Bei aktivem `NUKI_ROTATION_PAUSED` bleiben **alle Codes (53 Slots, historisch 101) gültig** — ein Freeze
  sperrt niemanden aus, er verhindert nur neue Codes.

---

## 8. Eskalationsweg

1. **Erkennen** — Alarm in Telegram Topic 37, oder `ERROR`-Zeilen bzw. fehlende
   `worker cycle:`-Zeilen im Log.
2. **Einordnen** (rein lesend, ohne Eingriff):
   ```bash
   docker ps --filter name=opengym --format '{{.Names}} | {{.Status}}'
   docker logs opengym-worker --tail 50
   docker logs opengym-service --tail 50
   ```
3. **Nicht-invasiv beheben** — Neustart ist erlaubt (kurz halten, danach
   Funktions-Check). Ein Neustart ist kein Deploy.
4. **Freigabe einholen bei Damien**, bevor irgendetwas davon passiert:
   - Rebuild (`docker compose build` / `up -d --build`)
   - Änderung an Tür-, Nuki- oder Rotations-Logik
   - Setzen oder Zurücksetzen von `NUKI_ROTATION_PAUSED` /
     `NUKI_REQUIRE_DEVICE_CONFIRMATION`
   - Restore in die Produktions-DB
5. **Dokumentieren** — Tageslog-Eintrag mit Zeitstempel in `PO-STATUS.md`, mit Beleg
   (Kommando-Output, Logzeile, Testausgabe).

---

## 9. Teilbereich Studio-Automations

Liegt auf `getimpulse-nas`. Das NAS ist seit 23.08.2026 wieder online über
Tailscale erreichbar (100.103.57.114, Ping OK ~98 ms). Die Studio-Automations
ruhen bis zur Abstimmung und Freigabe durch Damien.
Regel: Erreichbarkeit stündlich prüfen, bei Statusänderungen melden — nie darauf blockieren.

