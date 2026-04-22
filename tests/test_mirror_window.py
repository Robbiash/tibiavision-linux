"""Tests that spin up a real ``MirrorWindow`` under the offscreen Qt platform.

These live separately from :mod:`tests.test_regions` because they need a
``QApplication`` fixture and a brief event-loop spin to let Qt propagate
show/hide events.
"""

from __future__ import annotations

from PySide6.QtCore import QRect

from tvlinux.mirror_window import MirrorWindow
from tvlinux.regions import Region


def test_hide_then_show_preserves_saved_geometry(qapp):
    region = Region(name="w", rect=QRect(0, 0, 100, 100))
    saved = QRect(200, 150, 180, 120)
    region.geometry = QRect(saved)

    mirror = MirrorWindow(region)
    try:
        mirror.show()
        qapp.processEvents()

        # Hide the window. On Wayland this can emit spurious move/resize
        # events with compositor placeholder coords; _persist_geometry must
        # not write those back to the region model.
        mirror.hide()
        qapp.processEvents()

        assert region.geometry == saved

        # Re-show and make sure the window is placed back at the saved rect.
        mirror.show()
        qapp.processEvents()
        assert region.geometry == saved
    finally:
        mirror.close()
        mirror.deleteLater()
        qapp.processEvents()
