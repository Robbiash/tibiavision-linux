"""Smart HUD: a strictly click-through, always-on-top overlay.

The HUD is a single full-screen frameless transparent window that renders
pluggable :class:`HudPanel` instances on top of everything else. Every
mouse event passes through to the app underneath (``WA_TransparentForMouseEvents``)
so the overlay is purely visual -- you can never click it, accidentally
drag it, or have it steal focus from the game.

Panels are the extension point. A new feature -- resource arcs, an expiry
flasher, a profit ticker -- is a single new file that subclasses
:class:`HudPanel`, plus one ``register_panel(...)`` line in
:mod:`tvlinux.app`. The HUD itself never needs to change.

Rendering model
---------------
- A ``QTimer`` fires at ~60 fps. Each tick calls ``on_tick(dt_ms)`` on
  every panel (animation step) then schedules a single ``update()`` for the
  whole window. Panels never own their own timers -- one timer for the HUD
  keeps frame pacing consistent and avoids dozens of QTimer instances.
- ``paintEvent`` iterates registered panels in anchor-layout order, clips
  the painter to the panel's rect, and calls ``panel.paint(painter, rect)``.
  Panels paint in their own local coordinate space (0,0 at top-left of the
  panel rect), so they don't need to know where they live on screen.

Event routing
-------------
On construction the HUD subscribes once to ``bus.subscribe_all`` and fans
every event out to every panel's ``on_event``. Panels that don't care
about a given event kind simply ignore it. This keeps the bus subscriber
count at O(1) regardless of how many panels are registered.

Layout
------
Panels declare an anchor (``top_left``, ``top_right``, ``bottom_left``,
``bottom_right``). On relayout we stack panels sharing an anchor toward the
centre of the screen. User overrides are persisted per-panel-id to
``hud_layout.json`` so a future drag-to-reposition UI can restore exact
positions.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from .analyzers import AnalyzerHub, Event
from .logging_config import get_logger
from .paths import hud_layout_path

log = get_logger(__name__)

Anchor = Literal["top_left", "top_right", "bottom_left", "bottom_right"]

# ~60 fps. The HUD is cheap to paint (a handful of panels worth of vector
# ops) so we can afford the full-rate repaint without measurable cost.
_FRAME_INTERVAL_MS = 16
# Breathing room between the window edge and the first panel, plus between
# stacked panels on the same anchor.
_EDGE_MARGIN = 16
_PANEL_GAP = 12


class HudPanel(ABC):
    """Abstract base for HUD widgets.

    Subclass contract:

    * :attr:`id` -- stable string; used as the key in ``hud_layout.json``
      and for :meth:`SmartHud.panel`. Must be unique per HUD instance.
    * :attr:`anchor` -- default corner if no override exists on disk.
    * :meth:`preferred_size` -- returns the panel's natural size in pixels.
      The HUD uses this for default layout.
    * :meth:`paint` -- draws the panel. The ``QPainter`` is already
      translated so (0, 0) is the top-left of the panel's rect, and clipped
      to that rect -- panels cannot accidentally bleed into their
      neighbours.
    * :meth:`on_event` / :meth:`on_tick` -- default to no-ops so trivial
      panels don't have to implement them.
    """

    id: str = "abstract"
    anchor: Anchor = "top_left"

    @abstractmethod
    def preferred_size(self) -> QSizeF: ...

    @abstractmethod
    def paint(self, painter: QPainter, rect: QRectF) -> None: ...

    # Intentionally not abstract: most panels only care about either
    # events or ticks, not both. A concrete default keeps trivial panels
    # terse while the two ``@abstractmethod``s above still force subclasses
    # to declare their size and paint behavior.
    def on_event(self, event: Event) -> None:  # noqa: B027
        """React to a bus event. Default: ignore."""

    def on_tick(self, dt_ms: float) -> None:  # noqa: B027
        """Animation step (called every frame). Default: no-op."""


@dataclass
class _PanelSlot:
    """Runtime state paired with a registered :class:`HudPanel`."""

    panel: HudPanel
    rect: QRectF


class SmartHud(QWidget):
    """Full-screen, click-through, always-on-top container for HUD panels.

    :param bus: the application's :class:`EventBus` (alias of
        :class:`AnalyzerHub`). The HUD subscribes once with
        :meth:`AnalyzerHub.subscribe_all` and fans events out to every
        panel.
    :param layout_path: override for the per-panel layout JSON (testing).
    """

    def __init__(
        self,
        *,
        bus: AnalyzerHub,
        parent: QWidget | None = None,
        layout_path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus
        self._layout_path = layout_path or hud_layout_path()
        self._slots: dict[str, _PanelSlot] = {}
        self._unsubscribe: Callable[[], None] | None = None

        # Strictly click-through, always-on-top, no taskbar entry.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # See mirror_window.py for why this matters on NVIDIA + Wayland.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        # Belt-and-braces in case the window flag is not honoured by the WM.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)

        self._resize_to_screen()

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(_FRAME_INTERVAL_MS)
        self._last_tick_ts = time.monotonic()
        self._frame_timer.timeout.connect(self._on_frame)

        self._bus_sub_installed = False

    # -- Screen sizing ---------------------------------------------------------

    def _resize_to_screen(self) -> None:
        # Span the primary screen. Multi-monitor support is a future panel
        # concern -- one SmartHud per screen is the scalable answer there.
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.geometry()
        self.setGeometry(geom)

    # -- Public API ------------------------------------------------------------

    def register_panel(self, panel: HudPanel) -> None:
        """Install ``panel`` on the HUD. Idempotent by ``panel.id``."""
        if panel.id in self._slots:
            log.warning("hud.panel.duplicate", id=panel.id)
            return
        self._slots[panel.id] = _PanelSlot(panel=panel, rect=QRectF())
        self._relayout()
        log.info("hud.panel.registered", id=panel.id, anchor=panel.anchor)

    def unregister_panel(self, panel_id: str) -> None:
        if self._slots.pop(panel_id, None) is not None:
            self._relayout()

    def panel(self, panel_id: str) -> HudPanel | None:
        slot = self._slots.get(panel_id)
        return slot.panel if slot is not None else None

    def panels(self) -> list[HudPanel]:
        return [s.panel for s in self._slots.values()]

    # -- Bus subscription ------------------------------------------------------

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        self._ensure_bus_subscribed()
        self._frame_timer.start()

    def hideEvent(self, event):  # type: ignore[override]
        self._frame_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):  # type: ignore[override]
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
            self._bus_sub_installed = False
        super().closeEvent(event)

    def _ensure_bus_subscribed(self) -> None:
        if self._bus_sub_installed:
            return
        self._unsubscribe = self._bus.subscribe_all(self._on_bus_event)
        self._bus_sub_installed = True

    def _on_bus_event(self, event: Event) -> None:
        # Fan out to every panel; each decides whether it cares. Cheap:
        # panels we ship early-return on unknown kinds.
        for slot in self._slots.values():
            try:
                slot.panel.on_event(event)
            except Exception:  # pragma: no cover - never let one panel kill the HUD
                log.exception("hud.panel.on_event_failed", id=slot.panel.id)

    # -- Frame loop ------------------------------------------------------------

    def _on_frame(self) -> None:
        now = time.monotonic()
        dt_ms = (now - self._last_tick_ts) * 1000.0
        self._last_tick_ts = now
        for slot in self._slots.values():
            try:
                slot.panel.on_tick(dt_ms)
            except Exception:  # pragma: no cover
                log.exception("hud.panel.on_tick_failed", id=slot.panel.id)
        self.update()

    # -- Layout + paint --------------------------------------------------------

    def _relayout(self) -> None:
        """Compute each panel's rect from its anchor + preferred size.

        Panels sharing an anchor stack *inward* from the corner: top anchors
        stack downward, bottom anchors upward. This keeps the screen's
        centre uncluttered by default.
        """
        overrides = self._load_layout_overrides()

        # Group panel ids by anchor, preserving registration order so the
        # stacking is predictable.
        by_anchor: dict[Anchor, list[str]] = {
            "top_left": [],
            "top_right": [],
            "bottom_left": [],
            "bottom_right": [],
        }
        for pid, slot in self._slots.items():
            by_anchor[slot.panel.anchor].append(pid)

        geom = self.geometry()
        w = float(geom.width())
        h = float(geom.height())

        for anchor, ids in by_anchor.items():
            cursor = float(_EDGE_MARGIN)
            for pid in ids:
                slot = self._slots[pid]
                size = slot.panel.preferred_size()
                if pid in overrides:
                    pos = overrides[pid]
                    slot.rect = QRectF(pos, size)
                    continue
                if anchor == "top_left":
                    slot.rect = QRectF(QPointF(_EDGE_MARGIN, cursor), size)
                elif anchor == "top_right":
                    slot.rect = QRectF(QPointF(w - _EDGE_MARGIN - size.width(), cursor), size)
                elif anchor == "bottom_left":
                    y = h - _EDGE_MARGIN - size.height() - cursor + _EDGE_MARGIN
                    slot.rect = QRectF(QPointF(_EDGE_MARGIN, y), size)
                else:  # bottom_right
                    y = h - _EDGE_MARGIN - size.height() - cursor + _EDGE_MARGIN
                    slot.rect = QRectF(QPointF(w - _EDGE_MARGIN - size.width(), y), size)
                cursor += size.height() + _PANEL_GAP

    def _load_layout_overrides(self) -> dict[str, QPointF]:
        """Read the on-disk per-panel position overrides. Missing -> {}."""
        if not self._layout_path.exists():
            return {}
        try:
            data = json.loads(self._layout_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("hud.layout.load_failed", error=str(e))
            return {}
        overrides: dict[str, QPointF] = {}
        for pid, pos in (data.get("positions") or {}).items():
            try:
                overrides[str(pid)] = QPointF(float(pos["x"]), float(pos["y"]))
            except (TypeError, KeyError, ValueError):
                continue
        return overrides

    def save_layout(self) -> None:
        """Persist current panel positions. Used by :class:`HudLayoutEditor`."""
        payload = {
            "version": 1,
            "positions": {
                pid: {"x": slot.rect.x(), "y": slot.rect.y()} for pid, slot in self._slots.items()
            },
        }
        self._layout_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._layout_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._layout_path)

    def apply_layout_overrides(self, positions: dict[str, QPointF]) -> None:
        """Move each known panel's slot rect to ``positions[panel_id]``.

        Called by the :class:`HudLayoutEditor` companion window after
        the user drags tiles into place. Sizes are preserved -- only
        the rect's top-left is updated -- so panels keep their
        natural footprint.
        """
        for pid, pos in positions.items():
            slot = self._slots.get(pid)
            if slot is None:
                continue
            size = slot.panel.preferred_size()
            slot.rect = QRectF(QPointF(float(pos.x()), float(pos.y())), size)
        self.update()

    def _slots_clear_overrides(self) -> None:
        """Delete the override file and rebuild default anchor layout.

        Exposed for the layout editor's "Reset" button. Named with a
        leading underscore to flag it as editor plumbing rather than
        part of the public HUD API.
        """
        try:
            if self._layout_path.exists():
                self._layout_path.unlink()
        except OSError as e:
            log.warning("hud.layout.reset_failed", error=str(e))
        self._relayout()

    def open_layout_editor(self, parent: QWidget | None = None) -> QWidget:
        """Open (or focus) the companion :class:`HudLayoutEditor` window.

        Safe to call multiple times: a second call while the editor is
        open simply brings the existing window to the front instead of
        spawning a duplicate.
        """
        # Local import avoids a cycle with hud_layout_editor importing
        # SmartHud for type-checking only.
        from .hud_layout_editor import HudLayoutEditor

        existing = getattr(self, "_layout_editor", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return existing

        editor = HudLayoutEditor(self, parent=parent)
        editor.destroyed.connect(lambda *_: setattr(self, "_layout_editor", None))
        self._layout_editor = editor
        editor.show()
        return editor

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            for slot in self._slots.values():
                # Each panel paints in its own coordinate space, clipped to
                # its rect. Save/restore so one panel's pen/brush/transform
                # can't leak into the next.
                painter.save()
                try:
                    painter.setClipRect(slot.rect)
                    painter.translate(slot.rect.topLeft())
                    local = QRectF(0.0, 0.0, slot.rect.width(), slot.rect.height())
                    slot.panel.paint(painter, local)
                except Exception:  # pragma: no cover
                    log.exception("hud.panel.paint_failed", id=slot.panel.id)
                finally:
                    painter.restore()
        finally:
            painter.end()
