"""Rubber-band region picker.

Shows a **modal dialog** with a live preview of the captured source (Tibia window) and
lets the user drag out a rectangle with the mouse. The resulting rectangle is returned
in **source-stream coordinates** (not screen coordinates), because source coords are
stable across window moves, monitor changes, and mirror resizes.

We deliberately draw the preview inside a dialog instead of fullscreen: on Wayland, an
app cannot make itself visible on top of another app's window without the compositor's
help, and we *already* have the compositor's pixels via the portal. Using a dialog also
keeps the interaction predictable on multi-monitor setups.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget


class _PreviewCanvas(QWidget):
    """Widget that paints the live capture preview + a rubber-band selection."""

    selection_changed = Signal(QRect)  # in source coordinates

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._selection_src: QRect = QRect()
        self._drag_start_widget: QPoint | None = None
        self._drag_current_widget: QPoint | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self.update()

    def selection(self) -> QRect:
        return QRect(self._selection_src)

    def reset_selection(self) -> None:
        self._selection_src = QRect()
        self._drag_start_widget = None
        self._drag_current_widget = None
        self.update()
        self.selection_changed.emit(self._selection_src)

    # -- Painting ---------------------------------------------------------------------

    def _image_rect(self) -> QRect:
        """The rectangle inside this widget where the source image is drawn (letterboxed)."""
        if self._image is None or self._image.isNull():
            return QRect()
        src_size = self._image.size()
        widget_size = self.size()
        if src_size.width() == 0 or src_size.height() == 0:
            return QRect()
        scale = min(
            widget_size.width() / src_size.width(),
            widget_size.height() / src_size.height(),
        )
        w = int(src_size.width() * scale)
        h = int(src_size.height() * scale)
        x = (widget_size.width() - w) // 2
        y = (widget_size.height() - h) // 2
        return QRect(x, y, w, h)

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

        painter.setPen(QColor(220, 220, 220))
        painter.drawText(
            self.rect().adjusted(8, 8, -8, -8),
            int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft),
            "Drag a rectangle over the area you want to mirror.",
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
        if self._drag_start_widget is None:
            return
        pos = event.position().toPoint()
        img = self._image_rect()
        # Clamp the current point to the image rect so the selection stays inside.
        pos.setX(max(img.left(), min(img.right(), pos.x())))
        pos.setY(max(img.top(), min(img.bottom(), pos.y())))
        self._drag_current_widget = pos
        self.update()

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
    """Modal dialog wrapping the preview canvas with OK/Reset/Cancel buttons."""

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
        from PySide6.QtWidgets import QHBoxLayout  # local import to keep top tidy

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

    # Public hooks --------------------------------------------------------------------

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
