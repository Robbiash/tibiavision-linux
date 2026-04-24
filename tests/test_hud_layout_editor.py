"""Tests for the HUD layout editor companion window.

The editor drags widgets around in its own process and writes
``hud_layout.json`` when the user hits Save. Under the offscreen Qt
platform we can't synthesise real mouse drags, but we can:

- Build the editor from a SmartHud with a couple of fake panels.
- Assert each PanelTile ends up at the panel's current slot rect.
- Call ``_on_save`` directly after moving tiles programmatically and
  confirm the HUD slot rects + persisted JSON reflect the new
  positions.
- Call ``_reset_positions`` and confirm the override file is deleted
  and tiles go back to default anchor positions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QPainter

from tvlinux.analyzers import AnalyzerHub
from tvlinux.hud_layout_editor import HudLayoutEditor, PanelTile
from tvlinux.smart_hud import HudPanel, SmartHud


@dataclass
class _FakePanel(HudPanel):
    id: str = "fake"
    anchor: str = "top_left"  # type: ignore[assignment]

    def preferred_size(self) -> QSizeF:
        return QSizeF(120.0, 60.0)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        del painter, rect


def _hud(tmp_path: Path) -> SmartHud:
    bus = AnalyzerHub()
    return SmartHud(bus=bus, layout_path=tmp_path / "hud_layout.json")


def test_editor_tiles_mirror_panel_rects(qapp, tmp_path):
    hud = _hud(tmp_path)
    hud.register_panel(_FakePanel(id="a", anchor="top_left"))
    hud.register_panel(_FakePanel(id="b", anchor="top_right"))

    editor = HudLayoutEditor(hud)
    try:
        assert set(editor._tiles.keys()) == {"a", "b"}  # type: ignore[attr-defined]
        for pid, tile in editor._tiles.items():  # type: ignore[attr-defined]
            slot = hud._slots[pid]
            assert tile.x() == int(slot.rect.x())
            assert tile.y() == int(slot.rect.y())
            assert isinstance(tile, PanelTile)
            assert tile.panel_id == pid
    finally:
        editor.close()
        editor.deleteLater()
        qapp.processEvents()


def test_editor_save_writes_layout_json(qapp, tmp_path):
    hud = _hud(tmp_path)
    hud.register_panel(_FakePanel(id="a", anchor="top_left"))
    hud.register_panel(_FakePanel(id="b", anchor="top_right"))

    editor = HudLayoutEditor(hud)
    try:
        # Simulate a drag by moving each tile to an explicit position.
        editor._tiles["a"].move(123, 45)  # type: ignore[attr-defined]
        editor._tiles["b"].move(678, 90)  # type: ignore[attr-defined]

        # Directly invoke the Save handler; under offscreen Qt we can't
        # fire the click through QTest without flakiness.
        editor._on_save()  # type: ignore[attr-defined]

        layout_file = hud._layout_path  # type: ignore[attr-defined]
        assert layout_file.exists()
        payload = json.loads(layout_file.read_text())
        assert payload["positions"]["a"] == {"x": 123.0, "y": 45.0}
        assert payload["positions"]["b"] == {"x": 678.0, "y": 90.0}

        # The live HUD slot rects must also carry the override; otherwise
        # the next repaint would still show the old layout until the app
        # restarts.
        assert hud._slots["a"].rect.topLeft() == QPointF(123.0, 45.0)
        assert hud._slots["b"].rect.topLeft() == QPointF(678.0, 90.0)
    finally:
        qapp.processEvents()


def test_editor_reset_clears_override_file(qapp, tmp_path):
    hud = _hud(tmp_path)
    hud.register_panel(_FakePanel(id="a", anchor="top_left"))
    editor = HudLayoutEditor(hud)
    try:
        editor._tiles["a"].move(222, 333)  # type: ignore[attr-defined]
        editor._on_save()  # type: ignore[attr-defined]
        assert hud._layout_path.exists()  # type: ignore[attr-defined]

        # Build a fresh editor (the old one was closed by Save) and
        # hit Reset. The override file should be gone and the tile
        # should be back at the default top-left anchor (16 px margin).
        editor2 = HudLayoutEditor(hud)
        try:
            editor2._reset_positions()  # type: ignore[attr-defined]
            assert not hud._layout_path.exists()  # type: ignore[attr-defined]
            tile = editor2._tiles["a"]  # type: ignore[attr-defined]
            assert tile.x() == 16
            assert tile.y() == 16
        finally:
            editor2.close()
            editor2.deleteLater()
            qapp.processEvents()
    finally:
        qapp.processEvents()


def test_panel_tile_moved_signal(qapp, tmp_path):
    hud = _hud(tmp_path)
    hud.register_panel(_FakePanel(id="a", anchor="top_left"))

    editor = HudLayoutEditor(hud)
    try:
        tile = editor._tiles["a"]  # type: ignore[attr-defined]
        captured: list[tuple[str, QPointF]] = []
        tile.moved.connect(lambda pid, pos: captured.append((pid, pos)))
        # Manually emit because the QMouseEvent flow is brittle under
        # offscreen Qt.
        tile.moved.emit("a", QPointF(10.0, 20.0))
        assert captured == [("a", QPointF(10.0, 20.0))]
    finally:
        editor.close()
        editor.deleteLater()
        qapp.processEvents()
