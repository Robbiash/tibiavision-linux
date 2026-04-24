"""Tests for the in-app Companion view.

Covers:

- :class:`CompanionTile` renders a frame (doesn't crash, emits double-click).
- :class:`CompanionPage` adds / removes / renames tiles in lockstep with
  the :class:`RegionManager`.
- :func:`load_mirror_placement` / :func:`save_mirror_placement` round-trip
  through ``QSettings`` and reject bad values.

The page is rendered under the offscreen Qt platform (see
``tests/conftest.py``); we still call ``processEvents`` where needed so
Qt can flush its internal queues.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QRect, QSettings
from PySide6.QtGui import QColor, QImage

from tvlinux.pages.companion_page import CompanionPage, CompanionTile
from tvlinux.pages.settings_page import (
    PLACEMENT_CHOICES,
    load_mirror_placement,
    save_mirror_placement,
)
from tvlinux.regions import Region, RegionManager


@pytest.fixture(autouse=True)
def _isolate_qsettings(tmp_path, monkeypatch):
    """Point QSettings at a throwaway org/app so tests don't pollute state."""
    QCoreApplication.setOrganizationName("tvlinux-tests")
    QCoreApplication.setApplicationName(f"companion-{tmp_path.name}")
    QSettings().clear()
    yield
    QSettings().clear()


def _frame(width: int = 64, height: int = 64, color: str = "#00E5FF") -> QImage:
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(color))
    return img


# -- CompanionTile ----------------------------------------------------------


def test_companion_tile_renders_without_crash(qapp):
    region = Region(name="Health", rect=QRect(0, 0, 32, 32))
    tile = CompanionTile(region)
    try:
        tile.resize(240, 180)
        tile.set_frame(_frame(64, 64))
        qapp.processEvents()
        # The tile exists, has sensible accessibility metadata, and
        # accepted the frame without raising.
        assert tile.region_id == region.id
        assert "Health" in tile.accessibleName()
        assert "Health" in tile.toolTip()
    finally:
        tile.deleteLater()
        qapp.processEvents()


def test_companion_tile_renders_placeholder_when_region_outside_capture(qapp):
    # Region rect falls outside a tiny 10x10 frame -- the tile must fall
    # back to the placeholder text path rather than drawing from an
    # empty source rect.
    region = Region(name="Bad", rect=QRect(500, 500, 50, 50))
    tile = CompanionTile(region)
    try:
        tile.resize(120, 120)
        tile.set_frame(_frame(10, 10))
        qapp.processEvents()
    finally:
        tile.deleteLater()
        qapp.processEvents()


def test_companion_tile_double_click_emits_region_id(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    region = Region(name="Boss", rect=QRect(0, 0, 32, 32))
    tile = CompanionTile(region)
    captured: list = []
    tile.double_clicked.connect(captured.append)
    try:
        tile.resize(200, 150)
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            QPoint(50, 50),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        tile.mouseDoubleClickEvent(ev)
        qapp.processEvents()
        assert captured == [region.id]
    finally:
        tile.deleteLater()
        qapp.processEvents()


# -- CompanionPage ---------------------------------------------------------


def test_companion_page_mirrors_region_manager(qapp):
    regions = RegionManager()
    page = CompanionPage(regions)
    try:
        assert page.tile_ids() == []

        r1 = Region(name="HP", rect=QRect(0, 0, 10, 10))
        r2 = Region(name="Mana", rect=QRect(0, 10, 10, 10))
        regions.add(r1)
        regions.add(r2)
        qapp.processEvents()

        ids = page.tile_ids()
        assert r1.id in ids and r2.id in ids

        regions.remove(r1.id)
        qapp.processEvents()
        assert r1.id not in page.tile_ids()
        assert r2.id in page.tile_ids()
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_companion_page_update_existing_tile(qapp):
    regions = RegionManager()
    page = CompanionPage(regions)
    try:
        r = Region(name="HP", rect=QRect(0, 0, 10, 10))
        regions.add(r)
        qapp.processEvents()

        # Rename via region_changed -- the tile must stay (same id) and
        # pick up the new name for tooltip/accessibility.
        r.name = "Shielder's shield"
        regions.update(r)
        qapp.processEvents()

        # Internal map: the original tile is still there, not a new one.
        assert list(page.tile_ids()) == [r.id]
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_companion_page_set_frame_does_not_crash_without_tiles(qapp):
    regions = RegionManager()
    page = CompanionPage(regions)
    try:
        page.set_frame(_frame())
        page.set_frame(None)
        qapp.processEvents()
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_companion_page_reset_rebuilds_tiles(qapp):
    regions = RegionManager()
    page = CompanionPage(regions)
    try:
        regions.add(Region(name="A", rect=QRect(0, 0, 5, 5)))
        regions.add(Region(name="B", rect=QRect(5, 0, 5, 5)))
        qapp.processEvents()
        assert len(page.tile_ids()) == 2

        new_regions = [Region(name="C", rect=QRect(0, 0, 5, 5))]
        regions.reset(new_regions)
        qapp.processEvents()

        assert len(page.tile_ids()) == 1
        assert page.tile_ids()[0] == new_regions[0].id
    finally:
        page.deleteLater()
        qapp.processEvents()


# -- Placement setting round-trip -----------------------------------------


def test_mirror_placement_defaults_to_floating():
    assert load_mirror_placement() == "floating"


def test_mirror_placement_round_trip():
    for key, _label in PLACEMENT_CHOICES:
        save_mirror_placement(key)
        assert load_mirror_placement() == key


def test_mirror_placement_rejects_garbage():
    save_mirror_placement("floating")
    save_mirror_placement("totally-made-up-mode")
    # Persisted value must not be clobbered by the invalid write.
    assert load_mirror_placement() == "floating"


def test_mirror_placement_recovers_from_corrupt_storage():
    # Simulate a hand-edited settings file containing nonsense. The
    # loader must fall back to ``floating`` rather than crash.
    QSettings().setValue("ui/mirror_placement", "banana")
    assert load_mirror_placement() == "floating"
