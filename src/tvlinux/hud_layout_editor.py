"""Companion window for repositioning HUD panels.

Why a separate window instead of toggling the live HUD into
"editable" mode:

The live :class:`~tvlinux.smart_hud.SmartHud` is deliberately
click-through (``WindowTransparentForInput`` + ``WA_TransparentForMouseEvents``)
so its mouse events reach the game underneath. Flipping that off at
runtime to accept drags is a fragile dance on Wayland -- some
compositors cache the input-region bit per-window, and recovering
from "HUD ate my Tibia clicks" would be a bad user experience. It
also makes BattlEye / anti-cheat audits harder because we would be
momentarily sending real input events to a window on top of Tibia.

A separate companion window sidesteps both problems. The editor is
just a normal :class:`QMainWindow` styled to look like the game's
screen, draws the same panels via :meth:`HudPanel.paint`, and lets
the user drag tiles around. On Save we write the new positions to
``hud_layout.json`` and call ``SmartHud._relayout`` so the live HUD
picks them up on its next frame. The live HUD never loses its
click-through promise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import TOKENS

if TYPE_CHECKING:  # pragma: no cover
    from .smart_hud import HudPanel, SmartHud


class PanelTile(QWidget):
    """Draggable tile that renders a :class:`HudPanel` via its own paint.

    Reusing ``panel.paint`` rather than re-implementing the visuals
    means the editor is guaranteed to preview exactly what the live
    HUD would render -- no drift between "looks like" and "is".
    """

    moved = Signal(str, QPointF)  # panel_id, new top-left in editor coords

    def __init__(self, panel: HudPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel = panel
        self._drag_offset: QPoint | None = None

        size = panel.preferred_size()
        # Tile size matches the panel's preferred footprint 1:1 so that
        # dragging placements read as pixel-accurate on the live HUD.
        self.resize(max(40, int(size.width())), max(24, int(size.height())))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(f"Drag to move '{panel.id}'")
        self.setAccessibleName(f"HUD panel {panel.id}")
        self.setAccessibleDescription("Draggable HUD panel preview")

    @property
    def panel(self) -> HudPanel:
        return self._panel

    @property
    def panel_id(self) -> str:
        return self._panel.id

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            # Dotted "handle" rim so the user can see the tile's edges
            # even if the panel itself paints mostly-transparent content.
            rim = QColor(TOKENS.palette.accent)
            rim.setAlphaF(0.35)
            painter.setPen(rim)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            local = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
            self._panel.paint(painter, local)
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.raise_()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            return
        parent = self.parentWidget()
        if parent is None:
            return
        # mapToParent expects a QPoint; the global-pos dance keeps the
        # drag stable even if the tile itself is moving between frames.
        new_pos = parent.mapFromGlobal(event.globalPosition().toPoint()) - self._drag_offset
        # Clamp to parent bounds so tiles can't be dragged off-screen
        # and become unreachable.
        max_x = max(0, parent.width() - self.width())
        max_y = max(0, parent.height() - self.height())
        new_pos.setX(max(0, min(max_x, new_pos.x())))
        new_pos.setY(max(0, min(max_y, new_pos.y())))
        self.move(new_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            pos = self.pos()
            self.moved.emit(self._panel.id, QPointF(float(pos.x()), float(pos.y())))
            event.accept()
            return
        super().mouseReleaseEvent(event)


class HudLayoutEditor(QMainWindow):
    """Window that lets the user rearrange HUD panels with drag and drop.

    Not a modal; users often want to alt-tab to Tibia, watch the live
    HUD, and tweak positions based on how things overlap with the
    game's own UI. Close (X) cancels; Save writes back.
    """

    layout_saved = Signal()
    cancelled = Signal()

    def __init__(self, hud: SmartHud, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hud = hud
        self.setWindowTitle("HUD Layout Editor")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        info = QLabel(
            "Drag each panel to where you want it on your screen. "
            "The grey box matches your monitor. Save when you're happy.",
            self,
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            f"background-color: {TOKENS.palette.bg_surface};"
            f" color: {TOKENS.palette.text_secondary};"
            f" padding: {TOKENS.spacing.sm}px {TOKENS.spacing.md}px;"
        )
        root.addWidget(info)

        # Canvas is proportional to the HUD's own size. We cap it so
        # the editor window fits on a 1080p laptop even if the target
        # screen is 4K.
        self._canvas = _EditorCanvas(self)
        root.addWidget(self._canvas, 1)

        actions = QWidget(self)
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.sm, TOKENS.spacing.md, TOKENS.spacing.sm
        )
        actions_row.addStretch(1)
        self._reset_btn = QPushButton("Reset positions", actions)
        self._reset_btn.setToolTip("Discard overrides and rebuild default layout")
        self._reset_btn.clicked.connect(self._reset_positions)
        actions_row.addWidget(self._reset_btn)
        self._cancel_btn = QPushButton("Cancel", actions)
        self._cancel_btn.clicked.connect(self._on_cancel)
        actions_row.addWidget(self._cancel_btn)
        self._save_btn = QPushButton("Save layout", actions)
        self._save_btn.setProperty("variant", "primary")
        self._save_btn.clicked.connect(self._on_save)
        actions_row.addWidget(self._save_btn)
        root.addWidget(actions)

        self._tiles: dict[str, PanelTile] = {}
        self._build_tiles()
        self._size_to_hud()

    # -- Internals --------------------------------------------------------

    def _hud_geometry(self) -> tuple[int, int]:
        geom = self._hud.geometry()
        return geom.width(), geom.height()

    def _size_to_hud(self) -> None:
        w, h = self._hud_geometry()
        # Cap at 90 % of the primary screen so users on smaller
        # monitors can still close the window.
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(w, int(avail.width() * 0.9))
            h = min(h, int(avail.height() * 0.9))
        self.resize(max(640, w), max(480, h))

    def _build_tiles(self) -> None:
        for panel in self._hud.panels():
            tile = PanelTile(panel, parent=self._canvas)
            slot = self._hud._slots.get(panel.id)
            if slot is not None:
                tile.move(int(slot.rect.x()), int(slot.rect.y()))
            tile.show()
            self._tiles[panel.id] = tile

    def _reset_positions(self) -> None:
        # Clear overrides in memory and ask the HUD to regenerate default
        # slot rects, then rebuild tile positions.
        self._hud._slots_clear_overrides()
        for pid, tile in self._tiles.items():
            slot = self._hud._slots.get(pid)
            if slot is not None:
                tile.move(int(slot.rect.x()), int(slot.rect.y()))

    def _on_save(self) -> None:
        positions = {pid: QPointF(float(t.x()), float(t.y())) for pid, t in self._tiles.items()}
        self._hud.apply_layout_overrides(positions)
        self._hud.save_layout()
        self.layout_saved.emit()
        self.close()

    def _on_cancel(self) -> None:
        self.cancelled.emit()
        self.close()


class _EditorCanvas(QWidget):
    """Matte that represents the player's screen; tile container."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            f"background-color: {TOKENS.palette.bg_app};"
            f" border: 1px dashed {TOKENS.palette.border_strong};"
        )


__all__ = ["HudLayoutEditor", "PanelTile"]
