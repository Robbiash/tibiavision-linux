"""Smoke tests for :class:`tvlinux.hud_panels.hunt_stats_panel.HuntStatsPanel`."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import timedelta

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from tvlinux.analyzers import AnalyzerHub, Event, EventKind
from tvlinux.hud_panels import HuntStatsPanel
from tvlinux.hunt_parser import HuntSession


def _session(captured_at: float | None = None) -> HuntSession:
    return HuntSession(
        session=timedelta(hours=1),
        raw_xp_gain=500_000,
        xp_gain=1_000_000,
        raw_xp_per_h=500_000,
        xp_per_h=1_000_000,
        loot=1_500_000,
        supplies=500_000,
        balance=1_000_000,
        damage=2_000_000,
        damage_per_h=2_000_000,
        healing=500_000,
        healing_per_h=500_000,
        captured_at=captured_at if captured_at is not None else time.monotonic(),
    )


def test_hunt_stats_panel_starts_empty(qapp):
    bus = AnalyzerHub()
    panel = HuntStatsPanel(bus)
    assert panel.session is None
    assert panel.preferred_size().height() == 0.0


def test_hunt_stats_panel_ingests_update(qapp):
    bus = AnalyzerHub()
    panel = HuntStatsPanel(bus)
    data = asdict(_session())
    bus.publish(Event(analyzer_id="t", kind=EventKind.HUNT_STATS_UPDATE, data=data))
    assert panel.session is not None
    assert panel.session.balance == 1_000_000
    # With a session in hand the panel claims non-zero preferred height.
    assert panel.preferred_size().height() > 0.0


def test_hunt_stats_panel_on_tick_advances_timer(qapp):
    bus = AnalyzerHub()
    panel = HuntStatsPanel(bus)
    session = _session(captured_at=time.monotonic() - 0.5)
    bus.publish(
        Event(analyzer_id="t", kind=EventKind.HUNT_STATS_UPDATE, data=asdict(session))
    )
    baseline = session.session.total_seconds() * 1000.0
    panel.on_tick(100.0)
    # ``live_extrapolate`` uses wall-clock monotonic time, so the live
    # session counter should be strictly greater than the captured value.
    assert panel.session_ms_live > baseline


def test_hunt_stats_panel_paint_smoke(qapp):
    bus = AnalyzerHub()
    panel = HuntStatsPanel(bus)
    bus.publish(
        Event(
            analyzer_id="t",
            kind=EventKind.HUNT_STATS_UPDATE,
            data=asdict(_session()),
        )
    )
    img = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    try:
        panel.paint(painter, QRectF(0.0, 0.0, 320.0, 200.0))
    finally:
        painter.end()


def test_hunt_stats_panel_ignores_malformed_payload(qapp):
    bus = AnalyzerHub()
    panel = HuntStatsPanel(bus)
    # Missing most required keys -> panel must stay empty, not raise.
    bus.publish(
        Event(analyzer_id="t", kind=EventKind.HUNT_STATS_UPDATE, data={"session": 1.0})
    )
    assert panel.session is None
