"""Tests for :class:`tvlinux.hud_panels.hotbar_panel.HotbarPanel`."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from tvlinux.analyzers import AnalyzerHub, Event, EventKind
from tvlinux.hud_panels import HotbarPanel
from tvlinux.tibia_data import HotkeyBinding


def _rows() -> list[HotkeyBinding]:
    return [
        HotkeyBinding(keysequence="F10", label="exori vis", kind="spell"),
        HotkeyBinding(keysequence="F1", label="great mana potion", kind="item", object_id=237),
        HotkeyBinding(keysequence="F2", label="ultimate healing rune", kind="item", object_id=3160),
    ]


def test_hotbar_panel_sorts_f_keys_numerically():
    # ``_sort_rows`` is a classmethod-style static sorter; exercise it
    # directly so we don't care about the live ``refresh()`` file path.
    sorted_rows = HotbarPanel._sort_rows(_rows())
    assert [r.keysequence for r in sorted_rows] == ["F1", "F2", "F10"]


def test_hotbar_panel_preferred_size_collapses_when_empty(qapp):
    bus = AnalyzerHub()
    panel = HotbarPanel(bus)
    # Force empty state regardless of the local filesystem.
    panel.set_rows(None, [])
    size = panel.preferred_size()
    assert size.height() == 0.0


def test_hotbar_panel_reacts_to_login_detected(qapp, monkeypatch):
    """LOGIN_DETECTED must trigger a ``refresh()`` call."""
    bus = AnalyzerHub()
    panel = HotbarPanel(bus)

    calls: list[int] = []

    def _fake_refresh(*args, **kwargs):
        calls.append(1)

    monkeypatch.setattr(panel, "refresh", _fake_refresh)
    bus.publish(Event(analyzer_id="t", kind=EventKind.LOGIN_DETECTED, data={}))
    assert calls  # at least one refresh call was issued


def test_hotbar_panel_paint_does_not_crash_on_offscreen_surface(qapp):
    bus = AnalyzerHub()
    panel = HotbarPanel(bus)
    panel.set_rows("Sydnee", _rows())

    img = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    try:
        panel.paint(painter, QRectF(0.0, 0.0, 320.0, 200.0))
    finally:
        painter.end()
