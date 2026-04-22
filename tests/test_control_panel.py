"""Smoke tests for the restructured control panel.

These intentionally run under the offscreen Qt platform (see conftest). The
goal is to catch import/wiring regressions from the cards-based detail
panel, not to exercise every interaction.
"""

from __future__ import annotations

from PySide6.QtCore import QRect

from tvlinux.control_panel import ControlPanel
from tvlinux.regions import Region, RegionManager


def test_control_panel_builds_and_reflects_region_count(qapp):
    manager = RegionManager()
    panel = ControlPanel(manager)
    try:
        panel.show()
        qapp.processEvents()

        assert panel._regions_count.text() == "0 regions"

        manager.add(Region(name="r1", rect=QRect(0, 0, 10, 10)))
        manager.add(Region(name="r2", rect=QRect(10, 10, 10, 10)))
        qapp.processEvents()
        assert panel._regions_count.text() == "2 regions"

        # Detail panel is disabled until a region is selected; selecting
        # should enable the inner widget that wraps the three cards.
        assert panel._detail_inner.isEnabled() is False
        panel._list.setCurrentRow(0)
        qapp.processEvents()
        assert panel._detail_inner.isEnabled() is True
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()


def test_color_preset_swatches_present(qapp):
    panel = ControlPanel(RegionManager())
    try:
        # Six Tibia-themed presets defined in PRESET_BORDER_COLORS.
        from tvlinux.control_panel import PRESET_BORDER_COLORS

        assert len(PRESET_BORDER_COLORS) == 6
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()
