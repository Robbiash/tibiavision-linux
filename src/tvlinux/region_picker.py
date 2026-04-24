"""Region picker widgets.

Two surfaces live here:

- :class:`RegionPickerDialog` - the original modal dialog with a
  fit-to-window live preview. Kept intact for back-compat and as a
  minimal fallback surface.
- :class:`FloatingRegionPicker` - a borderless, always-on-top window
  that renders the live portal capture at 1:1 native size (or any
  chosen zoom) so the user can position it next to their real Tibia
  window and draw rectangles on what looks like a second live copy of
  the game.

Both produce rectangles in **source-stream coordinates** (not widget or
screen coordinates). Region storage is unchanged; only the picking UX
is different.

Wayland constraint: we do *not* try to detect or follow the real Tibia
window's screen position. The floating picker is a normal app window
showing pixels we already have via the XDG ScreenCast portal, so it
carries no new capabilities beyond the existing capture pipeline.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import TOKENS


class _PreviewCanvas(QWidget):
    """Widget that paints the live capture preview + a rubber-band selection.

    Supports two zoom modes:

    - ``"fit"`` (default): letterbox the image inside the available
      widget size, exactly like the original picker.
    - A float multiplier (e.g. ``1.0`` for native, ``2.0`` for 2x):
      the widget reports a ``sizeHint`` equal to ``image.size() * scale``
      so a surrounding :class:`QScrollArea` handles overflow, and the
      image is drawn at the top-left with no letterboxing.

    The source-coordinate math in :meth:`_widget_to_source` and
    :meth:`_source_to_widget` does not care which mode is active; it
    always divides by the concrete ``_image_rect`` drawn on screen.
    """

    selection_changed = Signal(QRect)  # in source coordinates

    # Loupe geometry in widget coordinates.
    _LOUPE_DIAMETER = 180
    _LOUPE_MARGIN = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._selection_src: QRect = QRect()
        self._drag_start_widget: QPoint | None = None
        self._drag_current_widget: QPoint | None = None
        # "fit" or a positive float.
        self._zoom: str | float = "fit"
        # Magnifier loupe state. The loupe only paints when the cursor
        # is over the image rect and the widget is enabled; callers
        # toggle it through :meth:`set_loupe_enabled`.
        self._loupe_enabled: bool = False
        self._loupe_factor: float = 6.0
        self._cursor_widget: QPoint | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, image: QImage) -> None:
        old_size = self._image.size() if self._image is not None else None
        self._image = image
        # Live feeds usually keep the same dimensions, but a Tibia resize
        # would change them -- re-evaluate size hints when the image
        # shape changes so a fixed-zoom canvas tracks the new source.
        if (old_size is None or old_size != image.size()) and self._zoom != "fit":
            self.updateGeometry()
            # In fixed-zoom mode the enclosing QScrollArea runs with
            # widgetResizable=False, so Qt will never resize us on its
            # own. Without this explicit resize the canvas stays pinned
            # to its minimumSize (320x240) and the image is drawn into a
            # tiny top-left crop while the scroll viewport shows black.
            self.resize(self.sizeHint())
        self.update()

    def selection(self) -> QRect:
        return QRect(self._selection_src)

    def reset_selection(self) -> None:
        self._selection_src = QRect()
        self._drag_start_widget = None
        self._drag_current_widget = None
        self.update()
        self.selection_changed.emit(self._selection_src)

    def set_loupe_enabled(self, enabled: bool) -> None:
        """Show/hide the magnifier loupe next to the cursor.

        The loupe is only useful for pixel-level picking (edges of
        HP bars, spell icon cooldowns). Keep it off by default so
        first-run users see a clean view.
        """
        if enabled == self._loupe_enabled:
            return
        self._loupe_enabled = enabled
        self.update()

    def loupe_enabled(self) -> bool:
        return self._loupe_enabled

    def set_loupe_factor(self, factor: float) -> None:
        """Set the loupe magnification (4x / 6x / 8x are the common values)."""
        f = float(factor)
        if f <= 1.0:
            f = 4.0
        if f == self._loupe_factor:
            return
        self._loupe_factor = f
        if self._loupe_enabled:
            self.update()

    def loupe_factor(self) -> float:
        return self._loupe_factor

    def zoom(self) -> str | float:
        return self._zoom

    def set_zoom(self, mode: str | float) -> None:
        """Switch between ``"fit"`` and a fixed multiplier.

        Non-fit values let the widget grow beyond the viewport so a
        scroll area can give the user pixel-perfect precision on dense
        spell icons or HP bars.
        """
        if mode == self._zoom:
            return
        if isinstance(mode, int | float):
            mode = float(mode)
            if mode <= 0:
                mode = 1.0
        elif mode != "fit":
            mode = "fit"
        self._zoom = mode
        self.updateGeometry()
        if mode != "fit" and self._image is not None and not self._image.isNull():
            # Same reasoning as set_frame: fixed-zoom mode lives inside
            # a non-auto-resizing scroll area, so we have to grow to our
            # hint ourselves.
            self.resize(self.sizeHint())
        self.update()

    # -- Painting ---------------------------------------------------------------------

    def _image_rect(self) -> QRect:
        """Where the source image lands inside this widget.

        In ``"fit"`` mode we letterbox and centre. In fixed-zoom mode we
        pin to the top-left at ``image.size() * scale`` pixels, which is
        what the scroll viewport expects.
        """
        if self._image is None or self._image.isNull():
            return QRect()
        src_size = self._image.size()
        if src_size.width() == 0 or src_size.height() == 0:
            return QRect()

        if self._zoom == "fit":
            widget_size = self.size()
            scale = min(
                widget_size.width() / src_size.width(),
                widget_size.height() / src_size.height(),
            )
            w = int(src_size.width() * scale)
            h = int(src_size.height() * scale)
            x = (widget_size.width() - w) // 2
            y = (widget_size.height() - h) // 2
            return QRect(x, y, w, h)

        scale = float(self._zoom)
        w = max(1, int(src_size.width() * scale))
        h = max(1, int(src_size.height() * scale))
        return QRect(0, 0, w, h)

    def sizeHint(self) -> QSize:
        if self._zoom != "fit" and self._image is not None and not self._image.isNull():
            scale = float(self._zoom)
            return QSize(
                max(1, int(self._image.width() * scale)),
                max(1, int(self._image.height() * scale)),
            )
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        # In fixed-zoom mode the scroll area needs the canvas to honour
        # its natural size; in fit mode we keep the 320x240 floor from
        # the original picker so narrow windows still render usefully.
        if self._zoom != "fit" and self._image is not None and not self._image.isNull():
            return self.sizeHint()
        return QSize(320, 240)

    def _widget_to_source(self, p: QPoint) -> QPoint:
        r = self._image_rect()
        if r.isEmpty() or self._image is None:
            return QPoint(0, 0)
        sx = (p.x() - r.x()) * self._image.width() / r.width()
        sy = (p.y() - r.y()) * self._image.height() / r.height()
        return QPoint(int(sx), int(sy))

    def _source_to_widget(self, r: QRect) -> QRect:
        img_rect = self._image_rect()
        if img_rect.isEmpty() or self._image is None:
            return QRect()
        fx = img_rect.width() / self._image.width()
        fy = img_rect.height() / self._image.height()
        return QRect(
            img_rect.x() + int(r.x() * fx),
            img_rect.y() + int(r.y() * fy),
            int(r.width() * fx),
            int(r.height() * fy),
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(15, 15, 15))
        if self._image is not None and not self._image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            target = self._image_rect()
            painter.drawImage(target, self._image, self._image.rect())

        if self._drag_start_widget and self._drag_current_widget:
            # Dimming overlay outside the selection.
            sel_widget = QRect(self._drag_start_widget, self._drag_current_widget).normalized()
            painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
            if not sel_widget.isEmpty():
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(sel_widget, Qt.GlobalColor.transparent)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                pen = QPen(QColor(0, 200, 255), 2)
                painter.setPen(pen)
                painter.drawRect(sel_widget)
        elif not self._selection_src.isEmpty():
            # Confirmed selection (committed on mouse release).
            sel_widget = self._source_to_widget(self._selection_src)
            pen = QPen(QColor(0, 200, 255), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(sel_widget)

        if self._loupe_enabled and self._cursor_widget is not None:
            self._draw_loupe(painter)

    def _draw_loupe(self, painter: QPainter) -> None:
        """Draw a circular magnified view of the pixels under the cursor.

        The loupe is anchored to a corner of the widget away from the
        cursor (edge-flipping) so it never obscures the area the user
        is actually trying to aim at.
        """
        if self._image is None or self._image.isNull():
            return
        img_rect = self._image_rect()
        assert self._cursor_widget is not None
        if not img_rect.contains(self._cursor_widget):
            return

        # Translate cursor into source coords; that's the centre of
        # the magnified patch we'll sample.
        src_centre = self._widget_to_source(self._cursor_widget)
        diameter = self._LOUPE_DIAMETER
        factor = self._loupe_factor
        # Source-space sample size so that ``diameter`` on-screen
        # pixels show ``diameter / factor`` source pixels.
        sample_size = max(4, int(diameter / factor))
        half = sample_size // 2
        src_x = max(0, min(self._image.width() - sample_size, src_centre.x() - half))
        src_y = max(0, min(self._image.height() - sample_size, src_centre.y() - half))
        src_rect = QRect(src_x, src_y, sample_size, sample_size)

        # Edge-flip: anchor the loupe to whichever corner is farthest
        # from the cursor so it doesn't cover the point of interest.
        w = self.width()
        h = self.height()
        cx = self._cursor_widget.x()
        cy = self._cursor_widget.y()
        right = cx < w // 2
        bottom = cy < h // 2
        margin = self._LOUPE_MARGIN
        x = (w - diameter - margin) if right else margin
        y = (h - diameter - margin) if bottom else margin
        loupe_rect = QRect(x, y, diameter, diameter)

        painter.save()
        try:
            # Clip to a circle so the zoom reads as a loupe rather than
            # a floating rectangle of pixels.
            from PySide6.QtGui import QPainterPath

            path = QPainterPath()
            path.addEllipse(loupe_rect)
            painter.setClipPath(path)
            painter.fillRect(loupe_rect, QColor(0, 0, 0))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawImage(loupe_rect, self._image, src_rect)

            # Crosshair at the centre of the loupe marks the exact
            # source pixel under the cursor.
            centre = loupe_rect.center()
            pen = QPen(QColor(0, 229, 255), 1)
            painter.setPen(pen)
            painter.drawLine(centre.x(), loupe_rect.top() + 4, centre.x(), loupe_rect.bottom() - 4)
            painter.drawLine(loupe_rect.left() + 4, centre.y(), loupe_rect.right() - 4, centre.y())
        finally:
            painter.restore()

        # Rim + subtle halo drawn outside the clip so they ring the
        # loupe rather than getting chopped in half.
        rim = QPen(QColor(0, 229, 255, 220), 2)
        painter.setPen(rim)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(loupe_rect)
        # Factor badge so the user remembers which zoom they're on.
        badge_text = f"{factor:g}x"
        painter.setPen(QColor(TOKENS.palette.text_primary))
        painter.drawText(
            loupe_rect.adjusted(0, 0, -6, -6),
            int(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight),
            badge_text,
        )

    # -- Mouse ------------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        img = self._image_rect()
        pos = event.position().toPoint()
        if not img.contains(pos):
            return
        self._drag_start_widget = pos
        self._drag_current_widget = pos
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        # Always track the cursor in widget coords -- the loupe follows
        # it even when the user hasn't started dragging yet.
        self._cursor_widget = QPoint(pos)
        if self._drag_start_widget is not None:
            img = self._image_rect()
            pos.setX(max(img.left(), min(img.right(), pos.x())))
            pos.setY(max(img.top(), min(img.bottom(), pos.y())))
            self._drag_current_widget = pos
        if self._loupe_enabled or self._drag_start_widget is not None:
            self.update()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        # Forget the cursor once it leaves the canvas so the loupe
        # doesn't linger at a stale position until the next move.
        self._cursor_widget = None
        if self._loupe_enabled:
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._drag_start_widget is None:
            return
        sel = QRect(self._drag_start_widget, self._drag_current_widget or self._drag_start_widget)
        sel = sel.normalized()
        self._drag_start_widget = None
        self._drag_current_widget = None
        if sel.width() < 4 or sel.height() < 4:
            self._selection_src = QRect()
        else:
            top_left = self._widget_to_source(sel.topLeft())
            bottom_right = self._widget_to_source(sel.bottomRight())
            self._selection_src = QRect(top_left, bottom_right).normalized()
            if self._image is not None:
                self._selection_src = self._selection_src.intersected(self._image.rect())
        self.selection_changed.emit(self._selection_src)
        self.update()


class RegionPickerDialog(QDialog):
    """Modal fit-to-window picker. Kept for back-compat and diagnostics.

    The main app now uses :class:`FloatingRegionPicker` for a better
    1:1 picking experience, but this smaller dialog is still a safe
    fallback if a caller wants a fire-and-forget modal surface.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select region")
        self.resize(720, 540)
        self._canvas = _PreviewCanvas(self)
        self._label = QLabel("No selection yet.", self)
        self._ok_btn = QPushButton("Create region", self)
        self._reset_btn = QPushButton("Reset", self)
        self._cancel_btn = QPushButton("Cancel", self)

        self._ok_btn.setEnabled(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._label)
        row = QWidget(self)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)
        h.addWidget(self._reset_btn)
        h.addWidget(self._cancel_btn)
        h.addWidget(self._ok_btn)
        layout.addWidget(row)

        self._canvas.selection_changed.connect(self._on_selection)
        self._ok_btn.clicked.connect(self.accept)
        self._reset_btn.clicked.connect(self._canvas.reset_selection)
        self._cancel_btn.clicked.connect(self.reject)

    def set_frame(self, image: QImage) -> None:
        self._canvas.set_frame(image)

    def selected_rect(self) -> QRect:
        return self._canvas.selection()

    def _on_selection(self, rect: QRect) -> None:
        if rect.isEmpty():
            self._label.setText("No selection yet.")
            self._ok_btn.setEnabled(False)
        else:
            self._label.setText(
                f"Selection: {rect.width()}x{rect.height()} @ ({rect.x()}, {rect.y()})"
            )
            self._ok_btn.setEnabled(True)

    def sizeHint(self) -> QSize:
        return QSize(720, 540)


# -- Floating picker ---------------------------------------------------------


class _TitleStrip(QWidget):
    """Grabbable title bar for the frameless floating picker.

    A frameless window has no native chrome, so we roll a minimal strip
    that shows the title, lets the user drag the window by click-and-hold,
    and hosts a Close button. Double-clicking toggles fit vs 100% zoom
    via the :pyattr:`zoom_toggle_requested` signal.
    """

    zoom_toggle_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("RegionPickerTitle")
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._press_offset: QPoint | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 8, 0)
        row.setSpacing(8)

        self._title = QLabel("Pick region  \u00b7  drag to move", self)
        self._title.setStyleSheet(
            f"color: {TOKENS.palette.text_primary}; font-weight: {TOKENS.type.weight_bold};"
        )
        row.addWidget(self._title)
        row.addStretch(1)

        self._close_btn = QPushButton("\u2715", self)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setToolTip("Cancel (Esc)")
        self._close_btn.setAccessibleName("Cancel region picker")
        self._close_btn.setAccessibleDescription("Close without creating a region (Escape)")
        self._close_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._close_btn.setProperty("variant", "ghost")
        self._close_btn.clicked.connect(self.close_requested)
        row.addWidget(self._close_btn)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Remember the cursor offset relative to the top-level window
            # so we can translate subsequent move events into absolute
            # window positions without jitter.
            top = self.window()
            self._press_offset = event.globalPosition().toPoint() - top.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_offset is None:
            return
        top = self.window()
        top.move(event.globalPosition().toPoint() - self._press_offset)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.zoom_toggle_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FloatingRegionPicker(QWidget):
    """Borderless, stay-on-top live region picker.

    Shows the portal capture at 1:1 native size by default. The user
    drags the window next to (or over) the real Tibia window and draws
    a rubber-band rectangle on the live feed; the rectangle is stored
    in source-stream coordinates, exactly like the modal picker.

    The picker is a normal Qt window: it sits above other windows
    because of :pyattr:`Qt.WindowStaysOnTopHint`, but it does not read
    or touch the Tibia window's geometry, receive input destined for
    Tibia, or expose any capability beyond the existing capture stream.
    """

    region_accepted = Signal(QRect)  # source-coord rect
    cancelled = Signal()

    _ZOOM_CHOICES: tuple[tuple[str, str | float], ...] = (
        ("Fit", "fit"),
        ("50%", 0.5),
        ("100% (native)", 1.0),
        ("200%", 2.0),
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("FloatingRegionPicker")
        # Source colours from the design-system palette so the picker
        # stays in step with the rest of the app when the theme shifts.
        p = TOKENS.palette
        r = TOKENS.radius
        self.setStyleSheet(
            f"""
            QWidget#FloatingRegionPicker {{
                background-color: {p.bg_app};
                border: 2px solid {p.accent};
                border-radius: {r.lg}px;
            }}
            QWidget#RegionPickerTitle {{
                background-color: {p.bg_surface};
                border-top-left-radius: {r.md}px;
                border-top-right-radius: {r.md}px;
            }}
            QWidget#RegionPickerToolbar {{
                background-color: {p.bg_surface};
                border-bottom-left-radius: {r.md}px;
                border-bottom-right-radius: {r.md}px;
            }}
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)

        self._title = _TitleStrip(self)
        self._title.close_requested.connect(self._on_cancel)
        self._title.zoom_toggle_requested.connect(self._toggle_fit_native)
        root.addWidget(self._title)

        self._canvas = _PreviewCanvas(self)
        self._canvas.selection_changed.connect(self._on_selection)
        # Default to Fit so the user always sees the entire live feed
        # the moment the picker opens, regardless of the capture's
        # native size. They can drop to 100%/200% for pixel work via
        # the zoom dropdown (or a double-click on the title strip).
        self._canvas.set_zoom("fit")

        self._scroll = QScrollArea(self)
        # Fit mode lets the scroll area grow the canvas to fill the
        # viewport. When we switch to a fixed zoom, _on_zoom_changed
        # flips this back to False and sizes the canvas explicitly.
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._canvas)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet(f"QScrollArea {{ background-color: {TOKENS.palette.bg_app}; }}")
        root.addWidget(self._scroll, 1)

        self._toolbar = QWidget(self)
        self._toolbar.setObjectName("RegionPickerToolbar")
        self._toolbar.setFixedHeight(44)
        tb = QHBoxLayout(self._toolbar)
        tb.setContentsMargins(12, 6, 12, 6)
        tb.setSpacing(10)

        zoom_label = QLabel("Zoom", self._toolbar)
        zoom_label.setStyleSheet(f"color: {p.text_secondary};")
        tb.addWidget(zoom_label)

        self._zoom_combo = QComboBox(self._toolbar)
        for label, _ in self._ZOOM_CHOICES:
            self._zoom_combo.addItem(label)
        # Start on Fit so the whole live feed is always visible on open.
        # 100% native and 200% are a click away when the user needs
        # pixel-perfect precision on spell icons or HP bars.
        self._zoom_combo.setCurrentIndex(0)
        self._zoom_combo.setToolTip("Zoom level for the live capture")
        self._zoom_combo.setAccessibleName("Zoom")
        self._zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        tb.addWidget(self._zoom_combo)

        # -- Loupe ------------------------------------------------------
        # Press L (or toggle the checkbox) to summon a circular
        # magnifier that follows the cursor. At 4x / 6x / 8x it makes
        # framing 1-pixel cooldown bars trivial without forcing the
        # user into permanent 200 % zoom + scrolling.
        self._loupe_chk = QCheckBox("Loupe", self._toolbar)
        self._loupe_chk.setStyleSheet(f"color: {p.text_secondary};")
        self._loupe_chk.setToolTip("Show magnifier near the cursor (shortcut: L)")
        self._loupe_chk.setAccessibleName("Loupe")
        self._loupe_chk.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._loupe_chk.toggled.connect(self._canvas.set_loupe_enabled)
        tb.addWidget(self._loupe_chk)

        self._loupe_combo = QComboBox(self._toolbar)
        for factor in (4.0, 6.0, 8.0):
            self._loupe_combo.addItem(f"{factor:g}x", factor)
        self._loupe_combo.setCurrentIndex(1)  # 6x by default
        self._loupe_combo.setToolTip("Loupe magnification level")
        self._loupe_combo.setAccessibleName("Loupe zoom")
        self._loupe_combo.currentIndexChanged.connect(self._on_loupe_factor_changed)
        tb.addWidget(self._loupe_combo)

        # Global (window-scoped) L shortcut toggles the loupe so the
        # user never has to reach for the mouse when working close to
        # the cursor.
        self._loupe_shortcut = QShortcut(QKeySequence("L"), self)
        self._loupe_shortcut.activated.connect(self._toggle_loupe)

        self._hint = QLabel(
            "Drag a rectangle on the live view. Move this window wherever it's easiest to aim.",
            self._toolbar,
        )
        self._hint.setStyleSheet(f"color: {p.text_secondary};")
        tb.addWidget(self._hint)

        tb.addStretch(1)

        self._selection_label = QLabel("No selection yet.", self._toolbar)
        self._selection_label.setStyleSheet(f"color: {p.text_secondary};")
        tb.addWidget(self._selection_label)

        self._reset_btn = QPushButton("Reset", self._toolbar)
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setProperty("variant", "ghost")
        self._reset_btn.setToolTip("Clear the current selection rectangle")
        self._reset_btn.setAccessibleName("Reset selection")
        self._reset_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._reset_btn.clicked.connect(self._canvas.reset_selection)
        tb.addWidget(self._reset_btn)

        self._cancel_btn = QPushButton("Cancel", self._toolbar)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setToolTip("Close without creating a region (Esc)")
        self._cancel_btn.setAccessibleName("Cancel")
        self._cancel_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._cancel_btn.clicked.connect(self._on_cancel)
        tb.addWidget(self._cancel_btn)

        self._ok_btn = QPushButton("Create region", self._toolbar)
        self._ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok_btn.setProperty("variant", "primary")
        self._ok_btn.setEnabled(False)
        self._ok_btn.setToolTip("Save the selected rectangle as a region")
        self._ok_btn.setAccessibleName("Create region from selection")
        self._ok_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ok_btn.clicked.connect(self._on_accept)
        tb.addWidget(self._ok_btn)

        root.addWidget(self._toolbar)

    # -- Public API -------------------------------------------------------

    def set_frame(self, image: QImage) -> None:
        self._canvas.set_frame(image)

    def selected_rect(self) -> QRect:
        return self._canvas.selection()

    def size_to_source(self) -> None:
        """Resize the window so the live feed is usable on open.

        In Fit mode the canvas has no native hint (it fills whatever
        we give it), so we fall back to the source image's own size.
        If even that is unknown (e.g. opened before any frame arrived)
        we still pick a comfortable default instead of leaving the
        frameless window at its minimum. Capped at 90 % of the primary
        screen so 4K captures at 200 % don't spawn an oversized window.
        """
        # Prefer the canvas hint (meaningful in fixed-zoom mode) and
        # fall back to the raw source size when we're in Fit mode.
        hint = self._canvas.sizeHint()
        if not hint.isValid() or hint.isEmpty():
            source = self._canvas._image  # type: ignore[attr-defined]
            if source is not None and not source.isNull():
                hint = source.size()

        chrome_w = 4  # 2 px border either side
        chrome_h = self._title.height() + self._toolbar.height() + 4

        if hint.isValid() and not hint.isEmpty():
            target_w = hint.width() + chrome_w
            target_h = hint.height() + chrome_h
        else:
            # Frame-less fallback so the user still sees a real window.
            target_w = 960
            target_h = 640

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, int(avail.width() * 0.9))
            target_h = min(target_h, int(avail.height() * 0.9))
        # Absolute floor: anything smaller is unusable for picking.
        self.resize(max(640, target_w), max(480, target_h))

    # -- Internals --------------------------------------------------------

    def _on_zoom_changed(self, index: int) -> None:
        if not 0 <= index < len(self._ZOOM_CHOICES):
            return
        _label, mode = self._ZOOM_CHOICES[index]
        # In fit mode the canvas expands with the viewport; in fixed
        # modes it needs its own natural size so the scroll bars engage.
        # Toggle the scroll policy *before* applying the zoom so that
        # _PreviewCanvas.set_zoom's explicit resize lands in the right
        # layout context (widgetResizable=False, which is what we want
        # for a scrollable fixed-zoom canvas).
        self._scroll.setWidgetResizable(mode == "fit")
        self._canvas.set_zoom(mode)

    def _toggle_fit_native(self) -> None:
        # Double-click on the title strip flips between a full overview
        # and pixel-accurate drawing without forcing the user into the
        # dropdown.
        current = self._zoom_combo.currentIndex()
        target = 0 if current != 0 else 2  # Fit <-> 100%
        self._zoom_combo.setCurrentIndex(target)

    def _toggle_loupe(self) -> None:
        # L shortcut target: round-trip through the checkbox so its
        # visible state matches the canvas state.
        self._loupe_chk.setChecked(not self._loupe_chk.isChecked())

    def _on_loupe_factor_changed(self, index: int) -> None:
        data = self._loupe_combo.itemData(index)
        if isinstance(data, int | float):
            self._canvas.set_loupe_factor(float(data))

    def _on_selection(self, rect: QRect) -> None:
        if rect.isEmpty():
            self._selection_label.setText("No selection yet.")
            self._ok_btn.setEnabled(False)
        else:
            self._selection_label.setText(
                f"Selection: {rect.width()}x{rect.height()} @ ({rect.x()}, {rect.y()})"
            )
            self._ok_btn.setEnabled(True)

    def _on_accept(self) -> None:
        rect = self._canvas.selection()
        if rect.isEmpty():
            return
        # Unmap the always-on-top picker *before* emitting so any
        # window spawned in response (e.g. a MirrorWindow) gets the
        # compositor's focus / stacking decision instead of losing it
        # to whatever was below the picker (typically the game).
        self.hide()
        self.region_accepted.emit(rect)
        self.close()

    def _on_cancel(self) -> None:
        # Mirror the accept path so cancel doesn't race the picker's
        # unmap against any downstream window show() either.
        self.hide()
        self.cancelled.emit()
        self.close()

    # -- Key handling -----------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._ok_btn.isEnabled():
            self._on_accept()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = [
    "FloatingRegionPicker",
    "RegionPickerDialog",
]
