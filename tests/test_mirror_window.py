"""Tests that spin up a real ``MirrorWindow`` under the offscreen Qt platform.

These live separately from :mod:`tests.test_regions` because they need a
``QApplication`` fixture and a brief event-loop spin to let Qt propagate
show/hide events.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt

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


def test_mirror_window_does_not_steal_focus_on_show(qapp):
    """WA_ShowWithoutActivating is the load-bearing attribute for keeping
    a freshly-shown mirror from triggering a compositor-level restack
    that buries it behind a focused Tibia window on KDE Plasma Wayland.
    If this attribute ever gets removed, the mirror will steal active
    focus on show() and the compositor may push it below the game.
    """
    region = Region(name="w", rect=QRect(0, 0, 100, 100))
    mirror = MirrorWindow(region)
    try:
        assert mirror.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is True
        # Unlocked mirror keeps StrongFocus so right-click context menu,
        # drag-to-move and Delete-key-to-remove all still work while
        # the user is positioning / editing the region.
        assert mirror.focusPolicy() == Qt.FocusPolicy.StrongFocus
    finally:
        mirror.close()
        mirror.deleteLater()
        qapp.processEvents()


def test_locked_mirror_is_click_through_and_ignores_focus(qapp):
    """A locked mirror MUST be input-transparent on Wayland. If it
    accepts clicks, the compositor delivers them to us instead of to
    Tibia underneath -- which is literally the "I can't play while the
    mirror is on top" bug report this policy is written to prevent.
    On Qt Wayland, ``WA_TransparentForMouseEvents`` on a top-level
    widget is translated to ``wl_surface.set_input_region(empty)``,
    which is the protocol-level click-through mechanism.
    """
    region = Region(name="w", rect=QRect(0, 0, 100, 100), locked=True)
    mirror = MirrorWindow(region)
    try:
        assert mirror.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
        # NoFocus when locked so the mirror doesn't eat Tibia hotkeys
        # via Qt's keyboard routing. The layer-shell path sets
        # KeyboardInteractivity::None at the protocol level; this
        # covers the xdg-toplevel fallback path for the same guarantee.
        assert mirror.focusPolicy() == Qt.FocusPolicy.NoFocus
    finally:
        mirror.close()
        mirror.deleteLater()
        qapp.processEvents()


def test_unlocked_mirror_is_interactive(qapp):
    """Flipping a region from locked to unlocked must restore full
    interactivity so the user can reposition / resize / right-click
    the mirror. Regression guard for the lock-toggle path."""
    region = Region(name="w", rect=QRect(0, 0, 100, 100), locked=True)
    mirror = MirrorWindow(region)
    try:
        assert mirror.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
        assert mirror.focusPolicy() == Qt.FocusPolicy.NoFocus

        # Unlock via the same code path the context menu / control panel use.
        region.locked = False
        mirror.set_region(region)

        assert mirror.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
        assert mirror.focusPolicy() == Qt.FocusPolicy.StrongFocus

        # And back again -- lock must re-engage click-through, not leave
        # the mirror in a half-configured "can click but can't drag" state.
        region.locked = True
        mirror.set_region(region)
        assert mirror.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
        assert mirror.focusPolicy() == Qt.FocusPolicy.NoFocus
    finally:
        mirror.close()
        mirror.deleteLater()
        qapp.processEvents()
