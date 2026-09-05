# Nuki Hub auf ESP32 — Bauplan & Migrationsvorbereitung

Stand 30.08.2026. Ziel: Die Nuki-Cloud vollständig ersetzen — Keypad-Code-Verwaltung
(Rotation!), Konfiguration und Status laufen dann **lokal** über BLE↔MQTT.
Hintergrund: Der Speedport 7 blockiert die Nuki Web API gerätespezifisch; der
Cloud↔Gerät-Auth-Sync ist seit Wochen unzuverlässig (Log seit 09.06. eingefroren).
Codes am Gerät sind bis 30.09.2026 gültig (nukictl/BLE) — bis dahin muss der Hub
stehen und die Rotation (Laufzeit bis 31.12.2026) darüber gelaufen sein.

## 1. Hardware (Einkaufsliste, ~15–25 €)

| Teil | Empfehlung | Hinweis |
|---|---|---|
| ESP32-Board | **ESP32-WROOM-32 DevKit** (klassischer ESP32) | NICHT ESP32-S2 (kein Bluetooth!). S3 ok, klassisch am besten unterstützt |
| Netzteil | USB-Netzteil 5V/1A + Micro-USB/USB-C-Kabel | Dauerbetrieb; an NICHT geschaltete Steckdose (Lehre aus Kameras 2/4/5!) |
| Gehäuse | optional | Staubschutz |

**Platzierung:** BLE-Reichweite zum Schloss < 5 m, freie Sichtlinie bevorzugt.
Gleichzeitig gutes WLAN — das Schloss selbst hat dort schwaches Signal
(~100 ms Jitter gemessen), der ESP32 mit externem Board ist toleranter, aber
Steckdose nahe der Tür wählen.

## 2. Firmware: Nuki Hub

- Projekt: https://github.com/technyon/nuki_hub (Doku: https://nukihub.io)
- Flashen am einfachsten per **Web-Installer** (Chrome/Edge + USB): https://install.nukihub.io
- Board-Variante „ESP32" (generic) wählen.

## 3. Konfiguration (nach dem Flashen, über das Nuki-Hub-Webinterface)

1. **WLAN:** SSID `GETIMPULSE` (Passwort siehe Router-Aufkleber/Speedport-App).
2. **Statische IP:** im Speedport reservieren, Vorschlag `192.168.2.142`
   (Speedport-App → Heimnetzwerk → Reservierte lokale IP-Adresse; .141 = Nuki).
3. **MQTT:**
   - Broker: `192.168.2.102`, Port `1883` (Mosquitto auf der NAS)
   - User: `getimpulse` (derselbe, den das Schloss nativ nutzt; Passwort in
     Mosquitto-Config auf der NAS: `/volume1/docker/.../mosquitto`), oder eigenen
     User `nukihub` in Mosquitto anlegen (sauberer, ACL-fähig)
   - Pfad-Präfix: `nukihub` (Default) — kollidiert NICHT mit dem nativen `nuki/…`
4. **Pairing mit dem Schloss:**
   - ⚠️ In der Nuki-App ist `pairingEnabled: false` gesetzt (per Cloud-Config
     bestätigt). Vorher in der App aktivieren: Einstellungen → Funktionen &
     Konfiguration → Kopplungen erlauben.
   - Dann im Nuki-Hub-UI „Pair Nuki Lock" — Hub koppelt als eigener BLE-Nutzer.
   - Danach Pairing in der App wieder deaktivieren.
5. **HYBRID-MODUS (wichtig!):** Das Schloss publiziert bereits natives MQTT
   (`nuki/4C17A4E7/…`), darüber laufen HA-Lock-Entity und alle Automationen.
   Nuki Hub im **Hybrid-Modus** betreiben: natives MQTT bleibt für Status/Lock-
   Aktionen aktiv, der Hub ergänzt das, was nativ fehlt — **Keypad-Codes,
   Konfiguration, Log**. So bleibt HA unangetastet (kein Umbau der Automationen).
6. **HA-Discovery des Hubs:** zunächst AUS lassen (sonst doppelte Lock-Entities
   in HA). Erst aktivieren, falls später bewusst von nativem MQTT migriert wird.

## 4. Keypad-Codes über den Hub (ersetzt die Web API)

Nuki Hub stellt die Keypad-Verwaltung über MQTT bereit:
- `nukihub/lock/keypad/json` — Liste aller Codes (retained, nach `keypadCodes`-Abfrage)
- `nukihub/lock/keypad/actionJson` — Kommandos, JSON:
  ```json
  {"action": "add|update|delete", "codeId": 0, "code": 123456,
   "name": "og-h07-p0", "timeLimited": 1,
   "allowedFrom": "2026-10-01 00:00:00", "allowedUntil": "2026-12-31 23:59:59",
   "allowedWeekdays": ["mon","tue","wed","thu","fri","sat","sun"],
   "allowedFromTime": "07:00", "allowedUntilTime": "08:00"}
  ```
  (Feldnamen gegen die installierte Nuki-Hub-Version verifizieren — Doku
  nukihub.io → MQTT → Keypad.)
- Ergebnis-Topic: `nukihub/lock/configuration/commandResult`

**Anzahl-Limit beachten:** Keypad hält max. 200 Codes; aktuell 137 belegt.
Rotation = update in-place (Code-Wert + Laufzeit ändern), NICHT delete+create.

## 5. Integration in den opengym-worker (Rotation reaktivieren)

Neuer Transport statt `NukiClient` (Web API) in
`opengym/src/nuki_integration/nuki_client.py`:

```
class NukiHubMqttClient:            # gleiche Schnittstelle wie NukiClient
    list_keypad_codes()             # publish keypadCodes-Abfrage, read json (retained)
    create_keypad_code(...)         # actionJson add
    update_keypad_code(...)         # actionJson update  ← Rotation nutzt NUR das
    delete_keypad_code(...)         # actionJson delete
    # Materialisierung = commandResult=="success" + Code taucht in keypad/json auf
    # → ersetzt _wait_present/_wait_materialised (deutlich schneller & lokal!)
```

- Env-Schalter `NUKI_TRANSPORT=webapi|nukihub` (Default webapi), damit Rollback trivial ist.
- MQTT-Client: paho-mqtt (in requirements aufnehmen), Broker wie oben.
- Nach Umstellung: `NUKI_ROTATION_PAUSED` entfernen → tägliche Rotation läuft
  wieder, jetzt über den zuverlässigen lokalen Kanal.
- Guardian/Monitoring: `evaluate_window_materialization` auf keypad/json-Quelle
  umstellen (statt Cloud-Auth-Liste).

## 6. Rotations-Fahrplan (Laufzeit bis 31.12.2026)

**Bewusst NICHT über die Nuki Web API** — der Cloud↔Gerät-Auth-Sync ist
nachweislich defekt (Cloud zeigt 29.08/None, Gerät real 30.09; Queue-Änderungen
kommen nicht an). Rotation erst, wenn der Hub steht:

1. Hub gebaut + gekoppelt + `keypad/json` zeigt die 101 og-Codes mit korrekten
   Werten (Abgleich gegen DB: muss 101/101 matchen — Stand 30.08. verifiziert).
2. Testlauf: EINEN Slot (og-h04-p3) per Hub-update rotieren → am Keypad testen.
3. Batch-Rotation aller 101: neuer Pin (6 Ziffern, nur 1–9, keypad-weit eindeutig)
   + `allowedUntil = 2026-12-31 23:59:59`, Fenster/Wochentage UNVERÄNDERT lassen.
4. DB nachziehen: neue Zeilen `nuki_pin_history` (slot_id, rotation_date=heute,
   pin, pushed=t, materialised=t, dry_run=f) — Versand liefert dann die neuen Pins.
5. Verifikation: keypad/json ↔ DB 101/101; `store.get_todays_slot_pin`-Stichproben;
   ein realer Türtest.
6. Danach Rotation automatisieren (Schritt 5 oben).

Fallback bis dahin: Codes sind bis 30.09. gültig; bei Verzug erneut per
nukictl/BLE verlängern (bewährter Weg).

## 7. Risiken / Rollback

- Hub ist ein ZUSÄTZLICHER BLE-Nutzer — Entfernen jederzeit möglich (App:
  Nutzer löschen). Natives MQTT & Keypad bleiben davon unberührt.
- Beim Pairing keine Codes/Config verändern → kein Aussperr-Risiko durch den Bau.
- Web-API-Zugang parallel behalten (Read-only-Monitoring), aber nichts mehr
  darüber schreiben.
- Speedport: dem ESP32 KEINE Sperren verpassen (aus Nuki-Fall lernen) — nach
  Einrichtung prüfen, dass der Hub NICHT ins Internet muss (er braucht es nicht:
  alles lokal; optional Updates manuell).

## 8. Offene Punkte vor dem Bau

- [ ] ESP32 + Netzteil besorgen
- [ ] Mosquitto: eigenen User `nukihub` anlegen (oder `getimpulse` mitnutzen)
- [ ] Nuki-App: Pairing kurzzeitig erlauben (vor Ort, BLE)
- [ ] Speedport: IP-Reservierung .142 für den ESP32
- [ ] Nach Bau: Feldnamen der Keypad-MQTT-API gegen installierte Version prüfen
