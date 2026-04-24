"""Parsers for Tibia's "Copy to clipboard" hunt output.

Tibia's in-game Hunt Analyser and Party Hunt widgets both expose a
right-click menu option that dumps the session's stats as a plain-text
block onto the clipboard. That's the fastest, zero-OCR way we can pull
live numbers into our HUD: the player copies, we listen on the
clipboard, we parse, we render.

Two payload shapes come out of Tibia:

Hunt Analyser (solo)
--------------------
::

    Session data: From 2025-04-01, 20:30:00 to 2025-04-02, 00:18:00
    Session: 03:48h
    Raw XP Gain: 33,673,350
    XP Gain: 71,462,604
    Raw XP/h: 521,857
    XP/h: 782,770
    Loot: 1,385,541
    Supplies: 509,120
    Balance: 876,421
    Damage: 19,844,957
    Damage/h: 5,381,491
    Healing: 6,415,088
    Healing/h: 1,744,415

Party Hunt
----------
::

    Session data: From 2025-04-01, 20:30:00 to 2025-04-01, 23:40:00
    Session: 03:10h
    Loot Type: Market
    Loot: 28,028,157
    Supplies: 6,748,447
    Balance: 21,279,710
    Abbadinos
        Loot: 18,908,088
        Supplies: 3,092,003
        Balance: 12,032,030
        Damage: ...
        Healing: ...
    La Kogzita
        ...

Both formats are locale-sensitive (commas vs periods as thousands
separators). The parsers normalise them and ignore whatever decorative
header Tibia prepends. Parsers return ``None`` on any failure rather
than raising, so the clipboard watcher can try all parsers in order
without dropping into exception handling for the 99.9% of clipboards
that aren't Tibia payloads.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass
class HuntSession:
    """One Hunt Analyser snapshot."""

    session: timedelta
    raw_xp_gain: int
    xp_gain: int
    raw_xp_per_h: int
    xp_per_h: int
    loot: int
    supplies: int
    balance: int
    damage: int
    damage_per_h: int
    healing: int
    healing_per_h: int
    captured_at: float = field(default_factory=time.monotonic)


@dataclass
class PartyMember:
    """One row in the Party Hunt per-member breakdown."""

    name: str
    loot: int
    supplies: int
    balance: int
    damage: int | None = None
    healing: int | None = None


@dataclass
class PartyHuntSession:
    """One Party Hunt snapshot."""

    session: timedelta
    loot_type: str
    loot: int
    supplies: int
    balance: int
    members: list[PartyMember] = field(default_factory=list)
    captured_at: float = field(default_factory=time.monotonic)


# ``1,234,567`` OR ``1.234.567`` OR ``1 234 567`` embedded in a larger
# string like ``"1,234,567 gp (after tax)"``.
_NUMBER_RE = re.compile(r"[-\u2212]?\d[\d.,\s\u00a0]*")


def _parse_int(raw: str) -> int | None:
    """Extract and parse the first integer-looking token in ``raw``.

    We intentionally accept decorated values such as ``"1,234 gp"`` and
    ``"21,279,710 (5,319,927 each)"`` because Tibia clipboard payloads and
    third-party tools may append helper text after the number.
    """
    normalized = raw.strip().replace("\u2212", "-").replace("\u2013", "-")
    match = _NUMBER_RE.search(normalized)
    if match is None:
        return None
    cleaned = (
        match.group(0).replace(",", "").replace(".", "").replace(" ", "").replace("\u00a0", "")
    )
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


_DURATION_RE = re.compile(r"(\d+):(\d+)(?::(\d+))?\s*h?", re.IGNORECASE)


def _parse_duration(raw: str) -> timedelta | None:
    """Parse ``"03:48h"``, ``"03:48:12"`` -> :class:`timedelta`."""
    m = _DURATION_RE.search(raw)
    if m is None:
        return None
    h = int(m.group(1))
    mm = int(m.group(2))
    ss = int(m.group(3)) if m.group(3) else 0
    return timedelta(hours=h, minutes=mm, seconds=ss)


def _extract_field(lines: list[str], name: str) -> str | None:
    """Find ``"<name>: <value>"`` (case-insensitive) and return the value."""
    prefix = f"{name.lower()}:"
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped[len(prefix) :].strip()
    return None


def _required_int(lines: list[str], name: str) -> int | None:
    value = _extract_field(lines, name)
    if value is None:
        return None
    return _parse_int(value)


def parse_hunt_analyser(text: str) -> HuntSession | None:
    """Parse the Hunt Analyser copy-to-clipboard payload.

    Returns ``None`` if any of the required fields is missing or
    unparseable.
    """
    if not isinstance(text, str) or "session:" not in text.lower():
        return None
    lines = text.splitlines()

    session = _parse_duration(_extract_field(lines, "Session") or "")
    raw_xp_gain = _required_int(lines, "Raw XP Gain")
    xp_gain = _required_int(lines, "XP Gain")
    raw_xp_per_h = _required_int(lines, "Raw XP/h")
    xp_per_h = _required_int(lines, "XP/h")
    loot = _required_int(lines, "Loot")
    supplies = _required_int(lines, "Supplies")
    balance = _required_int(lines, "Balance")
    damage = _required_int(lines, "Damage")
    damage_per_h = _required_int(lines, "Damage/h")
    healing = _required_int(lines, "Healing")
    healing_per_h = _required_int(lines, "Healing/h")

    required = (
        session,
        raw_xp_gain,
        xp_gain,
        raw_xp_per_h,
        xp_per_h,
        loot,
        supplies,
        balance,
        damage,
        damage_per_h,
        healing,
        healing_per_h,
    )
    if any(r is None for r in required):
        return None

    # Help type-checkers: after the None check all are non-None.
    return HuntSession(
        session=session,  # type: ignore[arg-type]
        raw_xp_gain=raw_xp_gain,  # type: ignore[arg-type]
        xp_gain=xp_gain,  # type: ignore[arg-type]
        raw_xp_per_h=raw_xp_per_h,  # type: ignore[arg-type]
        xp_per_h=xp_per_h,  # type: ignore[arg-type]
        loot=loot,  # type: ignore[arg-type]
        supplies=supplies,  # type: ignore[arg-type]
        balance=balance,  # type: ignore[arg-type]
        damage=damage,  # type: ignore[arg-type]
        damage_per_h=damage_per_h,  # type: ignore[arg-type]
        healing=healing,  # type: ignore[arg-type]
        healing_per_h=healing_per_h,  # type: ignore[arg-type]
    )


def parse_party_hunt(text: str) -> PartyHuntSession | None:
    """Parse the Party Hunt copy-to-clipboard payload.

    Party Hunt is distinguishable from the solo Hunt Analyser by its
    ``Loot Type`` header line and the per-member sub-sections that
    follow the totals. We use ``Loot Type`` as the sniff check so we
    don't mis-parse a solo dump.
    """
    if not isinstance(text, str):
        return None
    lowered = text.lower()
    if "loot type:" not in lowered or "session:" not in lowered:
        return None

    lines = text.splitlines()
    session = _parse_duration(_extract_field(lines, "Session") or "")
    loot_type = _extract_field(lines, "Loot Type") or ""
    loot = _required_int(lines, "Loot")
    supplies = _required_int(lines, "Supplies")
    balance = _required_int(lines, "Balance")

    if any(v is None for v in (session, loot, supplies, balance)):
        return None

    members = _parse_party_members(lines)

    return PartyHuntSession(
        session=session,  # type: ignore[arg-type]
        loot_type=loot_type,
        loot=loot,  # type: ignore[arg-type]
        supplies=supplies,  # type: ignore[arg-type]
        balance=balance,  # type: ignore[arg-type]
        members=members,
    )


def _parse_party_members(lines: list[str]) -> list[PartyMember]:
    """Walk the lines after the totals and extract per-member rows.

    A "member block" starts with an un-indented line that is not one of
    the known total headers, followed by indented ``Loot: / Supplies: /
    Balance:`` lines (and optionally ``Damage: / Healing:``).
    """
    # Known total-row headers; everything else at indent 0 is a name.
    TOTAL_HEADERS = {
        "session data",
        "session",
        "loot type",
        "loot",
        "supplies",
        "balance",
        "damage",
        "damage/h",
        "healing",
        "healing/h",
        "raw xp gain",
        "xp gain",
        "raw xp/h",
        "xp/h",
    }

    members: list[PartyMember] = []
    current_name: str | None = None
    current_stats: dict[str, int | None] = {}

    def flush() -> None:
        nonlocal current_name, current_stats
        if current_name is None:
            return
        loot = current_stats.get("loot")
        supplies = current_stats.get("supplies")
        balance = current_stats.get("balance")
        if loot is not None and supplies is not None and balance is not None:
            members.append(
                PartyMember(
                    name=current_name,
                    loot=loot,
                    supplies=supplies,
                    balance=balance,
                    damage=current_stats.get("damage"),
                    healing=current_stats.get("healing"),
                )
            )
        current_name = None
        current_stats = {}

    for raw_line in lines:
        if not raw_line.strip():
            continue
        is_indented = raw_line.startswith((" ", "\t"))
        stripped = raw_line.strip()
        head = stripped.split(":", 1)[0].strip().lower() if ":" in stripped else stripped.lower()

        if not is_indented:
            # Un-indented line. Either a new member name, or one of the
            # total headers (which we already consumed above).
            if head in TOTAL_HEADERS:
                flush()  # defensive: a stray total header after members
                continue
            # Lines that aren't ``foo: bar`` and aren't indented are
            # treated as member names. Real member rows follow this
            # convention in every Party Hunt copy we've seen.
            if ":" in stripped:
                continue
            flush()
            current_name = stripped
            current_stats = {}
            continue

        # Indented: a member's sub-stat line.
        if current_name is None or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key_l = key.strip().lower()
        if key_l in {"loot", "supplies", "balance", "damage", "healing"}:
            current_stats[key_l] = _parse_int(value)

    flush()
    return members


__all__ = [
    "HuntSession",
    "PartyHuntSession",
    "PartyMember",
    "parse_hunt_analyser",
    "parse_party_hunt",
]
