"""Smoke tests for :class:`tvlinux.hud_panels.party_panel.PartyPanel`."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from tvlinux.analyzers import AnalyzerHub, Event, EventKind
from tvlinux.hud_panels import PartyPanel
from tvlinux.hunt_parser import PartyHuntSession, PartyMember


def _session() -> PartyHuntSession:
    return PartyHuntSession(
        session=timedelta(hours=2),
        loot_type="Market",
        loot=10_000_000,
        supplies=2_000_000,
        balance=8_000_000,
        members=[
            PartyMember(name="Low", loot=1_000_000, supplies=500_000, balance=500_000),
            PartyMember(name="Mid", loot=3_000_000, supplies=500_000, balance=2_500_000),
            PartyMember(name="Top", loot=6_000_000, supplies=1_000_000, balance=5_000_000),
        ],
    )


def test_party_panel_starts_empty(qapp):
    bus = AnalyzerHub()
    panel = PartyPanel(bus)
    assert panel.session is None
    assert panel.preferred_size().height() == 0.0


def test_party_panel_ingests_update_and_sorts_by_balance(qapp):
    bus = AnalyzerHub()
    panel = PartyPanel(bus)
    bus.publish(
        Event(
            analyzer_id="t",
            kind=EventKind.PARTY_HUNT_UPDATE,
            data=asdict(_session()),
        )
    )
    assert panel.session is not None
    assert [m.name for m in panel.members_sorted] == ["Top", "Mid", "Low"]
    assert panel.preferred_size().height() > 0.0


def test_party_panel_paint_smoke(qapp):
    bus = AnalyzerHub()
    panel = PartyPanel(bus)
    bus.publish(
        Event(
            analyzer_id="t",
            kind=EventKind.PARTY_HUNT_UPDATE,
            data=asdict(_session()),
        )
    )
    img = QImage(320, 240, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    try:
        panel.paint(painter, QRectF(0.0, 0.0, 320.0, 240.0))
    finally:
        painter.end()


def test_party_panel_handles_missing_members(qapp):
    bus = AnalyzerHub()
    panel = PartyPanel(bus)
    # Only the headline totals, no member rows. Panel should ingest
    # without raising and simply have no member rows to sort.
    data = asdict(_session())
    data["members"] = []
    bus.publish(Event(analyzer_id="t", kind=EventKind.PARTY_HUNT_UPDATE, data=data))
    assert panel.session is not None
    assert panel.members_sorted == []


def test_party_panel_ignores_malformed_payload(qapp):
    bus = AnalyzerHub()
    panel = PartyPanel(bus)
    bus.publish(Event(analyzer_id="t", kind=EventKind.PARTY_HUNT_UPDATE, data={}))
    assert panel.session is None
