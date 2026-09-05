"""Nuki Hub (ESP32/technyon) transport over MQTT — drop-in for :class:`NukiClient`.

Why this exists: the Nuki **Web API** is not a reliable view of this lock. The
cloud↔device auth sync has been broken since 2026-06 (log frozen, queued changes
never flushed), so ``GET /smartlock/{id}/auth`` returns a stale projection: on
2026-09-05 it still showed the 2026-07-30 keypad codes while the device had long
since been rotated. Delivery verifies every code before mailing it (fail-closed),
so a stale cloud view silently blocked *every* dispatch for days.

The Nuki Hub talks **BLE directly to the lock** and mirrors it to MQTT. Its
``keypad/json`` is therefore *device truth*, not a cloud projection — which is
exactly what the pre-dispatch gate needs. This class speaks that MQTT dialect
while exposing the same method surface as :class:`NukiClient`, so callers
(rotation, guardian, access, monitoring) stay unchanged; pick one with
:func:`nuki_integration.nuki_client.build_nuki_client` / ``NUKI_TRANSPORT``.

Shape translation (hub → Web API), so the pure evaluators in ``nuki_client`` —
``evaluate_materialization`` / ``evaluate_window_materialization`` — work
untouched on hub data:

===================  ==========================================================
Web API field        Hub source
===================  ==========================================================
``id``               ``codeId`` (int, hub-local — NOT the cloud's hex auth id)
``type``             always 13 (the hub only lists keypad codes here)
``code``             ``code``
``updateDate``       timestamp of the hub read — see note below
``allowedWeekDays``  ``allowedWeekdays`` names → Nuki bitmask (Mo=64 … So=1)
``allowedFromTime``  ``allowedFromTime`` "HH:MM" → minutes since midnight
``allowedUntilDate`` ``allowedUntil`` "Y-m-d H:M:S" → ISO-8601 Z
===================  ==========================================================

**``updateDate`` = "device-confirmed" and that is not a lie here.** Over the Web
API the field means "the device echoed this change back to the cloud", which is
why its absence gates dispatch. Everything the hub publishes it has *read off
the device over BLE*, so presence in ``keypad/json`` already carries that proof;
we stamp the read time so the outage detector reads it as confirmed.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .nuki_client import (
    NukiApiError,
    evaluate_materialization,
    evaluate_window_materialization,
    validate_keypad_code,
)

logger = logging.getLogger(__name__)

# Nuki weekday bitmask (same convention as the Web API's ``allowedWeekDays``).
_WEEKDAY_BITS = {"mon": 64, "tue": 32, "wed": 16, "thu": 8, "fri": 4, "sat": 2, "sun": 1}
_BIT_WEEKDAYS = {v: k for k, v in _WEEKDAY_BITS.items()}

_LOCK_STATE_LABELS = {
    "locked": "Abgeschlossen",
    "unlocked": "Aufgeschlossen",
    "unlatched": "Falle offen",
    "unlocking": "Schließt auf",
    "locking": "Schließt ab",
    "jammed": "Blockiert",
    "undefined": "Unbekannt",
}
_DOOR_STATE_LABELS = {
    "doorClosed": "Geschlossen",
    "doorOpened": "Offen",
    "doorStateUnknown": "Unbekannt",
    "deactivated": "Kein Sensor",
}


# ── Conversion helpers (pure, unit-tested) ────────────────────────


def _minutes(hhmm: str | None) -> int | None:
    """"HH:MM" → minutes since midnight; None when unparseable."""
    if not hhmm or ":" not in str(hhmm):
        return None
    try:
        h, m = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def _hhmm(minutes: int | None) -> str:
    """Minutes since midnight → "HH:MM" (the hub's time format)."""
    if minutes is None:
        return "00:00"
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _iso(hub_date: str | None) -> str | None:
    """Hub "Y-m-d H:M:S" → ISO-8601 "…Z"; None for the hub's zero date."""
    if not hub_date or str(hub_date).startswith("0000"):
        return None
    return str(hub_date).strip().replace(" ", "T") + "Z"


def _hub_date(iso_or_date: str | None) -> str:
    """Web-API-style ISO date → hub "Y-m-d H:M:S" (empty string when absent)."""
    if not iso_or_date:
        return ""
    s = str(iso_or_date).strip().replace("Z", "").replace("T", " ")
    if len(s) == 10:  # date only
        s += " 00:00:00"
    return s[:19]


def _weekday_names(mask: int | None) -> list[str]:
    """Nuki bitmask → hub weekday names. A falsy mask means *every* day."""
    if not mask:
        return list(_WEEKDAY_BITS.keys())
    return [name for bit, name in sorted(_BIT_WEEKDAYS.items(), reverse=True) if mask & bit]


def hub_entry_to_auth(entry: dict[str, Any], *, seen_at: str) -> dict[str, Any]:
    """Translate one ``keypad/json`` entry into a Web-API-shaped type-13 auth."""
    time_limited = bool(int(entry.get("timeLimited") or 0))
    mask = sum(_WEEKDAY_BITS.get(d, 0) for d in (entry.get("allowedWeekdays") or []))
    from_time = _minutes(entry.get("allowedFromTime")) if time_limited else None
    until_time = _minutes(entry.get("allowedUntilTime")) if time_limited else None
    # The lock encodes "no daily restriction" as an empty span (00:00–00:00).
    # Passing that through verbatim would make _auth_covers_hour reject every
    # hour (``from <= start < until`` is never true), i.e. fail-close a code
    # that in fact opens around the clock. Normalise it to "unrestricted".
    if from_time is not None and from_time == until_time:
        from_time = until_time = None
    code = entry.get("code")
    return {
        "id": entry.get("codeId"),
        "type": 13,
        "name": (entry.get("name") or "").strip(),
        "code": int(code) if code is not None else None,
        "enabled": bool(int(entry.get("enabled") or 0)),
        "updateDate": seen_at,  # read off the device by the hub — see module docstring
        "creationDate": _iso(entry.get("dateCreated")),
        "allowedFromDate": _iso(entry.get("allowedFrom")) if time_limited else None,
        "allowedUntilDate": _iso(entry.get("allowedUntil")) if time_limited else None,
        "allowedWeekDays": mask if time_limited else 0,
        "allowedFromTime": from_time,
        "allowedUntilTime": until_time,
        "lockCount": entry.get("lockCount"),
        "dateLastActive": entry.get("dateLastActive"),
        "source": "nukihub",
    }


def parse_keypad_json(payload: str, *, seen_at: str) -> tuple[list[dict[str, Any]], bool]:
    """Parse ``keypad/json`` into auth dicts. Returns ``(auths, truncated)``.

    The hub builds this payload into a fixed buffer and simply stops when it is
    full, so a large keypad yields a *syntactically broken* JSON array cut mid
    entry. Rather than dropping the whole list (which would fail-close every
    dispatch), salvage every complete object and report the truncation so the
    caller can warn — a partial device view is still device truth for the codes
    it does contain.
    """
    payload = (payload or "").strip()
    if not payload:
        return [], False
    try:
        raw = json.loads(payload)
        return [hub_entry_to_auth(e, seen_at=seen_at) for e in raw], False
    except json.JSONDecodeError:
        pass

    entries: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    idx = payload.find("{")
    while idx != -1:
        try:
            obj, end = decoder.raw_decode(payload, idx)
        except json.JSONDecodeError:
            break  # first incomplete object — everything after it is cut off too
        if isinstance(obj, dict):
            entries.append(hub_entry_to_auth(obj, seen_at=seen_at))
        idx = payload.find("{", end)
    return entries, True


# ── MQTT transport ────────────────────────────────────────────────


class NukiHubMqttClient:
    """Keypad/lock control through a Nuki Hub's MQTT interface.

    Method surface mirrors :class:`NukiClient`; ``auth_id`` is the hub's
    ``codeId`` here, so a transport switch must not reuse cloud auth ids
    (rotation re-resolves them by slot name, which is transport agnostic).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._prefix = (getattr(settings, "nuki_mqtt_prefix", "") or "nukihub").rstrip("/")
        self._timeout = float(getattr(settings, "nuki_mqtt_timeout_seconds", 20) or 20)
        self._messages: dict[str, str] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._client = None
        self._connected = False
        self._cache: tuple[float, list[dict[str, Any]]] | None = None

    # ── topics ────────────────────────────────────────────────────

    def _t(self, suffix: str) -> str:
        return f"{self._prefix}/{suffix}"

    # ── connection ────────────────────────────────────────────────

    def _connect(self):
        if self._client is not None and self._connected:
            return self._client
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise NukiApiError(f"paho-mqtt not installed: {exc}") from exc

        host = getattr(self._settings, "nuki_mqtt_host", "") or ""
        if not host:
            raise NukiApiError("NUKI_MQTT_HOST is not configured.")
        port = int(getattr(self._settings, "nuki_mqtt_port", 1883) or 1883)

        try:  # paho 2.x moved the callback API behind a version flag
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"opengym-worker-{int(time.time())}",
            )
        except AttributeError:  # paho 1.x
            client = mqtt.Client(client_id=f"opengym-worker-{int(time.time())}")

        user = getattr(self._settings, "nuki_mqtt_username", "") or ""
        if user:
            client.username_pw_set(user, getattr(self._settings, "nuki_mqtt_password", "") or None)

        def _on_message(_c, _u, msg):
            try:
                text = msg.payload.decode(errors="replace")
            except Exception:  # pragma: no cover - defensive
                return
            with self._lock:
                self._messages[msg.topic] = text
                ev = self._events.get(msg.topic)
            if ev is not None:
                ev.set()

        client.on_message = _on_message
        try:
            client.connect(host, port, keepalive=30)
        except Exception as exc:
            raise NukiApiError(f"Nuki Hub MQTT connect failed ({host}:{port}): {exc}") from exc
        client.loop_start()
        for suffix in (
            "lock/keypad/json",
            "lock/keypad/#",  # per-entry topics: name/code/id, no size cap
            "lock/keypad/commandResultJson",
            "lock/keypad/commandResult",
            "lock/commandResult",
            "lock/configuration/commandResult",
            "lock/json",
            "lock/availability",
            "lock/battery/basicJson",
            "lock/doorSensorState",
            "lock/state",
            "lock/log",
        ):
            client.subscribe(self._t(suffix), qos=0)
        self._client = client
        self._connected = True
        # Retained topics arrive right after SUBSCRIBE; give them a moment so the
        # first call does not have to trigger a device round-trip.
        time.sleep(1.0)
        return client

    def close(self) -> None:
        client, self._client, self._connected = self._client, None, False
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.debug("Nuki Hub MQTT close: %s", exc)

    # ── primitives ────────────────────────────────────────────────

    def _publish(self, suffix: str, payload: str) -> None:
        client = self._connect()
        topic = self._t(suffix)
        logger.info("NukiHub PUBLISH %s", topic)
        info = client.publish(topic, payload, qos=1)
        try:
            info.wait_for_publish(timeout=self._timeout)
        except Exception as exc:  # pragma: no cover - paho version differences
            logger.debug("wait_for_publish: %s", exc)

    def _await_message(self, suffix: str, *, timeout: float, since: float) -> str | None:
        """Wait for a *fresh* message on ``suffix``; None if none arrives in time.

        The previously received (usually retained) payload is put back when the
        wait times out — the hub only republishes ``keypad/json`` once its BLE
        query returns, and losing the retained snapshot in the meantime would
        leave the caller with nothing to fall back on.
        """
        topic = self._t(suffix)
        event = threading.Event()
        with self._lock:
            previous = self._messages.pop(topic, None)
            self._events[topic] = event
        fresh = None
        try:
            if event.wait(timeout=max(0.0, timeout - (time.time() - since))):
                with self._lock:
                    fresh = self._messages.get(topic)
            return fresh
        finally:
            with self._lock:
                self._events.pop(topic, None)
                if fresh is None and previous is not None:
                    self._messages.setdefault(topic, previous)

    def _last(self, suffix: str) -> str | None:
        with self._lock:
            return self._messages.get(self._t(suffix))

    # ── keypad reads ──────────────────────────────────────────────

    def list_keypad_codes(self, *, refresh: bool = True, cache_seconds: float = 10.0) -> list[dict]:
        """All keypad codes as Web-API-shaped type-13 auths (device truth)."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip list_keypad_codes")
            return []
        now = time.time()
        if self._cache and now - self._cache[0] < cache_seconds:
            return list(self._cache[1])
        try:
            self._connect()
            payload = None
            if refresh:
                started = time.time()
                # Ask the hub to re-read the keypad from the lock over BLE.
                self._publish("lock/query/keypad", "1")
                payload = self._await_message(
                    "lock/keypad/json", timeout=self._timeout, since=started
                )
            if payload is None:  # fall back to the retained snapshot
                payload = self._last("lock/keypad/json")
            if payload is None:
                logger.error("NukiHub: no keypad/json received (hub offline?)")
                return []
            seen_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            auths, truncated = parse_keypad_json(payload, seen_at=seen_at)
            if truncated:
                logger.warning(
                    "NukiHub: keypad/json cut off at %d entries (%d bytes)",
                    len(auths), len(payload),
                )
            # The JSON is size-capped by the firmware, so fill the gap from the
            # per-entry topics. Verified on 2026-09-05: keypad/json listed 34 of
            # the keypad's codes, yet a device check (action "check") confirmed
            # codes that only the per-entry topics knew — the missing ones are
            # invisible, not absent.
            known = {a.get("code") for a in auths}
            extra = [a for a in self._per_entry_auths(seen_at) if a["code"] not in known]
            if extra:
                logger.info(
                    "NukiHub: %d code(s) only visible via per-entry topics "
                    "(keypad/json holds %d) — window data unknown for those",
                    len(extra), len(auths),
                )
                auths = auths + extra
            self._cache = (now, auths)
            return list(auths)
        except Exception as exc:
            logger.error("NukiHub list_keypad_codes failed: %s", exc)
            return []

    def _per_entry_auths(self, seen_at: str) -> list[dict[str, Any]]:
        """Codes from the per-entry topics (``keypad/code_N/{name,code,id}``).

        These carry no time-window fields, but they are not subject to the
        ``keypad/json`` size cap — on this keypad the JSON stops at 34 entries
        while the lock holds far more. Entries sourced here are flagged
        ``windowUnknown`` so the caller confirms them against the device
        (``check_keypad_code``) instead of trusting a possibly stale retain.
        """
        prefix = self._t("lock/keypad/")
        grouped: dict[str, dict[str, str]] = {}
        with self._lock:
            items = list(self._messages.items())
        for topic, value in items:
            if not topic.startswith(prefix):
                continue
            rest = topic[len(prefix):]
            if "/" not in rest:
                continue
            entry_key, field = rest.split("/", 1)
            if not entry_key.startswith("code_") or "/" in field:
                continue
            grouped.setdefault(entry_key, {})[field] = value
        out: list[dict[str, Any]] = []
        for entry in grouped.values():
            code, name = entry.get("code"), entry.get("name")
            if not code or not name or not str(code).isdigit():
                continue
            out.append({
                "id": int(entry["id"]) if str(entry.get("id", "")).isdigit() else None,
                "type": 13,
                "name": name.strip(),
                "code": int(code),
                "enabled": str(entry.get("enabled", "1")) != "0",
                "updateDate": seen_at,
                "creationDate": None,
                "allowedFromDate": None, "allowedUntilDate": None,
                "allowedWeekDays": 0, "allowedFromTime": None, "allowedUntilTime": None,
                "lockCount": entry.get("lockCount"),
                "windowUnknown": True,
                "source": "nukihub-entry-topic",
            })
        return out

    def _auths_or_error(self) -> tuple[list[dict], bool]:
        """``(auths, unreachable)`` — an empty list from a live hub is not an error."""
        try:
            self._connect()
        except Exception as exc:
            logger.error("NukiHub unreachable: %s", exc)
            return [], True
        auths = self.list_keypad_codes()
        if not auths and self._last("lock/keypad/json") is None:
            return [], True
        return auths, False

    def verify_materialization(self, code: str) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip materialization check for code ******")
            return {"materialised": True, "simulated": True, "auth_id": None, "update_date": None}
        auths, unreachable = self._auths_or_error()
        if unreachable:
            return {"materialised": False, "simulated": False, "auth_id": None, "update_date": None}
        return evaluate_materialization(auths, code)

    def verify_code_for_window(self, code: str, *, weekday: int, hour: int) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip window verification for code ****** (wd=%s h=%s)", weekday, hour)
            return {
                "exists": True, "materialised": True, "covers_window": True, "valid": True,
                "simulated": True, "auth_id": None, "update_date": None,
                "link_last_confirmed": None,
            }
        auths, unreachable = self._auths_or_error()
        if unreachable:
            # Same contract as the Web API client: "cannot verify" must not be
            # mistaken for "code is missing" — callers skip and retry on error.
            return {
                "exists": False, "materialised": False, "covers_window": False, "valid": False,
                "simulated": False, "auth_id": None, "update_date": None,
                "link_last_confirmed": None, "error": True,
            }
        outcome = evaluate_window_materialization(auths, code, weekday, hour)
        if not outcome.get("exists"):
            return outcome
        match = next(
            (a for a in auths if a.get("type") == 13 and a.get("code") == int(code)), {}
        )
        if not match.get("windowUnknown"):
            return outcome
        # Only a per-entry topic knew this code, and those are retained: they can
        # outlive the code itself. Ask the device before letting it be dispatched.
        live = self.check_keypad_code(code_id=match.get("id"), code=code)
        if live is False:
            logger.warning(
                "NukiHub: %s is a stale retained topic — the device rejects the code",
                match.get("name"),
            )
            return {**outcome, "exists": False, "materialised": False,
                    "covers_window": False, "valid": False}
        if live is None:
            # The hub could not answer (offline / BLE busy). Two independent sources
            # still agree that this code is on the keypad: our own rotation history
            # and the hub's last device read (the retained topic we matched). Refusing
            # here would lock the member out for the whole booking — the failure mode
            # that kept 7 members out for five days — so deliver and flag it loudly.
            logger.warning(
                "NukiHub: device check inconclusive for %s — delivering on retained "
                "device read (hub unreachable)", match.get("name"),
            )
            outcome["window_source"] = "per-entry-topic"
            outcome["device_check"] = "inconclusive"
            return outcome
        outcome["window_source"] = "per-entry-topic"
        outcome["device_check"] = "confirmed"
        return outcome

    # ── keypad writes ─────────────────────────────────────────────

    def _keypad_action(self, payload: dict[str, Any]) -> str | None:
        started = time.time()
        self._cache = None
        self._publish("lock/keypad/actionJson", json.dumps(payload))
        # The hub answers keypad actions on commandResultJson ("success",
        # "codeValid", "noExistingCodeIdSet", …); the plain commandResult topics
        # are the lock-action channel and stay on "--" for keypad commands.
        result = self._await_message(
            "lock/keypad/commandResultJson", timeout=self._timeout / 2, since=started
        )
        if result and result not in ("--", "undefined"):
            return result
        # Older hub builds answer on the plain result topics — read what is cached
        # rather than serialising another full wait onto every command.
        for suffix in ("lock/keypad/commandResult", "lock/commandResult"):
            cached = self._last(suffix)
            if cached and cached not in ("--", "undefined"):
                return cached
        return None

    def check_keypad_code(self, *, code_id: int | str, code: str) -> bool | None:
        """Ask the lock itself whether ``code`` is the code behind ``code_id``.

        This is the one verification path that does not depend on the hub's
        ``keypad/json`` (which the firmware caps — 34 entries here, while the
        keypad holds far more): the hub forwards the question over BLE and the
        device answers ``codeValid`` / ``codeInvalid``. Returns None when the
        hub gives no verdict (unknown codeId, hub busy) — never guess from that.
        """
        if self._settings.nuki_dry_run:
            return True
        result = self._keypad_action(
            {"action": "check", "codeId": int(code_id), "code": int(code)}
        )
        if result == "codeValid":
            return True
        if result == "codeInvalid":
            return False
        logger.warning("NukiHub check codeId=%s: inconclusive (%s)", code_id, result)
        return None

    def _find_code_id(self, name: str, code: str | None) -> int | None:
        """Resolve a slot's hub ``codeId`` by name (and code, when given)."""
        for auth in self.list_keypad_codes(refresh=False, cache_seconds=2.0):
            if auth.get("name") != name[:32]:
                continue
            if code is not None and str(auth.get("code")) != str(code):
                continue
            return auth.get("id")
        return None

    def _action_payload(
        self, action: str, *, name: str, code: str | None, allowed_from: str,
        allowed_until: str, allowed_week_days: int, allowed_from_time: int | None,
        allowed_until_time: int | None, enabled: bool = True,
    ) -> dict[str, Any]:
        time_limited = bool(allowed_from or allowed_until or allowed_from_time is not None)
        payload: dict[str, Any] = {
            "action": action,
            "name": name[:32],
            "enabled": 1 if enabled else 0,
            "timeLimited": 1 if time_limited else 0,
        }
        if code is not None:
            payload["code"] = int(code)
        if time_limited:
            payload["allowedFrom"] = _hub_date(allowed_from)
            payload["allowedUntil"] = _hub_date(allowed_until)
            payload["allowedWeekdays"] = _weekday_names(allowed_week_days)
            payload["allowedFromTime"] = _hhmm(allowed_from_time)
            payload["allowedUntilTime"] = _hhmm(allowed_until_time)
        return payload

    def create_keypad_code(
        self, *, name: str, code: str, allowed_from: str, allowed_until: str,
        allowed_week_days: int = 127, allowed_from_time: int | None = None,
        allowed_until_time: int | None = None,
    ) -> int | None:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code create for %s", name)
            return None
        validate_keypad_code(code)
        payload = self._action_payload(
            "add", name=name, code=code, allowed_from=allowed_from,
            allowed_until=allowed_until, allowed_week_days=allowed_week_days,
            allowed_from_time=allowed_from_time, allowed_until_time=allowed_until_time,
        )
        result = self._keypad_action(payload)
        logger.info("NukiHub add %s: %s", name, result or "no commandResult")
        return self._find_code_id(name, code)

    def update_keypad_code(
        self, *, auth_id: int | str, name: str, code: str | None = None,
        allowed_from: str, allowed_until: str, allowed_week_days: int = 127,
        allowed_from_time: int | None = None, allowed_until_time: int | None = None,
        enabled: bool = True,
    ) -> None:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code update auth_id=%s", auth_id)
            return
        if code is not None:
            validate_keypad_code(code)
        code_id = auth_id
        # A cloud hex auth id is meaningless to the hub — re-resolve by slot name.
        if code_id is None or not str(code_id).isdigit():
            code_id = self._find_code_id(name, None)
        if code_id is None:
            raise NukiApiError(f"NukiHub: no keypad entry named {name!r} to update")
        payload = self._action_payload(
            "update", name=name, code=code, allowed_from=allowed_from,
            allowed_until=allowed_until, allowed_week_days=allowed_week_days,
            allowed_from_time=allowed_from_time, allowed_until_time=allowed_until_time,
            enabled=enabled,
        )
        payload["codeId"] = int(code_id)
        result = self._keypad_action(payload)
        logger.info("NukiHub update %s (codeId=%s): %s", name, code_id, result or "no commandResult")

    def deactivate_keypad_code(self, *, auth_id: int) -> None:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip deactivate auth_id=%s", auth_id)
            return
        self._keypad_action({"action": "update", "codeId": int(auth_id), "enabled": 0})

    def delete_keypad_code(self, *, auth_id: int | str) -> None:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip keypad code delete auth_id=%s", auth_id)
            return
        if not str(auth_id).isdigit():
            raise NukiApiError(f"NukiHub: {auth_id!r} is not a hub codeId")
        result = self._keypad_action({"action": "delete", "codeId": int(auth_id)})
        logger.info("NukiHub delete codeId=%s: %s", auth_id, result or "no commandResult")

    # ── lock actions ──────────────────────────────────────────────

    def _lock_action(self, action: str) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip %s", action)
            return {"dry_run": True, "smartlock_id": self._settings.nuki_smartlock_id}
        self._publish("lock/action", action)
        return {"success": True, "smartlock_id": self._settings.nuki_smartlock_id}

    def remote_open(self) -> dict[str, Any]:
        return self._lock_action("unlock")

    def remote_lock(self) -> dict[str, Any]:
        return self._lock_action("lock")

    def remote_unlatch(self) -> dict[str, Any]:
        return self._lock_action("unlatch")

    # ── status / misc ─────────────────────────────────────────────

    def get_lock_status(self) -> dict[str, Any]:
        if self._settings.nuki_dry_run:
            return {
                "dry_run": True, "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "credentials-pending", "stateName": "Testmodus",
                "lock_state": "Testmodus", "door_state": "Kein Sensor",
                "battery_state": "Unbekannt", "battery_critical": False,
                "batteryCritical": False, "source": "dry-run",
            }
        try:
            self._connect()
            time.sleep(0.5)
            state = (self._last("lock/state") or "undefined").strip()
            door = (self._last("lock/doorSensorState") or "doorStateUnknown").strip()
            online = (self._last("lock/availability") or "").strip() == "online"
            battery: dict[str, Any] = {}
            raw_battery = self._last("lock/battery/basicJson")
            if raw_battery:
                try:
                    battery = json.loads(raw_battery)
                except json.JSONDecodeError:
                    battery = {}
            critical = str(battery.get("critical", "0")) not in ("0", "false", "False")
            charging = str(battery.get("charging", "0")) not in ("0", "false", "False")
            level = battery.get("level")
            if level is not None:
                battery_state = f"{level}%" + (" (lädt)" if charging else (" ⚠" if critical else ""))
            else:
                battery_state = "Kritisch!" if critical else "OK"
            lock_label = _LOCK_STATE_LABELS.get(state, state or "Unbekannt")
            return {
                "dry_run": False,
                "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "online" if online else "offline (hub)",
                "server_state": 0 if online else 4,
                "lock_state": lock_label,
                "stateName": lock_label if online else f"{lock_label} (veraltet)",
                "door_state": _DOOR_STATE_LABELS.get(door, door),
                "battery_state": battery_state,
                "battery_charge": level,
                "battery_critical": critical,
                "batteryCritical": critical,
                "battery_charging": charging,
                "last_update": None,
                "source": "nuki-hub",
            }
        except Exception as exc:
            logger.error("NukiHub status failed: %s", exc)
            return {
                "dry_run": False, "smartlock_id": self._settings.nuki_smartlock_id,
                "connectivity": "error", "stateName": "Verbindungsfehler",
                "lock_state": "error", "door_state": "Unbekannt",
                "battery_state": "Unbekannt", "battery_critical": False,
                "batteryCritical": False, "error": str(exc), "source": "nuki-hub",
            }

    def get_log(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Activity log, mapped onto the Web API's field names.

        The hub splits the timestamp into ``timeYear``/``timeMonth``/… and calls the
        actor ``authorizationName``; the keypad-event classifier and the log cursor
        both key on ``date``/``name``/``id``, so translate here rather than teaching
        every consumer a second dialect.
        """
        if self._settings.nuki_dry_run:
            return []
        try:
            self._connect()
            time.sleep(0.5)
            raw = self._last("lock/log")
            entries = json.loads(raw) if raw else []
            if not isinstance(entries, list):
                return []
            out = []
            for e in entries[:limit]:
                if not isinstance(e, dict):
                    continue
                mapped = dict(e)
                mapped.setdefault("name", e.get("authorizationName"))
                mapped.setdefault("id", e.get("index"))
                if e.get("timeYear"):
                    mapped.setdefault("date", "%04d-%02d-%02dT%02d:%02d:%02dZ" % (
                        int(e.get("timeYear", 0)), int(e.get("timeMonth", 0)),
                        int(e.get("timeDay", 0)), int(e.get("timeHour", 0)),
                        int(e.get("timeMinute", e.get("timeMin", 0))),
                        int(e.get("timeSecond", e.get("timeSec", 0)))))
                out.append(mapped)
            return out
        except Exception as exc:
            logger.error("NukiHub get_log failed: %s", exc)
            return []

    def force_sync(self) -> None:
        """Ask the hub to re-read lock state + keypad from the device (BLE)."""
        if self._settings.nuki_dry_run:
            logger.info("DRY_RUN: skip force sync")
            return
        self._cache = None
        self._publish("lock/query/lockstate", "1")
        self._publish("lock/query/keypad", "1")
