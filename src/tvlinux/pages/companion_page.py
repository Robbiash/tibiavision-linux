"""Companion view -- in-app tiles for every region.

On Wayland + GNOME the compositor refuses to draw **any** window above
another client's fullscreen window. That makes :class:`MirrorWindow`
invisible the moment the user runs Tibia fullscreen, even with
``WindowStaysOnTopHint``. The fix for that configuration is to stop
fighting the compositor and instead render the regions inside our own
normal window, which the user can alt-tab to or park on a second
monitor.

This page is that companion surface. It subscribes to the same
:class:`~tvlinux.regions.RegionManager` signals the floating mirrors
use and reuses the same crop-and-blit logic, so the two code paths
stay visually identical.
"""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..regions import Region, RegionManager
from ..theme import TOKENS
from ..ui_helpers import empty_state

__all__ = ["CompanionPage", "CompanionTile"]


# -- Single tile -------------------------------------------------------------


class CompanionTile(QFrame):
    """One region rendered inside the app window.

    Holds a reference to the current ``QImage`` frame and a
    :class:`Region` snapshot. ``paintEvent`` crops the frame to
    ``region.rect`` and draws it to the tile's viewport with a
    rounded-corner clip and a neon border, matching the floating
    :class:`MirrorWindow` aesthetic so users can switch placement
    modes without visual whiplash.
    """

    # Minimum preview size so even tiny regions (e.g. a single spell
    # slot) stay usable.
    _MIN_PREVIEW = 120
    _HEADER_H = 28

    double_clicked = Signal(object)  # emits region id so the shell can focus it

    def __init__(self, region: Region, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._region = region
        self._frame: QImage | None = None

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(False)
        self.setMinimumSize(self._MIN_PREVIEW, self._MIN_PREVIEW + self._HEADER_H)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("CompanionTile")
        self.setToolTip(f"Live preview of '{region.name}'")
        self.setAccessibleName(f"Companion tile {region.name}")

    # -- Public API -------------------------------------------------------

    @property
    def region_id(self) -> UUID:
        return self._region.id

    def set_region(self, region: Region) -> None:
        """Update the region snapshot and re-render. Called on region_changed."""
        self._region = region
        self.setToolTip(f"Live preview of '{region.name}'")
        self.setAccessibleName(f"Companion tile {region.name}")
        self.update()

    def set_frame(self, image: QImage | None) -> None:
        """Called on every incoming capture frame."""
        self._frame = image
        self.update()

    # -- Painting ---------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            # Panel chrome (background + rounded border).
            p = TOKENS.palette
            bg = QColor(p.bg_surface)
            border = QColor(self._region.border_color)
            if not border.isValid():
                border = QColor(p.border_strong)
            radius = min(int(TOKENS.radius.lg), min(self.width(), self.height()) // 2)
            body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            path = QPainterPath()
            path.addRoundedRect(body, radius, radius)
            painter.fillPath(path, bg)

            # Header label strip.
            header_rect = QRectF(body).adjusted(0, 0, 0, -(body.height() - self._HEADER_H))
            painter.save()
            painter.setClipPath(path)
            header_bg = QColor(p.bg_elevated)
            painter.fillRect(header_rect, header_bg)
            painter.setPen(QColor(p.text_primary))
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                header_rect.adjusted(TOKENS.spacing.md, 0, -TOKENS.spacing.md, 0),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self._region.name,
            )
            painter.restore()

            # Preview area: crop the source frame to region.rect and draw it
            # into the area below the header. Scales with aspect ratio preserved.
            preview_rect = QRectF(body).adjusted(
                TOKENS.spacing.sm,
                self._HEADER_H + TOKENS.spacing.xs,
                -TOKENS.spacing.sm,
                -TOKENS.spacing.sm,
            )
            painter.save()
            preview_path = QPainterPath()
            preview_path.addRoundedRect(preview_rect, TOKENS.radius.md, TOKENS.radius.md)
            painter.setClipPath(preview_path)
            painter.fillRect(preview_rect, QColor(p.bg_app))

            if self._frame is not None and not self._frame.isNull():
                src = self._region.rect.intersected(self._frame.rect())
                if not src.isEmpty():
                    target = self._aspect_fit(preview_rect, src.width(), src.height())
                    painter.drawImage(target, self._frame, src)
                else:
                    self._draw_placeholder(painter, preview_rect, "Region is outside capture area")
            else:
                self._draw_placeholder(painter, preview_rect, "Waiting for capture...")
            painter.restore()

            # Outer border last so it sits on top of the preview edges.
            painter.setPen(border)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        finally:
            painter.end()

    # -- Helpers ----------------------------------------------------------

    @staticmethod
    def _aspect_fit(viewport: QRectF, src_w: int, src_h: int) -> QRectF:
        """Return the largest sub-rect of ``viewport`` preserving ``src_w/src_h``."""
        if src_w <= 0 or src_h <= 0 or viewport.width() <= 0 or viewport.height() <= 0:
            return QRectF(viewport)
        src_ratio = src_w / src_h
        vp_ratio = viewport.width() / viewport.height()
        if src_ratio > vp_ratio:
            w = viewport.width()
            h = w / src_ratio
        else:
            h = viewport.height()
            w = h * src_ratio
        x = viewport.x() + (viewport.width() - w) / 2
        y = viewport.y() + (viewport.height() - h) / 2
        return QRectF(x, y, w, h)

    def _draw_placeholder(self, painter: QPainter, rect: QRectF, text: str) -> None:
        painter.setPen(QColor(TOKENS.palette.text_muted))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self.double_clicked.emit(self._region.id)
        super().mouseDoubleClickEvent(event)


# -- Page container ----------------------------------------------------------


class CompanionPage(QWidget):
    """Grid of :class:`CompanionTile` -- one per region that is visible.

    Reacts to the same :class:`RegionManager` signals the app uses to
    spawn / destroy floating mirrors, so the two surfaces always
    reflect the same region set.
    """

    # Re-exposed so shell.py can forward it (doubles as a "focus this region"
    # hint in the future).
    tile_double_clicked = Signal(object)

    _COLUMNS = 2  # Two tiles per row on normal widths; the scroll area handles overflow.

    def __init__(self, regions: RegionManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._regions = regions
        self._tiles: dict[UUID, CompanionTile] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(
            TOKENS.spacing.lg,
            TOKENS.spacing.md,
            TOKENS.spacing.lg,
            TOKENS.spacing.lg,
        )
        root.setSpacing(TOKENS.spacing.md)

        hint = QLabel(
            "Companion view renders every region inside this window. Use this "
            "when Tibia is fullscreen on a compositor that refuses floating "
            "overlays (GNOME + Wayland), or when you want a single big "
            "dashboard on a second monitor.",
            self,
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "caption")
        root.addWidget(hint)

        self._empty = empty_state(
            icon_name="target",
            title="No regions yet",
            subtitle=(
                "Create a region from the Regions page and it will show up here as a live tile."
            ),
            parent=self,
        )
        root.addWidget(self._empty)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)

        self._grid_host = QWidget(self._scroll)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(TOKENS.spacing.md)
        self._scroll.setWidget(self._grid_host)

        # Populate from any regions already in the manager (e.g. loaded from disk).
        for region in self._regions:
            self._add_tile(region)
        self._refresh_empty_state()

        # Live wiring.
        self._regions.region_added.connect(self._add_tile)
        self._regions.region_removed.connect(self._remove_tile)
        self._regions.region_changed.connect(self._update_tile)
        self._regions.regions_reset.connect(self._on_regions_reset)

    # -- Public API -------------------------------------------------------

    def set_frame(self, image: QImage | None) -> None:
        """Fan out the latest capture frame to every tile."""
        for tile in self._tiles.values():
            tile.set_frame(image)

    def tile_ids(self) -> list[UUID]:
        """Stable list of region ids with a live tile. Used by tests."""
        return list(self._tiles.keys())

    # -- Grid maintenance -------------------------------------------------

    def _add_tile(self, region: Region) -> None:
        if region.id in self._tiles:
            return
        tile = CompanionTile(region, parent=self._grid_host)
        tile.double_clicked.connect(self.tile_double_clicked)
        self._tiles[region.id] = tile
        self._replace_grid()
        self._refresh_empty_state()

    def _remove_tile(self, region_id: UUID) -> None:
        tile = self._tiles.pop(region_id, None)
        if tile is None:
            return
        tile.setParent(None)
        tile.deleteLater()
        self._replace_grid()
        self._refresh_empty_state()

    def _update_tile(self, region: Region) -> None:
        tile = self._tiles.get(region.id)
        if tile is None:
            # region_changed can fire before region_added for newly-loaded
            # profiles; re-route as an add so the UI stays coherent.
            self._add_tile(region)
            return
        tile.set_region(region)

    def _on_regions_reset(self, regions: list[Region]) -> None:
        for tid in list(self._tiles.keys()):
            self._remove_tile(tid)
        for region in regions:
            self._add_tile(region)

    def _replace_grid(self) -> None:
        """Re-lay the grid whenever the tile count changes."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.setParent(self._grid_host)
        for idx, tile in enumerate(self._tiles.values()):
            row, col = divmod(idx, self._COLUMNS)
            self._grid.addWidget(tile, row, col)

    def _refresh_empty_state(self) -> None:
        has_tiles = bool(self._tiles)
        self._empty.setVisible(not has_tiles)
        self._scroll.setVisible(has_tiles)
