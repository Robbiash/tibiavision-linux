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
    QGuiApplication,
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


def _debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
) -> None:
    _ = (run_id, hypothesis_id, location, message, data)
    return


def _is_xcb_platform() -> bool:
    """True when Qt is running under the xcb (X11 / XWayland) QPA plugin.

    Module-level (rather than a method on :class:`MirrorWindow`) so tests
    can monkeypatch it without touching class descriptors -- PySide6's
    signal/slot binding is sensitive to mutated class dicts and crashes
    when ``staticmethod`` is replaced with a ``MagicMock`` in the middle
    of ``__init__``.
    """
    if QGuiApplication.instance() is None:
        return False
    return QGuiApplication.platformName() == "xcb"


RESIZE_MARGIN = 6  # pixels from edge considered an "edge" for resizing
_ALERT_BLINK_TICK_MS = 110


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
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H7",
            location="mirror_window.py:__init__:entry",
            message="mirror_window_ctor_entered",
            data={
                "region_id": str(region.id),
                "visible": bool(region.visible),
                "locked": bool(region.locked),
                "has_geometry": region.geometry is not None,
            },
        )
        # endregion
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
        self._geometry_dirty: bool = False
        self._cooldown_alert_remaining_ms: int = 0
        self._cooldown_alert_visible: bool = False
        self._debug_first_paint_logged: bool = False
        self._debug_paint_count: int = 0
        self._debug_set_frame_count: int = 0
        self._cooldown_alert_timer = QTimer(self)
        self._cooldown_alert_timer.setInterval(_ALERT_BLINK_TICK_MS)
        self._cooldown_alert_timer.timeout.connect(self._on_cooldown_alert_tick)

        self.setWindowFlags(self._compute_window_flags())
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
        self._region = region
        self.setWindowTitle(region.name)
        debug_geometry_none = region.geometry is None
        if debug_geometry_none:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H12",
                location="mirror_window.py:set_region:entry",
                message="set_region_entered_without_saved_geometry",
                data={
                    "region_id": str(region.id),
                    "visible": bool(region.visible),
                    "locked": bool(region.locked),
                    "is_visible": bool(self.isVisible()),
                },
            )
            # endregion
        # Any property that feeds into ``_compute_window_flags`` needs to
        # trigger a recompute; today that's ``always_on_top`` (toggles
        # ``WindowStaysOnTopHint``) and ``locked`` (toggles
        # ``BypassWindowManagerHint`` on xcb, promoting the mirror to an
        # X11 override-redirect window that KWin cannot re-stack below a
        # fullscreen/focused Tibia). The naive ``prev.locked != region.locked``
        # check is unreliable because the context menu mutates the region
        # in-place before emitting ``region_updated``, so ``prev is region``
        # by the time we get here; instead we diff the computed flag sets
        # themselves and only re-apply when they actually changed. That
        # matters: ``setWindowFlags`` on a mapped X11 window destroys and
        # re-creates the native window, so doing it on every toggle flashes
        # the mirror.
        # Apply input-transparency (widget-attribute + focus policy) FIRST,
        # before we touch window flags. Qt6 couples the top-level
        # ``WA_TransparentForMouseEvents`` attribute with the underlying
        # QWindow's ``WindowTransparentForInput`` flag, so a stale
        # ``WA_TransparentForMouseEvents == True`` at the moment
        # ``setWindowFlags`` runs can silently re-introduce
        # ``WindowTransparentForInput`` into the recomputed flag set and
        # make an unlock fail to restore click interactivity. Flipping
        # the attribute first keeps the two state machines coherent.
        # Unconditional: the context menu mutates the region in-place
        # before emitting ``region_updated`` (so ``prev is region`` and
        # a naive diff misses the toggle), and setAttribute is idempotent.
        self._apply_input_transparency()
        desired_flags = self._compute_window_flags()
        if debug_geometry_none:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H12",
                location="mirror_window.py:set_region:flags_state",
                message="set_region_flag_state",
                data={
                    "region_id": str(self._region.id),
                    "flags_changed": int(desired_flags != self.windowFlags()),
                    "is_visible": bool(self.isVisible()),
                },
            )
            # endregion
        if desired_flags != self.windowFlags():
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H1",
                location="mirror_window.py:set_region:flags_rebuild",
                message="rebuilding_window_flags",
                data={
                    "region_id": str(self._region.id),
                    "locked": bool(self._region.locked),
                    "visible": bool(self._region.visible),
                    "was_visible": bool(self.isVisible()),
                },
            )
            # endregion
            was_visible = self.isVisible()
            self.setWindowFlags(desired_flags)
            if was_visible:
                self.show()
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
        if debug_geometry_none:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H12",
                location="mirror_window.py:set_region:exit",
                message="set_region_completed_without_saved_geometry",
                data={
                    "region_id": str(self._region.id),
                    "is_visible": bool(self.isVisible()),
                    "window_w": int(self.width()),
                    "window_h": int(self.height()),
                    "window_handle_is_none": self.windowHandle() is None,
                },
            )
            # endregion
        self.update()

    def _compute_window_flags(self) -> Qt.WindowType:
        """Assemble the window-flag bitmask for the current region state.

        The base set -- frameless, tool, stays-on-top, no drop shadow --
        matches what the FloatingRegionPicker uses so stacking stays
        consistent. ``Qt.Window`` is included explicitly so the compositor
        treats us as a real top-level (without it, Tool + StaysOnTop can
        regress to "held below a just-closed sibling" on Plasma Wayland).

        Two conditional flags are layered on top when ``region.locked``:

        **``Qt.WindowTransparentForInput``** (all platforms). This is the
        Qt flag that actually makes clicks and keystrokes pass through
        the window to whatever is underneath (i.e., Tibia). Do NOT
        confuse this with the ``WA_TransparentForMouseEvents`` *widget*
        attribute, which only reroutes events inside Qt's own widget
        hierarchy -- on X11/XWayland, a top-level widget with only that
        attribute still captures X server pointer events and Tibia
        never sees the click. ``Qt.WindowTransparentForInput`` is the
        *window-system-level* primitive: on X11 it sets an empty
        ``XShape`` input region via the XShape extension, on Wayland it
        sets an empty ``wl_surface.set_input_region``. Both make the
        window invisible to the display server's hit-testing, which is
        exactly what "let the user play Tibia through the mirror" means.
        This was the bug that made the previous XWayland migration
        regress click-through: widget-level mouse transparency alone is
        not enough on X11.

        **``Qt.BypassWindowManagerHint``** (xcb only). Maps to the X11
        ``override_redirect`` flag, which tells the X server "bypass the
        window manager entirely". Consequences:

        1. KWin / Mutter / any X11 WM cannot re-stack an override-redirect
           window -- not on focus change, not on fullscreen toggle, not
           ever. This is the same protocol-level guarantee Discord, Steam,
           MangoHud, and RivaTuner use to keep their overlays above
           fullscreen games. Tibia runs under Wine and is therefore
           always an XWayland client, so our override-redirect window
           sits in the same rootless X server and beats it by protocol,
           not by policy.
        2. No WM interaction means no ``_NET_WM_MOVERESIZE`` /
           ``startSystemMove``, no WM-drawn decorations, no focus-stealing
           prevention fighting us. We only accept this trade-off while
           locked, because a locked mirror is input-transparent anyway
           and the user has no need to drag/resize it. Unlocking drops
           both conditional flags so standard WM move/resize works and
           the mirror can be repositioned like any other window.

        On Wayland (``platformName() == "wayland"``) or offscreen (tests),
        ``BypassWindowManagerHint`` is a no-op, so omitting it costs
        nothing and keeps the flag bitmask predictable. Stay-on-top under
        ``--force-wayland`` is instead provided by
        :mod:`tvlinux.layer_shell`'s overlay-layer promotion.
        """
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if self._region.locked:
            flags |= Qt.WindowType.WindowTransparentForInput
            if _is_xcb_platform():
                flags |= Qt.WindowType.BypassWindowManagerHint
        return flags

    def _apply_input_transparency(self) -> None:
        """Flip the mirror between click-through and interactive.

        The *load-bearing* click-through mechanism is the
        ``Qt.WindowTransparentForInput`` window flag applied in
        :meth:`_compute_window_flags`. That flag is what instructs the
        display server (X11 via XShape, Wayland via
        ``wl_surface.set_input_region``) to skip the mirror during
        pointer / keyboard hit-testing and deliver the event to Tibia
        underneath. Without it, the user tries to cast a spell through
        the spellbar mirror and nothing happens because we captured
        the click at the window-system level.

        This method handles the Qt-layer consequences of that flag:

        - ``WA_TransparentForMouseEvents``: belt-and-suspenders so any
          mouse events that somehow still make it into Qt's event
          queue get routed as if the widget wasn't there. On its own
          this is not enough on X11 (events are already delivered to
          the X window before Qt sees them) but it's cheap and
          harmless to keep.
        - ``FocusPolicy.NoFocus``: stops Qt from trying to give the
          window keyboard focus on click. Complements
          ``Qt.WindowTransparentForInput`` for keystroke routing.
        - ``clearFocus``: give up any focus we hold *right now*, so
          the next keypress goes to whichever window the WM decides
          instead of getting queued to us.
        - ``unsetCursor``: reset the cursor shape so a previously
          hovered edge-resize arrow doesn't persist once the mirror
          is click-through.

        Policy:

        - ``region.locked`` -> click-through + no focus. The mirror is
          purely visual. Play Tibia as if the mirror wasn't there.
        - ``region.locked is False`` -> fully interactive. Drag from
          anywhere, resize from the edges, right-click for the context
          menu, Delete to remove. The mirror will block clicks to
          Tibia while unlocked -- that's the point, the user is
          editing it.
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
        if self._debug_set_frame_count < 8:
            self._debug_set_frame_count += 1
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H31",
                location="mirror_window.py:set_frame",
                message="mirror_set_frame_called",
                data={
                    "region_id": str(self._region.id),
                    "count": int(self._debug_set_frame_count),
                    "is_visible": bool(self.isVisible()),
                    "img_w": int(image.width()),
                    "img_h": int(image.height()),
                },
            )
            # endregion
        self._source_image = image
        if self._region.visible and self.isVisible():
            self.update()

    def trigger_cooldown_alert(self, duration_ms: int = 1700) -> None:
        """Blink the border for cooldown-ready alerts.

        Called by :class:`Application` when the cooldown analyzer predicts a
        tracked spell is about to be ready (e.g. <= 1.9s remaining).
        """
        self._cooldown_alert_remaining_ms = max(0, int(duration_ms))
        self._cooldown_alert_visible = True
        if self._cooldown_alert_remaining_ms <= 0:
            self._cooldown_alert_timer.stop()
            self.update()
            return
        self._cooldown_alert_timer.start()
        self.update()

    def _on_cooldown_alert_tick(self) -> None:
        if self._cooldown_alert_remaining_ms <= 0:
            self._cooldown_alert_timer.stop()
            self._cooldown_alert_visible = False
            self.update()
            return
        self._cooldown_alert_remaining_ms -= _ALERT_BLINK_TICK_MS
        self._cooldown_alert_visible = not self._cooldown_alert_visible
        if self._cooldown_alert_remaining_ms <= 0:
            self._cooldown_alert_timer.stop()
            self._cooldown_alert_visible = False
        self.update()

    # -- Painting ---------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._debug_paint_count < 8:
            self._debug_paint_count += 1
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H32",
                location="mirror_window.py:paintEvent:cycle",
                message="mirror_paint_cycle",
                data={
                    "region_id": str(self._region.id),
                    "count": int(self._debug_paint_count),
                    "is_visible": bool(self.isVisible()),
                    "has_source_image": self._source_image is not None,
                    "w": int(self.width()),
                    "h": int(self.height()),
                },
            )
            # endregion
        if not self._debug_first_paint_logged:
            self._debug_first_paint_logged = True
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H14",
                location="mirror_window.py:paintEvent:first_paint",
                message="mirror_first_paint",
                data={
                    "region_id": str(self._region.id),
                    "has_source_image": self._source_image is not None,
                    "rect_w": int(self._region.rect.width()),
                    "rect_h": int(self._region.rect.height()),
                    "window_w": int(self.width()),
                    "window_h": int(self.height()),
                },
            )
            # endregion
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
        if self._cooldown_alert_visible:
            # High-contrast blink layer for cooldown-ready alerts.
            alert_path = self._inset_rounded_path(inset=4)
            pen = QPen(QColor(255, 64, 64, 255), 8)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(alert_path)
            return
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
        if event.button() == Qt.MouseButton.LeftButton:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H17",
                location="mirror_window.py:mousePressEvent:left_entry",
                message="left_press_received",
                data={
                    "region_id": str(self._region.id),
                    "locked": bool(self._region.locked),
                    "is_visible": bool(self.isVisible()),
                    "has_geometry": self._region.geometry is not None,
                    "window_handle_is_none": self.windowHandle() is None,
                },
            )
            # endregion
        if self._region.locked:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Delegate move/resize to the compositor. This is REQUIRED on Wayland
            # (client-side ``self.move()`` is a no-op there) and works fine on X11.
            wh = self.windowHandle()
            edge = self._edge_at(event.position().toPoint())
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H5",
                location="mirror_window.py:mousePressEvent:left",
                message="mirror_move_or_resize_requested",
                data={
                    "region_id": str(self._region.id),
                    "edge_mask": int(edge),
                    "window_handle_is_none": wh is None,
                    "locked": bool(self._region.locked),
                },
            )
            # endregion
            if wh is None:
                return
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H18",
                location="mirror_window.py:mousePressEvent:before_system_move",
                message="about_to_start_system_move_or_resize",
                data={
                    "region_id": str(self._region.id),
                    "edge_mask": int(edge),
                    "window_handle_is_none": False,
                },
            )
            # endregion
            try:
                if edge:
                    started_raw = wh.startSystemResize(self._qt_edges_from_mask(edge))
                else:
                    started_raw = wh.startSystemMove()
            except Exception as exc:
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H19",
                    location="mirror_window.py:mousePressEvent:system_move_exception",
                    message="start_system_move_or_resize_raised",
                    data={
                        "region_id": str(self._region.id),
                        "edge_mask": int(edge),
                        "exc_type": type(exc).__name__,
                        "exc": str(exc),
                    },
                )
                # endregion
                raise
            started = None if started_raw is None else bool(started_raw)
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H19",
                location="mirror_window.py:mousePressEvent:after_system_move",
                message="system_move_or_resize_started",
                data={
                    "region_id": str(self._region.id),
                    "edge_mask": int(edge),
                    "started": started,
                    "started_raw_is_none": started_raw is None,
                },
            )
            # endregion
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
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H2",
            location="mirror_window.py:moveEvent",
            message="mirror_move_event",
            data={
                "region_id": str(self._region.id),
                "delta_x": int(delta.x()),
                "delta_y": int(delta.y()),
                "suppress_next_move": bool(self._suppress_next_move),
                "is_visible": bool(self.isVisible()),
            },
        )
        # endregion
        self._last_pos = new_pos
        # During active drag, keep geometry on the shared Region object
        # but defer the expensive region_updated -> RegionManager.update ->
        # app._update_mirror feedback until the move settles.
        self._persist_geometry(emit_signal=False)
        if self._suppress_next_move:
            self._suppress_next_move = False
            return
        if delta.isNull():
            return
        self._last_delta = delta
        self.moved.emit(self._region.id, delta, False)
        self._move_debounce.start()

    def _emit_final_move(self) -> None:
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H26",
            location="mirror_window.py:_emit_final_move:entry",
            message="emit_final_move_entered",
            data={
                "region_id": str(self._region.id),
                "geometry_dirty": bool(self._geometry_dirty),
                "last_delta_x": int(self._last_delta.x()),
                "last_delta_y": int(self._last_delta.y()),
            },
        )
        # endregion
        if self._geometry_dirty:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H24",
                location="mirror_window.py:_emit_final_move:flush_geometry",
                message="flushing_deferred_geometry_update",
                data={
                    "region_id": str(self._region.id),
                    "has_geometry": self._region.geometry is not None,
                },
            )
            # endregion
        self._persist_geometry(emit_signal=True)
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H26",
            location="mirror_window.py:_emit_final_move:after_persist",
            message="emit_final_move_after_persist",
            data={
                "region_id": str(self._region.id),
                "geometry_dirty": bool(self._geometry_dirty),
                "has_geometry": self._region.geometry is not None,
            },
        )
        # endregion
        self.moved.emit(self._region.id, self._last_delta, True)
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H26",
            location="mirror_window.py:_emit_final_move:after_moved_emit",
            message="emit_final_move_after_moved_emit",
            data={
                "region_id": str(self._region.id),
                "last_delta_x": int(self._last_delta.x()),
                "last_delta_y": int(self._last_delta.y()),
            },
        )
        # endregion

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
        # Resize can happen as part of a compositor-managed drag. Mirror the
        # move-path behavior and defer broadcasts while debounce is active.
        self._persist_geometry(emit_signal=not self._move_debounce.isActive())

    def _persist_geometry(self, *, emit_signal: bool) -> None:
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
            if current is None:
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H15",
                    location="mirror_window.py:_persist_geometry:first_save",
                    message="persisting_initial_geometry_for_region",
                    data={
                        "region_id": str(self._region.id),
                        "x": int(new_geo.x()),
                        "y": int(new_geo.y()),
                        "w": int(new_geo.width()),
                        "h": int(new_geo.height()),
                    },
                )
                # endregion
            self._region.geometry = new_geo
            self._geometry_dirty = True
            if emit_signal:
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H25",
                    location="mirror_window.py:_persist_geometry:before_emit_region_updated",
                    message="about_to_emit_region_updated",
                    data={
                        "region_id": str(self._region.id),
                        "x": int(new_geo.x()),
                        "y": int(new_geo.y()),
                        "w": int(new_geo.width()),
                        "h": int(new_geo.height()),
                        "is_visible": bool(self.isVisible()),
                    },
                )
                # endregion
                self._geometry_dirty = False
                self.region_updated.emit(self._region)
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H25",
                    location="mirror_window.py:_persist_geometry:after_emit_region_updated",
                    message="region_updated_emit_returned",
                    data={
                        "region_id": str(self._region.id),
                        "has_geometry": self._region.geometry is not None,
                    },
                )
                # endregion

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
