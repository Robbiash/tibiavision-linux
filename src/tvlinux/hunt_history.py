"""Persistent log of hunt sessions the user has captured.

A ``HuntRecord`` is everything we know about a single solo Hunt Analyser paste,
plus the timestamp we captured it and any notes the user added. The store is a
simple JSON-backed list (roundtripped via the dataclass) so it plays nicely
with the rest of the app, stays human-editable, and doesn't require a DB.

The store is intentionally agnostic to the UI -- pages in
``tvlinux.pages.hunt_history_page`` read/write via this API and that is the
only place that cares about Qt. Tests can therefore drive the store in-process
without mounting a QApplication.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from .hunt_parser import HuntSession
from .logging_config import get_logger
from .paths import config_dir

log = get_logger(__name__)


def hunt_history_path() -> Path:
    return config_dir() / "hunt_history.json"


@dataclass
class HuntRecord:
    """One captured solo hunt session + user-editable metadata."""

    id: UUID = field(default_factory=uuid4)
    captured_at: float = field(default_factory=time.time)  # unix seconds
    character: str = ""
    session_sec: int = 0
    xp_gain: int = 0
    raw_xp_gain: int = 0
    xp_per_h: int = 0
    raw_xp_per_h: int = 0
    loot: int = 0
    supplies: int = 0
    balance: int = 0
    damage: int = 0
    damage_per_h: int = 0
    healing: int = 0
    healing_per_h: int = 0
    notes: str = ""
    raw_text: str = ""

    # -- Derived (not persisted as additional columns; computed on read) -------

    @property
    def session(self) -> timedelta:
        return timedelta(seconds=int(self.session_sec))

    @property
    def profit(self) -> int:
        """Alias for balance; exposed as 'profit' for UI labels."""
        return self.balance

    # -- (De)serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = str(self.id)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> HuntRecord:
        def _int(k: str, default: int = 0) -> int:
            try:
                return int(data.get(k, default))
            except (TypeError, ValueError):
                return default

        raw_id = data.get("id")
        try:
            rec_id = UUID(raw_id) if isinstance(raw_id, str) else uuid4()
        except ValueError:
            rec_id = uuid4()
        return cls(
            id=rec_id,
            captured_at=float(data.get("captured_at", time.time())),
            character=str(data.get("character", "")),
            session_sec=_int("session_sec"),
            xp_gain=_int("xp_gain"),
            raw_xp_gain=_int("raw_xp_gain"),
            xp_per_h=_int("xp_per_h"),
            raw_xp_per_h=_int("raw_xp_per_h"),
            loot=_int("loot"),
            supplies=_int("supplies"),
            balance=_int("balance"),
            damage=_int("damage"),
            damage_per_h=_int("damage_per_h"),
            healing=_int("healing"),
            healing_per_h=_int("healing_per_h"),
            notes=str(data.get("notes", "")),
            raw_text=str(data.get("raw_text", "")),
        )

    @classmethod
    def from_session(
        cls,
        session: HuntSession,
        *,
        character: str = "",
        raw_text: str = "",
        notes: str = "",
    ) -> HuntRecord:
        return cls(
            captured_at=time.time(),
            character=character,
            session_sec=int(session.session.total_seconds()),
            xp_gain=int(session.xp_gain),
            raw_xp_gain=int(session.raw_xp_gain),
            xp_per_h=int(session.xp_per_h),
            raw_xp_per_h=int(session.raw_xp_per_h),
            loot=int(session.loot),
            supplies=int(session.supplies),
            balance=int(session.balance),
            damage=int(session.damage),
            damage_per_h=int(session.damage_per_h),
            healing=int(session.healing),
            healing_per_h=int(session.healing_per_h),
            notes=notes,
            raw_text=raw_text,
        )


class HuntHistoryStore:
    """JSON-backed list of HuntRecords with stable ordering (newest first)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or hunt_history_path()
        self._records: list[HuntRecord] = []
        self.load()

    def __len__(self) -> int:
        return len(self._records)

    def all(self) -> list[HuntRecord]:
        return list(self._records)

    def get(self, record_id: UUID) -> HuntRecord | None:
        for r in self._records:
            if r.id == record_id:
                return r
        return None

    def filter_by_character(self, character: str) -> list[HuntRecord]:
        character = character.strip().lower()
        return [r for r in self._records if r.character.lower() == character]

    def filter_since(self, since_epoch: float) -> list[HuntRecord]:
        return [r for r in self._records if r.captured_at >= since_epoch]

    def add(self, record: HuntRecord) -> None:
        self._records.insert(0, record)
        self.save()
        log.info(
            "hunt_history.added",
            character=record.character,
            profit=record.balance,
            session_sec=record.session_sec,
        )

    def update(self, record: HuntRecord) -> None:
        for i, r in enumerate(self._records):
            if r.id == record.id:
                self._records[i] = record
                self.save()
                return

    def remove(self, record_id: UUID) -> None:
        before = len(self._records)
        self._records = [r for r in self._records if r.id != record_id]
        if len(self._records) != before:
            self.save()

    def clear(self) -> None:
        self._records.clear()
        self.save()

    # -- Persistence ----------------------------------------------------------

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("hunt_history.load_failed", error=str(e))
            return
        self._records = [HuntRecord.from_dict(d) for d in data.get("records", [])]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "records": [r.to_dict() for r in self._records],
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


__all__ = [
    "HuntHistoryStore",
    "HuntRecord",
    "hunt_history_path",
]
