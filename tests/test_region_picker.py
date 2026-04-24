"""Tests for the live region-picker widgets.

The picker contains two surfaces worth testing:

- `_PreviewCanvas` converts widget-space selections into source-stream
  coordinates. Both ``"fit"`` and fixed-zoom modes must produce the
  same source rectangle for an equivalent on-canvas selection, and
  selections must clamp to the image bounds so off-screen drags don't
  produce negative/overflowed regions.
- `FloatingRegionPicker` exposes ``region_accepted`` / ``cancelled``
  signals. Clicking Create should emit with the selection; Escape or
  Cancel should emit ``cancelled`` instead.

These tests run under the offscreen Qt platform via the shared ``qapp``
fixture in :mod:`tests.conftest`.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent

from tvlinux.region_picker import FloatingRegionPicker, _PreviewCanvas


def _make_image(width: int = 400, height: int = 300) -> QImage:
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(0x303030)
    return img


def _press(canvas: _PreviewCanvas, pos: QPoint) -> None:
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mousePressEvent(event)


def _move(canvas: _PreviewCanvas, pos: QPoint) -> None:
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        pos,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mouseMoveEvent(event)


def _release(canvas: _PreviewCanvas, pos: QPoint) -> None:
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    canvas.mouseReleaseEvent(event)


def _drag(canvas: _PreviewCanvas, start: QPoint, end: QPoint) -> None:
    _press(canvas, start)
    _move(canvas, end)
    _release(canvas, end)


def test_canvas_native_zoom_maps_one_to_one(qapp):
    canvas = _PreviewCanvas()
    canvas.set_frame(_make_image(400, 300))
    canvas.set_zoom(1.0)
    # At 1.0 zoom the canvas sizeHint equals the image; resize matches.
    canvas.resize(canvas.sizeHint())

    _drag(canvas, QPoint(50, 40), QPoint(150, 140))
    sel = canvas.selection()
    # 1 widget pixel == 1 source pixel at native zoom. Qt's two-point
    # QRect constructor is inclusive on both corners, so a 100-pixel
    # drag yields a 101-pixel rectangle -- same as the old dialog.
    assert sel.topLeft() == QPoint(50, 40)
    assert sel.width() == 101
    assert sel.height() == 101


def test_canvas_fit_zoom_scales_back_to_source(qapp):
    canvas = _PreviewCanvas()
    canvas.set_frame(_make_image(400, 300))
    canvas.set_zoom("fit")
    # 800x600 widget -> image letterboxes to 800x600 (2x scale, same aspect).
    canvas.resize(800, 600)

    _drag(canvas, QPoint(100, 80), QPoint(300, 280))
    sel = canvas.selection()
    # widget (100,80)-(300,280) at 2x scale -> source (50,40)-(150,140).
    # Same inclusive-two-points behaviour as the original dialog, so
    # width/height are 101, not 100.
    assert sel.topLeft() == QPoint(50, 40)
    assert sel.width() == 101
    assert sel.height() == 101


def test_canvas_clamps_selection_to_image_bounds(qapp):
    canvas = _PreviewCanvas()
    canvas.set_frame(_make_image(400, 300))
    canvas.set_zoom(1.0)
    canvas.resize(canvas.sizeHint())

    # Drag well past the right/bottom edges; the clamp in mouseMove
    # should keep the current point inside the image rect.
    _drag(canvas, QPoint(350, 250), QPoint(999, 999))
    sel = canvas.selection()
    assert sel.right() <= 399
    assert sel.bottom() <= 299
    assert sel.left() == 350
    assert sel.top() == 250


def test_canvas_discards_tiny_selections(qapp):
    canvas = _PreviewCanvas()
    canvas.set_frame(_make_image(400, 300))
    canvas.set_zoom(1.0)
    canvas.resize(canvas.sizeHint())

    _drag(canvas, QPoint(10, 10), QPoint(12, 12))
    assert canvas.selection().isEmpty()


def test_floating_picker_emits_region_accepted(qapp):
    picker = FloatingRegionPicker()
    try:
        picker.set_frame(_make_image(400, 300))
        picker.show()
        qapp.processEvents()

        canvas = picker._canvas  # type: ignore[attr-defined]
        # Drop the picker to 100% zoom for this test so drag coordinates
        # are 1:1 with source coordinates regardless of window size.
        picker._zoom_combo.setCurrentIndex(2)  # type: ignore[attr-defined]
        qapp.processEvents()
        _drag(canvas, QPoint(25, 30), QPoint(125, 130))

        received: list[QRect] = []
        visibility_at_emit: list[bool] = []

        def _on_accepted(r: QRect) -> None:
            # The picker must have unmapped itself before downstream
            # slots run; otherwise a freshly-spawned MirrorWindow can
            # lose the focus/stacking race to the game when the
            # always-on-top picker finally closes.
            visibility_at_emit.append(picker.isVisible())
            received.append(r)

        picker.region_accepted.connect(_on_accepted)

        picker._on_accept()  # type: ignore[attr-defined]
        assert len(received) == 1
        rect = received[0]
        assert rect.topLeft() == QPoint(25, 30)
        assert rect.width() == 101
        assert rect.height() == 101
        assert visibility_at_emit == [False]
    finally:
        picker.close()
        picker.deleteLater()
        qapp.processEvents()


def test_floating_picker_cancel_hides_before_emit(qapp):
    picker = FloatingRegionPicker()
    try:
        picker.set_frame(_make_image(400, 300))
        picker.show()
        qapp.processEvents()

        visibility_at_emit: list[bool] = []
        picker.cancelled.connect(lambda: visibility_at_emit.append(picker.isVisible()))

        picker._on_cancel()  # type: ignore[attr-defined]
        assert visibility_at_emit == [False]
    finally:
        picker.close()
        picker.deleteLater()
        qapp.processEvents()


def test_floating_picker_cancel_via_escape(qapp):
    picker = FloatingRegionPicker()
    try:
        picker.set_frame(_make_image(400, 300))
        picker.show()
        qapp.processEvents()

        cancels: list[object] = []
        picker.cancelled.connect(lambda: cancels.append(None))

        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        picker.keyPressEvent(event)
        assert len(cancels) == 1
    finally:
        picker.close()
        picker.deleteLater()
        qapp.processEvents()


def test_floating_picker_defaults_to_fit_zoom(qapp):
    picker = FloatingRegionPicker()
    try:
        picker.set_frame(_make_image(400, 300))
        picker.show()
        qapp.processEvents()

        # Fit is the default on open: the entire capture must be
        # visible without the user having to discover the zoom dropdown.
        assert picker._zoom_combo.currentIndex() == 0  # type: ignore[attr-defined]
        assert picker._scroll.widgetResizable() is True  # type: ignore[attr-defined]
        assert picker._canvas.zoom() == "fit"  # type: ignore[attr-defined]
    finally:
        picker.close()
        picker.deleteLater()
        qapp.processEvents()


def test_floating_picker_zoom_switch_respects_scroll_policy(qapp):
    picker = FloatingRegionPicker()
    try:
        picker.set_frame(_make_image(400, 300))
        picker.show()
        qapp.processEvents()

        # Fit is the default; scroll area resizes the canvas for us.
        assert picker._scroll.widgetResizable() is True  # type: ignore[attr-defined]

        picker._zoom_combo.setCurrentIndex(2)  # "100% (native)"  # type: ignore[attr-defined]
        assert picker._scroll.widgetResizable() is False  # type: ignore[attr-defined]
        assert picker._canvas.zoom() == 1.0  # type: ignore[attr-defined]
        # Switching out of Fit must grow the canvas to its natural
        # size; otherwise the scroll area shows a black crop because
        # widgetResizable=False disables Qt's auto-resize.
        assert picker._canvas.size() == picker._canvas.sizeHint()  # type: ignore[attr-defined]

        picker._zoom_combo.setCurrentIndex(3)  # "200%"  # type: ignore[attr-defined]
        assert picker._scroll.widgetResizable() is False  # type: ignore[attr-defined]
        assert picker._canvas.zoom() == 2.0  # type: ignore[attr-defined]
        assert picker._canvas.size() == picker._canvas.sizeHint()  # type: ignore[attr-defined]

        picker._zoom_combo.setCurrentIndex(0)  # back to "Fit"  # type: ignore[attr-defined]
        assert picker._scroll.widgetResizable() is True  # type: ignore[attr-defined]
        assert picker._canvas.zoom() == "fit"  # type: ignore[attr-defined]
    finally:
        picker.close()
        picker.deleteLater()
        qapp.processEvents()


def test_canvas_native_zoom_set_frame_resizes_to_image(qapp):
    """Regression: set_frame in fixed-zoom mode must size the canvas.

    Without the explicit resize the canvas stays at its minimum size
    and the scroll area shows a tiny top-left crop of the capture on
    a black background.
    """
    canvas = _PreviewCanvas()
    canvas.set_zoom(1.0)
    # Simulate being inside a QScrollArea(widgetResizable=False):
    # Qt will never resize us on our behalf.
    canvas.resize(canvas.minimumSizeHint())
    assert canvas.size().width() <= 320  # starts small

    canvas.set_frame(_make_image(640, 480))
    assert canvas.size() == QSize(640, 480)


def test_canvas_loupe_defaults_and_setters(qapp):
    canvas = _PreviewCanvas()
    # Off by default so first-time users see a clean view; 6x is the
    # middle default to match the toolbar combobox.
    assert canvas.loupe_enabled() is False
    assert canvas.loupe_factor() == 6.0

    canvas.set_loupe_enabled(True)
    canvas.set_loupe_factor(4.0)
    assert canvas.loupe_enabled() is True
    assert canvas.loupe_factor() == 4.0

    # Out-of-range factor falls back to 4x rather than accepting 1x or
    # lower (which would not meaningfully magnify anything).
    canvas.set_loupe_factor(0.5)
    assert canvas.loupe_factor() == 4.0


def test_floating_picker_loupe_shortcut_toggles_checkbox(qapp):
    picker = FloatingRegionPicker()
    try:
        picker.set_frame(_make_image(400, 300))
        picker.show()
        qapp.processEvents()

        assert picker._loupe_chk.isChecked() is False  # type: ignore[attr-defined]
        assert picker._canvas.loupe_enabled() is False  # type: ignore[attr-defined]

        # Programmatically emit the shortcut action: QShortcut.activated
        # is the primary signal, so emitting it is the tightest test we
        # can run under offscreen Qt without platform keyboard input.
        picker._loupe_shortcut.activated.emit()  # type: ignore[attr-defined]
        qapp.processEvents()
        assert picker._loupe_chk.isChecked() is True  # type: ignore[attr-defined]
        assert picker._canvas.loupe_enabled() is True  # type: ignore[attr-defined]

        picker._loupe_shortcut.activated.emit()  # type: ignore[attr-defined]
        qapp.processEvents()
        assert picker._canvas.loupe_enabled() is False  # type: ignore[attr-defined]
    finally:
        picker.close()
        picker.deleteLater()
        qapp.processEvents()


def test_floating_picker_loupe_factor_dropdown(qapp):
    picker = FloatingRegionPicker()
    try:
        picker.set_frame(_make_image(400, 300))
        picker.show()
        qapp.processEvents()

        # Default to 6x (middle entry, index 1).
        assert picker._canvas.loupe_factor() == 6.0  # type: ignore[attr-defined]

        picker._loupe_combo.setCurrentIndex(2)  # "8x"  # type: ignore[attr-defined]
        assert picker._canvas.loupe_factor() == 8.0  # type: ignore[attr-defined]

        picker._loupe_combo.setCurrentIndex(0)  # "4x"  # type: ignore[attr-defined]
        assert picker._canvas.loupe_factor() == 4.0  # type: ignore[attr-defined]
    finally:
        picker.close()
        picker.deleteLater()
        qapp.processEvents()
