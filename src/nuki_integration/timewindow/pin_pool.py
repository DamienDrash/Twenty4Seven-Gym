"""Reine Zeitfenster-PIN-Logik (stdlib-only, keine DB, kein Netz).

Portiert aus ``backend/services/nuki_pin/timewindow_pin_pool.py`` (getestet):
  - Geschäftszeiten: Mo–Fr 08:00–21:00, Sa 08:00–15:00, So geschlossen — die
    zentrale Definition liegt in ``datetime_utils.BUSINESS_HOURS`` und wird auch
    vom Auto-Lock genutzt (kein Auseinanderlaufen).
  - Off-Peak (= Keypad-Zutritt) = Komplement. Sonntag ganztägig off-peak.
  - Pro Off-Peak-Stunden-Bucket: 4 Codes ("Pool-Tiefe 4") → 24×4 = 96/200 Slots.
  - Zusätzlich 5 Business-Hours-Fallback-Codes (og-bh-*) → 101 Slots gesamt.
  - Jeder Slot = eine Nuki-Autorisierung (type 13) mit allowedFromTime/UntilTime
    (Stundenfenster) + allowedWeekDays (Bitmaske Mo=64 … So=1).
  - PIN-Werte rotieren täglich (nur online); die Slots bleiben fest.
  - Anti-Repeat: Mitglied bekommt für seinen (Wochentag,Stunde)-Slot möglichst
    einen anderen der 4 Pool-Codes als in Vorwochen (Tiefe 4).

Nuki-PIN-Regeln: genau 6 Ziffern 1–9 (keine 0), darf nicht mit "12" beginnen,
pro Keypad eindeutig. allowedFromTime/UntilTime = Minuten seit Mitternacht (0–1439).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from ..datetime_utils import BUSINESS_HOURS, is_open

POOL_PER_HOUR = 4          # Codes je Off-Peak-Stunde (Betreiber-Entscheid)
KEYPAD_CODE_LIMIT = 200    # Hardware-Grenze Nuki Keypad
ANTI_REPEAT_DEPTH = POOL_PER_HOUR  # Wochen bis erzwungene Wiederholung

# Business-Hours-Fallback: 5 Codes, 08:00–21:00, Mo–Sa (NICHT Sonntag), täglich
# rotierend. Werden an Buchungen INNERHALB der Geschäftszeiten zugestellt.
FALLBACK_POOL = 5
FALLBACK_HOUR = 24                 # Sentinel-"Stunde" für die Fallback-Slots (og-bh-*)
FALLBACK_FROM_MIN = 8 * 60         # 08:00
FALLBACK_UNTIL_MIN = 21 * 60       # 21:00

# Geschäftszeiten (Mo–Fr 08–21, Sa 08–15, So zu) kommen aus der zentralen Quelle
# ``datetime_utils.BUSINESS_HOURS`` — hier nur re-exportiert für Abwärtskompat.
# ``is_open``/``needs_keypad_code`` bauen darauf auf.

# Nuki-Wochentags-Bits: Mo=64 .. So=1  →  bit = 64 >> weekday_index
WEEKDAY_BIT = {wd: 64 >> wd for wd in range(7)}
WEEKDAY_NAME = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}

_PIN_PATTERN = re.compile(r"^[1-9]{6}$")


def new_rng() -> random.Random:
    """Kryptographisch tauglicher RNG für den Betrieb (kein reproduzierbarer Seed)."""
    return random.SystemRandom()


# ── PIN-Erzeugung / Validierung ──────────────────────────────────────────
def validate_keypad_code(code: str) -> None:
    if not _PIN_PATTERN.match(code):
        raise ValueError(f"PIN muss genau 6 Ziffern 1–9 sein: {code!r}")
    if code.startswith("12"):
        raise ValueError(f"PIN darf nicht mit '12' beginnen: {code!r}")


def gen_pin(rng: random.Random, used: set[str]) -> str:
    """Erzeuge einen eindeutigen, regelkonformen 6-stelligen PIN (1–9)."""
    for _ in range(10_000):
        code = "".join(rng.choice("123456789") for _ in range(6))
        if code.startswith("12") or code in used:
            continue
        used.add(code)
        return code
    raise RuntimeError("PIN-Raum erschöpft (sollte bei 200 Codes nie passieren)")


# ── Off-Peak-Berechnung ──────────────────────────────────────────────────
# ``is_open`` stammt aus ``..datetime_utils`` (zentrale Geschäftszeiten) und ist
# oben importiert; hier keine eigene, potenziell abweichende Kopie mehr.
def needs_keypad_code(weekday: int, hour: int) -> bool:
    """True iff eine Buchung zu (weekday, hour) einen Keypad-Code braucht.

    Buchungen **innerhalb** der Geschäftszeiten (Tür besetzt/offen) → kein Code.
    Nur Off-Peak-Buchungen brauchen eine PIN (Guard).
    """
    return not is_open(weekday, hour)


@dataclass
class Bucket:
    """Ein Off-Peak-Stunden-Bucket über die Woche."""
    hour: int
    weekday_mask: int            # Nuki allowedWeekDays
    weekdays: list[int]          # 0=Mo .. 6=So
    from_min: int | None = None  # Override (z.B. Business-Hours-Fallback 08:00–21:00)
    until_min: int | None = None

    @property
    def from_time(self) -> int:  # Minuten seit Mitternacht
        return self.from_min if self.from_min is not None else self.hour * 60

    @property
    def until_time(self) -> int:
        if self.until_min is not None:
            return self.until_min
        # Nuki erlaubt 0–1439; 24:00 (=1440) gibt es nicht → 23:00-Bucket
        # endet auf 23:59.
        return min((self.hour + 1) * 60, 1439)

    def label(self) -> str:
        days = "".join(WEEKDAY_NAME[wd] for wd in self.weekdays)
        return f"{self.hour:02d}:00–{(self.hour + 1) % 24:02d}:00 [{days}]"


def compute_offpeak_buckets() -> list[Bucket]:
    """Alle Stunden-Buckets, die an mind. einem Wochentag off-peak sind."""
    buckets: list[Bucket] = []
    for hour in range(24):
        weekdays = [wd for wd in range(7) if not is_open(wd, hour)]
        if not weekdays:
            continue  # Stunde an allen Tagen besetzt → kein Keypad-Slot
        mask = 0
        for wd in weekdays:
            mask |= WEEKDAY_BIT[wd]
        buckets.append(Bucket(hour=hour, weekday_mask=mask, weekdays=weekdays))
    return buckets


# ── Slot-/Autorisierungs-Plan ────────────────────────────────────────────
@dataclass
class Slot:
    """Ein persistenter Keypad-Slot = eine Nuki-Autorisierung."""
    bucket: Bucket
    pool_index: int              # 0..POOL_PER_HOUR-1
    code: str = ""               # rotiert täglich

    @property
    def name(self) -> str:
        if self.bucket.hour == FALLBACK_HOUR:
            return f"og-bh-p{self.pool_index}"
        return f"og-h{self.bucket.hour:02d}-p{self.pool_index}"


def build_slots(buckets: list[Bucket]) -> list[Slot]:
    slots: list[Slot] = []
    for b in buckets:
        for p in range(POOL_PER_HOUR):
            slots.append(Slot(bucket=b, pool_index=p))
    return slots


def build_fallback_slots() -> list[Slot]:
    """Die 5 Business-Hours-Fallback-Slots (og-bh-p0..p4), 08:00–21:00, Mo–Sa.

    Gültig an allen Geschäftstagen der zentralen ``BUSINESS_HOURS`` (Sonntag fällt
    raus, weil geschlossen → Maske 126 statt 127). Ein Nuki-Keypad-Code hat genau
    EIN tägliches Zeitfenster: die engere Sa-Grenze (08:00–15:00) wird nicht über
    das Code-Fenster, sondern über das Routing durchgesetzt — Sa ab 15:00 ist
    Off-Peak (``is_open`` = False) → dann greift ein 96er-Stunden-Code, kein
    Fallback. Die Fallback-Codes werden also nur für Mo–Fr 08–21 und Sa 08–15
    zugestellt.
    """
    weekdays = [wd for wd, span in BUSINESS_HOURS.items() if span is not None]
    mask = 0
    for wd in weekdays:
        mask |= WEEKDAY_BIT[wd]
    b = Bucket(hour=FALLBACK_HOUR, weekday_mask=mask, weekdays=weekdays,
               from_min=FALLBACK_FROM_MIN, until_min=FALLBACK_UNTIL_MIN)
    return [Slot(bucket=b, pool_index=p) for p in range(FALLBACK_POOL)]


def expected_slot_count() -> int:
    """Anzahl aller täglich rotierten Slots: 96 Off-Peak + 5 Business-Hours-Fallback.

    Zentrale Quelle für die Rotations-Idempotenz (``rotate_daily``) und den
    Admin-Status (``app.timewindow_status``), damit beide alle 101 Slots erwarten.
    """
    return POOL_PER_HOUR * len(compute_offpeak_buckets()) + FALLBACK_POOL


def choose_fallback_index(recent_indices: list[int]) -> int:
    """Anti-Repeat unter den 5 Fallback-Codes (Tiefe 4 → rotiert durch alle 5)."""
    recent = set(recent_indices[-(FALLBACK_POOL - 1):])
    candidates = [p for p in range(FALLBACK_POOL) if p not in recent]
    if candidates:
        return candidates[0]
    return recent_indices[-1] if recent_indices else 0


def rotate_pins(slots: list[Slot], rng: random.Random | None = None) -> None:
    """Weist allen Slots frische, eindeutige PINs zu (tägliche Rotation)."""
    rng = rng or new_rng()
    used: set[str] = set()
    for s in slots:
        s.code = gen_pin(rng, used)


def assert_within_budget(slots: list[Slot]) -> None:
    """Grenzwächter: niemals mehr als KEYPAD_CODE_LIMIT Slots."""
    if len(slots) > KEYPAD_CODE_LIMIT:
        raise ValueError(f"Slot-Budget überschritten: {len(slots)} > {KEYPAD_CODE_LIMIT}")


# ── Anti-Repeat-Zuweisung ────────────────────────────────────────────────
@dataclass
class AssignmentLog:
    """Merkt sich, welchen Pool-Index ein Mitglied je (Wochentag,Stunde) hatte."""
    history: dict[tuple[str, int, int], list[int]] = field(default_factory=dict)

    def assign(self, member: str, weekday: int, hour: int) -> int:
        key = (member, weekday, hour)
        prev = self.history.get(key, [])
        recent = set(prev[-ANTI_REPEAT_DEPTH:])
        candidates = [p for p in range(POOL_PER_HOUR) if p not in recent]
        chosen = candidates[0] if candidates else prev[-1]  # Reuse erlaubt
        self.history.setdefault(key, []).append(chosen)
        return chosen


def choose_pool_index(recent_indices: list[int]) -> int:
    """Anti-Repeat-Wahl aus einer chronologischen Vergangenheits-Liste (alt→neu)."""
    recent = set(recent_indices[-ANTI_REPEAT_DEPTH:])
    candidates = [p for p in range(POOL_PER_HOUR) if p not in recent]
    if candidates:
        return candidates[0]
    return recent_indices[-1] if recent_indices else 0
