"""The floating, frameless, always-on-top mirror window.

Each ``MirrorWindow`` is bound to exactly one ``Region`` and renders that region's crop
of the live capture stream. When the region is **locked**, the window is frameless and
ignores mouse input for drag/resize; when **unlocked**, the window can be dragged from
anywhere inside it and resized via 8-way edge grip handles.

Rendering uses a plain ``QPainter.drawImage(target, source, sourceRect)`` call, which
Qt accelerates on any modern GPU. For CPU-only rigs we still get 60 fps for the typical
few-hundred-pixels-wide regions used for spell bars and cooldowns.
"""

from __future__ import annotations

import math
from uuid import UUID

from PySide6.QtCore import (
    Property,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from .regions import Region

RESIZE_MARGIN = 6  # pixels from edge considered an "edge" for resizing


# Edge bitmask: 1=Left, 2=Right, 4=Top, 8=Bottom
_EDGE_LEFT = 1
_EDGE_RIGHT = 2
_EDGE_TOP = 4
_EDGE_BOTTOM = 8


class MirrorWindow(QWidget):
    """A single floating mirror bound to a single region."""

    rename_requested = Signal(UUID, str)
    delete_requested = Signal(UUID)
    region_updated = Signal(Region)  # emitted on geometry / lock / visibility changes
    # Emitted while the user drags the window. ``delta`` is the offset from the
    # previous position; ``final`` is True once the drag settles (80 ms debounce).
    moved = Signal(UUID, QPoint, bool)
    unlink_requested = Signal(UUID)

    def __init__(self, region: Region, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._region = region
        self._source_image: QImage | None = None
        self._hover_edge: int = 0
        self._glow_phase: float = 0.0
        self._last_pos: QPoint = QPoint()
        # Prevents a moveEvent driven by group-delta replay from re-emitting
        # ``moved`` and causing a feedback loop.
        self._suppress_next_move: bool = False
        self._has_peers: bool = False
        self._move_debounce = QTimer(self)
        self._move_debounce.setSingleShot(True)
        self._move_debounce.setInterval(80)
        self._move_debounce.timeout.connect(self._emit_final_move)
        self._last_delta: QPoint = QPoint()

        # Qt.Window is explicit here so the compositor treats the mirror as a
        # real top-level surface. Without it, Tool + WindowStaysOnTopHint
        # together sometimes lose their "always on top" promise on Wayland
        # when another frameless/tool window (the FloatingRegionPicker) closes
        # immediately above us -- Tibia would end up drawn on top of a freshly
        # shown mirror. Pairing Window with Tool matches the picker's own
        # flag set and keeps the stacking stable.
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # WA_NoSystemBackground prevents Qt from painting the widget's palette
        # background before paintEvent, which on NVIDIA + Wayland would otherwise
        # leave an opaque underlay that defeats per-pixel alpha.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        # WA_ShowWithoutActivating stops show() / setWindowFlags()-triggered
        # re-show from stealing active focus. On KDE Plasma 6 Wayland the
        # focus-theft side effect is what frequently nudges KWin into restacking
        # the freshly-shown mirror *below* an already-focused Tibia window,
        # defeating WindowStaysOnTopHint. StrongFocus is retained deliberately:
        # focus policy governs Qt-level key delivery (Delete/Backspace to
        # :meth:`keyPressEvent`), not compositor stacking, so dropping it to
        # NoFocus would silently kill the keyboard shortcut without helping
        # Z-order at all.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowTitle(region.name)
        self.setMinimumSize(48, 48)

        if region.geometry is None:
            self.resize(max(region.rect.width(), 200), max(region.rect.height(), 200))
        else:
            self.setGeometry(region.geometry)
        self._last_pos = self.pos()

        # Lock state drives input-transparency. On Wayland this is not
        # optional: the compositor routes pointer events to whatever
        # surface the cursor is over, and an overlay that accepts clicks
        # will swallow them *before* they reach Tibia -- which on
        # layer-shell is the most common failure mode we ship against
        # (user tries to cast a spell through a mirror, Tibia sees
        # nothing). See :meth:`_apply_input_transparency` for the full
        # policy.
        self._apply_input_transparency()

        self._glow_anim = QPropertyAnimation(self, b"glow_phase_prop", self)
        self._glow_anim.setStartValue(0.0)
        self._glow_anim.setEndValue(1.0)
        self._glow_anim.setDuration(1800)
        self._glow_anim.setLoopCount(-1)
        if region.border_glow:
            self._glow_anim.start()

    # -- Region wiring ----------------------------------------------------------------

    @property
    def region_id(self) -> UUID:
        return self._region.id

    def set_region(self, region: Region) -> None:
        """Called by the owning app whenever the region model mutates."""
        prev = self._region
        self._region = region
        self.setWindowTitle(region.name)
        if region.always_on_top != prev.always_on_top:
            flags = self.windowFlags()
            if region.always_on_top:
                flags |= Qt.WindowType.WindowStaysOnTopHint
            else:
                flags &= ~Qt.WindowType.WindowStaysOnTopHint
            was_visible = self.isVisible()
            self.setWindowFlags(flags)
            if was_visible:
                self.show()
        # Always re-apply input-transparency rather than diffing against
        # ``prev.locked``. In practice the context menu flips the lock
        # in-place on ``self._region`` before emitting ``region_updated``,
        # which means ``prev is region`` by the time we get here and the
        # naive ``prev.locked != region.locked`` comparison always
        # reads False. setAttribute is idempotent so the unconditional
        # call is free.
        self._apply_input_transparency()
        if region.border_glow and self._glow_anim.state() != QPropertyAnimation.State.Running:
            self._glow_anim.start()
        elif not region.border_glow and self._glow_anim.state() == QPropertyAnimation.State.Running:
            self._glow_anim.stop()
        # Apply geometry before show(); on Wayland, the compositor can
        # otherwise pick a placeholder position first and briefly flash the
        # window in the wrong place.
        if region.geometry and region.geometry != self.geometry():
            self.setGeometry(region.geometry)
        if region.visible and not self.isVisible():
            self.show()
        elif not region.visible and self.isVisible():
            self.hide()
        self.update()

    def _apply_input_transparency(self) -> None:
        """Flip the mirror between click-through and interactive.

        This is what actually lets the user *play Tibia* while a mirror
        is on top of it. On Wayland, pointer events are routed by the
        compositor to whichever surface owns the input region at the
        cursor position. A mirror that accepts clicks will swallow them
        before they ever reach Tibia -- the user tries to cast a spell
        through a spellbar mirror and nothing happens because the
        compositor delivered the click to us and we silently dropped
        it. Qt's ``WA_TransparentForMouseEvents`` on a top-level widget
        translates to ``wl_surface.set_input_region(empty)`` on the
        Wayland side, which tells the compositor "skip this surface
        for pointer hit-testing, deliver to whatever's underneath"
        (i.e., Tibia). This is the exact mechanism TibiaVision.com
        describes as "strictly click-through"; it is also what
        :class:`~tvlinux.smart_hud.SmartHud` already uses for the HUD.

        Policy:

        - ``region.locked`` -> click-through + no focus. The mirror is
          purely visual. Play Tibia as if the mirror wasn't there.
        - ``region.locked is False`` -> fully interactive. Drag from
          anywhere, resize from the edges, right-click for the context
          menu, Delete to remove. The mirror will block clicks to
          Tibia while unlocked -- that's the point, the user is
          editing it.

        Toggling ``WA_TransparentForMouseEvents`` on a top-level
        widget while it is mapped requires a reconfigure round-trip
        on some compositors to re-commit the input region. Qt handles
        that internally for us via ``QWindow::requestUpdate`` on
        attribute change; no show/hide dance needed.
        """
        locked = self._region.locked
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, locked)
        if locked:
            # No keyboard focus either: the mirror shouldn't eat
            # Delete/Backspace while the user is playing, and
            # WindowStaysOnTopHint + layer-shell overlay already
            # keep us visually on top without needing focus.
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            # Give up any keyboard focus we might be holding right
            # now. Otherwise the next keystroke (say, the user's
            # next hotkey in Tibia) would still be routed to us
            # until the compositor rebinds focus.
            self.clearFocus()
            # Reset cursor so a locked mirror doesn't leave behind a
            # resize-arrow cursor shape that confuses the user.
            self.unsetCursor()
        else:
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_frame(self, image: QImage) -> None:
        """Called on every incoming capture frame."""
        self._source_image = image
        if self._region.visible and self.isVisible():
            self.update()

    # -- Painting ---------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        # Radius clamped so it never exceeds half the shortest side (which would
        # render as a weird lemon shape).
        radius = max(0, min(self._region.corner_radius, min(self.width(), self.height()) // 2))
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(self.rect()), radius, radius)
        target = self.rect().adjusted(1, 1, -1, -1)

        # Render the frame into an offscreen ARGB32-premultiplied buffer first.
        # painter.setOpacity() applied directly to drawImage() on the widget is
        # unreliable on NVIDIA + Wayland + Qt.Tool: the compositor sometimes
        # treats the window surface as opaque and the slider has no effect.
        # Compositing offscreen and then blitting with setOpacity guarantees
        # per-pixel alpha regardless of the window/compositor path.
        buf = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
        buf.fill(Qt.GlobalColor.transparent)

        bp = QPainter(buf)
        bp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        bp.setClipPath(clip_path)
        if self._source_image is not None:
            src = self._region.rect.intersected(self._source_image.rect())
            if not src.isEmpty():
                bp.drawImage(target, self._source_image, src)
            else:
                self._draw_placeholder(bp, "Region is outside capture area")
        else:
            self._draw_placeholder(bp, "Waiting for capture...")
        if self._region.grid:
            self._draw_grid(bp, target)
        bp.end()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # Explicit transparent clear: some compositors (NVIDIA + Wayland) do not
        # reliably zero the backing store before paintEvent.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        painter.setOpacity(max(0.2, min(1.0, self._region.opacity)))
        painter.drawImage(0, 0, buf)
        painter.setOpacity(1.0)

        # Border and resize grip are always drawn fully opaque so the window
        # stays visibly "there" even at low content opacity.
        self._draw_border(painter, clip_path)
        if not self._region.locked:
            self._draw_resize_hint(painter)

    def _resolve_border_color(self) -> QColor:
        c = QColor(self._region.border_color)
        if not c.isValid():
            c = QColor("#0f8fbf")
        return c

    def _draw_border(self, painter: QPainter, path: QPainterPath) -> None:
        """Render the region border.

        When ``border_glow`` is on, we layer two strokes to create a neon
        bloom effect: a wide (18 px) translucent outer halo plus a solid
        6 px core. When it is off, a chunky 8 px solid frame is drawn.
        Every stroke draws on an inset path so the outer pen edge sits
        flush with the widget boundary (the window frame hard-clips
        anything that bleeds past ``self.rect()``).

        ``path`` is retained for API compatibility with callers that might
        want the outer clip contour; the border itself is drawn on inset
        paths so it stays fully visible.
        """
        del path  # reserved for future compositing passes
        base = self._resolve_border_color()
        if self._region.border_glow:
            # Pulse between 0.5 and 1.0 intensity on glow_phase.
            t = 0.75 - 0.25 * math.cos(self._glow_phase * 2 * math.pi)
            # Inset matches half the outer pen width so the bloom halo's
            # outer edge sits flush with the widget edge -- no clipping,
            # every pixel of the pen is visible.
            bloom_path = self._inset_rounded_path(inset=9)

            color_outer = QColor(base.red(), base.green(), base.blue(), int(255 * 0.35 * t))
            pen_outer = QPen(color_outer, 18)
            pen_outer.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen_outer)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(bloom_path)

            color_core = QColor(base.red(), base.green(), base.blue(), int(255 * t))
            pen_core = QPen(color_core, 6)
            pen_core.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen_core)
            painter.drawPath(bloom_path)
        else:
            # Fat, flush-to-edge border: inset by half the pen width so the
            # outer edge of the stroke coincides with the widget boundary
            # (no clipping, whole pen visible, reads as a chunky frame).
            solid_path = self._inset_rounded_path(inset=4)
            pen = QPen(base, 8)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(solid_path)

    def _inset_rounded_path(self, inset: int) -> QPainterPath:
        """Return a rounded-rect path inset from ``self.rect()`` by ``inset`` px.

        Radius is reduced by the same amount (floor 0) so the geometry stays
        visually concentric. Used by :meth:`_draw_border` so the bloom halo
        has room to expand without being clipped at the widget edge.
        """
        radius = max(0, self._region.corner_radius - inset)
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def _draw_grid(self, painter: QPainter, target: QRect) -> None:
        pen = QPen(QColor(255, 255, 255, 40), 1)
        painter.setPen(pen)
        step = max(4, self._region.grid_spacing)
        x = target.left() + step
        while x < target.right():
            painter.drawLine(x, target.top(), x, target.bottom())
            x += step
        y = target.top() + step
        while y < target.bottom():
            painter.drawLine(target.left(), y, target.right(), y)
            y += step

    def _draw_placeholder(self, painter: QPainter, text: str) -> None:
        painter.fillRect(self.rect(), QColor(20, 20, 20, 230))
        painter.setPen(QColor(180, 180, 180))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def _draw_resize_hint(self, painter: QPainter) -> None:
        # Subtle corner grip indicator in bottom-right.
        c = QColor(255, 255, 255, 120)
        painter.setPen(QPen(c, 1))
        r = self.rect()
        for i in range(1, 4):
            painter.drawLine(
                r.right() - 3 - i * 3,
                r.bottom() - 3,
                r.right() - 3,
                r.bottom() - 3 - i * 3,
            )

    # -- Glow animation property ------------------------------------------------------

    def _get_glow_phase(self) -> float:
        return self._glow_phase

    def _set_glow_phase(self, v: float) -> None:
        self._glow_phase = v
        if self._region.border_glow:
            self.update()

    glow_phase_prop = Property(float, _get_glow_phase, _set_glow_phase)

    # -- Mouse interaction ------------------------------------------------------------

    def _edge_at(self, pos: QPoint) -> int:
        """Return a bitmask indicating which edges (if any) ``pos`` is on."""
        e = 0
        if pos.x() <= RESIZE_MARGIN:
            e |= _EDGE_LEFT
        if pos.x() >= self.width() - RESIZE_MARGIN:
            e |= _EDGE_RIGHT
        if pos.y() <= RESIZE_MARGIN:
            e |= _EDGE_TOP
        if pos.y() >= self.height() - RESIZE_MARGIN:
            e |= _EDGE_BOTTOM
        return e

    @staticmethod
    def _cursor_for_edge(edge: int) -> Qt.CursorShape:
        if edge in (_EDGE_LEFT | _EDGE_TOP, _EDGE_RIGHT | _EDGE_BOTTOM):
            return Qt.CursorShape.SizeFDiagCursor
        if edge in (_EDGE_RIGHT | _EDGE_TOP, _EDGE_LEFT | _EDGE_BOTTOM):
            return Qt.CursorShape.SizeBDiagCursor
        if edge in (_EDGE_LEFT, _EDGE_RIGHT):
            return Qt.CursorShape.SizeHorCursor
        if edge in (_EDGE_TOP, _EDGE_BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.OpenHandCursor

    @staticmethod
    def _qt_edges_from_mask(edge: int) -> Qt.Edge:
        edges: Qt.Edge = Qt.Edge(0)
        if edge & _EDGE_LEFT:
            edges |= Qt.Edge.LeftEdge
        if edge & _EDGE_RIGHT:
            edges |= Qt.Edge.RightEdge
        if edge & _EDGE_TOP:
            edges |= Qt.Edge.TopEdge
        if edge & _EDGE_BOTTOM:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._region.locked:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Delegate move/resize to the compositor. This is REQUIRED on Wayland
            # (client-side ``self.move()`` is a no-op there) and works fine on X11.
            wh = self.windowHandle()
            if wh is None:
                return
            edge = self._edge_at(event.position().toPoint())
            if edge:
                wh.startSystemResize(self._qt_edges_from_mask(edge))
            else:
                wh.startSystemMove()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # startSystemMove/Resize takes over the gesture, so we only need to
        # update the hover cursor while the user is NOT dragging.
        if self._region.locked:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        edge = self._edge_at(event.position().toPoint())
        if edge != self._hover_edge:
            self._hover_edge = edge
            self.setCursor(QCursor(self._cursor_for_edge(edge)))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # Nothing to do; compositor-driven move/resize completion is reported to
        # us via ``moveEvent`` and ``resizeEvent`` below.
        pass

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit(self._region.id)
            event.accept()
            return
        super().keyPressEvent(event)

    def moveEvent(self, event: QMoveEvent) -> None:
        super().moveEvent(event)
        new_pos = self.pos()
        delta = new_pos - self._last_pos
        self._last_pos = new_pos
        self._persist_geometry()
        if self._suppress_next_move:
            self._suppress_next_move = False
            return
        if delta.isNull():
            return
        self._last_delta = delta
        self.moved.emit(self._region.id, delta, False)
        self._move_debounce.start()

    def _emit_final_move(self) -> None:
        self.moved.emit(self._region.id, self._last_delta, True)

    def set_has_peers(self, has_peers: bool) -> None:
        """Let the owner tell us whether this mirror is part of a group.

        Used only to enable/disable the ``Unlink from group`` context action.
        """
        self._has_peers = bool(has_peers)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename region", "New name:", text=self._region.name
        )
        if ok and new_name.strip():
            self.rename_requested.emit(self._region.id, new_name.strip())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._persist_geometry()

    def _persist_geometry(self) -> None:
        """Save the live window geometry back onto the region model."""
        # Hidden frameless Tool windows on Wayland occasionally emit spurious
        # move/resize events with compositor-chosen placeholder geometry as
        # they are unmapped. Writing that back would clobber the real saved
        # position, so ignore anything that arrives while we're not visible.
        if not self.isVisible():
            return
        new_geo = QRect(self.geometry())
        current = self._region.geometry
        if current is None or current != new_geo:
            self._region.geometry = new_geo
            self.region_updated.emit(self._region)

    def changeEvent(self, event: QEvent) -> None:
        # Nothing to do; kept for future hooks (active-window gradient, etc.).
        super().changeEvent(event)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        act_lock = menu.addAction("Unlock" if self._region.locked else "Lock")
        act_hide = menu.addAction("Hide")
        act_rename = menu.addAction("Rename...")
        menu.addSeparator()
        act_glow = menu.addAction("Border glow")
        act_glow.setCheckable(True)
        act_glow.setChecked(self._region.border_glow)
        act_grid = menu.addAction("Grid overlay")
        act_grid.setCheckable(True)
        act_grid.setChecked(self._region.grid)
        menu.addSeparator()
        act_unlink = menu.addAction("Unlink from group")
        act_unlink.setEnabled(self._has_peers)
        act_delete = menu.addAction("Delete region")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is act_unlink:
            self.unlink_requested.emit(self._region.id)
            return
        if chosen is act_lock:
            self._region.locked = not self._region.locked
            self.region_updated.emit(self._region)
        elif chosen is act_hide:
            self._region.visible = False
            self.region_updated.emit(self._region)
        elif chosen is act_rename:
            new_name, ok = QInputDialog.getText(
                self, "Rename region", "New name:", text=self._region.name
            )
            if ok and new_name.strip():
                self.rename_requested.emit(self._region.id, new_name.strip())
        elif chosen is act_glow:
            self._region.border_glow = act_glow.isChecked()
            self.region_updated.emit(self._region)
        elif chosen is act_grid:
            self._region.grid = act_grid.isChecked()
            self.region_updated.emit(self._region)
        elif chosen is act_delete:
            self.delete_requested.emit(self._region.id)

    # Convenience for callers to pre-size windows to match region aspect.
    def size_to_region(self) -> None:
        if self._region.rect.width() > 0 and self._region.rect.height() > 0:
            self.resize(QSize(self._region.rect.width(), self._region.rect.height()))
