"""HuntHistoryStore persistence + HuntRecord conversion."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from tvlinux.hunt_history import HuntHistoryStore, HuntRecord
from tvlinux.hunt_parser import HuntSession


def _sess() -> HuntSession:
    return HuntSession(
        session=timedelta(hours=1, minutes=30),
        xp_gain=450_000,
        raw_xp_gain=420_000,
        xp_per_h=300_000,
        raw_xp_per_h=280_000,
        loot=1_500_000,
        supplies=500_000,
        balance=1_000_000,
        damage=600_000,
        damage_per_h=400_000,
        healing=180_000,
        healing_per_h=120_000,
        captured_at=1234.0,
    )


def test_record_from_session_copies_fields() -> None:
    rec = HuntRecord.from_session(_sess(), character="Bobby", notes="Good run")
    assert rec.character == "Bobby"
    assert rec.balance == 1_000_000
    assert rec.notes == "Good run"
    assert rec.session_sec == 90 * 60


def test_store_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "hist.json"
    store = HuntHistoryStore(path=p)
    assert len(store) == 0
    rec = HuntRecord.from_session(_sess(), character="Bobby")
    store.add(rec)
    assert len(store) == 1

    store2 = HuntHistoryStore(path=p)
    assert len(store2) == 1
    assert store2.all()[0].character == "Bobby"
    assert store2.all()[0].balance == 1_000_000


def test_store_insert_order_newest_first(tmp_path: Path) -> None:
    store = HuntHistoryStore(path=tmp_path / "h.json")
    a = HuntRecord(character="A", captured_at=100.0)
    b = HuntRecord(character="B", captured_at=200.0)
    store.add(a)
    store.add(b)
    names = [r.character for r in store.all()]
    assert names == ["B", "A"]


def test_filter_by_character(tmp_path: Path) -> None:
    store = HuntHistoryStore(path=tmp_path / "h.json")
    store.add(HuntRecord(character="Alice"))
    store.add(HuntRecord(character="Bob"))
    store.add(HuntRecord(character="alice"))
    assert len(store.filter_by_character("alice")) == 2


def test_remove_and_update(tmp_path: Path) -> None:
    store = HuntHistoryStore(path=tmp_path / "h.json")
    rec = HuntRecord(character="Bobby", balance=100)
    store.add(rec)
    rec.notes = "updated"
    store.update(rec)
    assert store.get(rec.id) is not None
    assert store.get(rec.id).notes == "updated"  # type: ignore[union-attr]
    store.remove(rec.id)
    assert store.get(rec.id) is None
