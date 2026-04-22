"""HUD panel that visualises auto-attack pacing ("The Metronome").

Listens for :data:`EventKind.SWING_TIMER_RESET` from
:class:`~tvlinux.analyzers.swing_timer.SwingTimerAnalyzer`. On each reset:

1. Record the timestamp.
2. Start a bright flash that decays over ``_FLASH_MS``.
3. From now until ``expected_interval_ms`` has elapsed, paint a clockwise
   arc that sweeps 0 -> 360 degrees -- a visual countdown to the next
   expected swing window.
4. After the expected interval, the arc holds at 360 and the colour mutes
   so the player knows "free to swing now".

While ``swing_timer.py`` is still a stub, the panel sits in an idle
"waiting..." state. That's the whole point of the HudPanel contract -- the
HUD renders what it has; filling in the analyzer later "just works".
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSizeF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen

from ..analyzers import Event, EventKind
from ..smart_hud import Anchor, HudPanel
from ..theme import TOKENS

_DEFAULT_INTERVAL_MS = 2000.0  # Tibia melee swing cadence
_FLASH_MS = 250.0
_IDLE_GRACE_MS = 5000.0  # after this long with no reset, treat as idle

_PANEL_DIAMETER = 140.0
_STROKE_WIDTH = 10.0


class MetronomePanel(HudPanel):
    """Circular arc that paces against auto-attack swing resets."""

    id = "metronome"
    anchor: Anchor = "bottom_right"

    def __init__(
        self,
        *,
        expected_interval_ms: float = _DEFAULT_INTERVAL_MS,
    ) -> None:
        self._expected_interval_ms = expected_interval_ms
        # Milliseconds since the last reset. ``math.inf`` until we see the
        # first event, which keeps us in the idle "waiting..." state.
        self._since_reset_ms: float = math.inf
        # Flash decay timer. > 0 while we're still flashing from a reset.
        self._flash_ms: float = 0.0
        # Event count -- handy for tests that want to assert the panel saw
        # an event without having to peek at float timestamps.
        self._reset_count: int = 0

    # -- Introspection hooks (tests) ------------------------------------------

    @property
    def since_reset_ms(self) -> float:
        return self._since_reset_ms

    @property
    def reset_count(self) -> int:
        return self._reset_count

    @property
    def flash_ms(self) -> float:
        return self._flash_ms

    # -- HudPanel overrides ---------------------------------------------------

    def on_event(self, event: Event) -> None:
        if event.kind != EventKind.SWING_TIMER_RESET:
            return
        self._since_reset_ms = 0.0
        self._flash_ms = _FLASH_MS
        self._reset_count += 1

    def on_tick(self, dt_ms: float) -> None:
        # Advance both clocks by the HUD's dt. Using dt (not wall-clock)
        # keeps animations synced to the paint loop and makes tests
        # deterministic.
        if math.isfinite(self._since_reset_ms):
            self._since_reset_ms += dt_ms
        if self._flash_ms > 0.0:
            self._flash_ms = max(0.0, self._flash_ms - dt_ms)

    def preferred_size(self) -> QSizeF:
        return QSizeF(_PANEL_DIAMETER, _PANEL_DIAMETER)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        palette = TOKENS.palette
        centre = rect.center()
        radius = min(rect.width(), rect.height()) / 2.0 - _STROKE_WIDTH
        arc_rect = QRectF(
            centre.x() - radius,
            centre.y() - radius,
            radius * 2.0,
            radius * 2.0,
        )

        # Backing ring: shows the dial even when idle / early in the window.
        track = QColor(palette.bg_elevated)
        track.setAlpha(200)
        painter.setPen(QPen(track, _STROKE_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(arc_rect)

        if not math.isfinite(self._since_reset_ms) or self._since_reset_ms > _IDLE_GRACE_MS:
            # Idle / waiting state: paint a muted centre label, no arc.
            self._paint_label(painter, rect, "waiting", QColor(palette.text_muted))
            return

        progress = min(1.0, self._since_reset_ms / self._expected_interval_ms)

        # Arc colour fades from accent cyan to danger as we exit the
        # expected window. Past 1.0 we're "free to swing".
        arc_colour = self._arc_colour(progress)
        if self._flash_ms > 0.0:
            # Brighten during the post-reset flash by shifting alpha up.
            flash_t = self._flash_ms / _FLASH_MS
            arc_colour = QColor(arc_colour)
            arc_colour.setAlphaF(min(1.0, 0.7 + 0.3 * flash_t))

        pen = QPen(arc_colour, _STROKE_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Qt's drawArc uses 1/16 of a degree units; negative sweep = clockwise.
        start_angle = 90 * 16  # 12 o'clock
        sweep_angle = int(-progress * 360 * 16)
        painter.drawArc(arc_rect, start_angle, sweep_angle)

        # Central label: remaining seconds until the expected swing window
        # re-opens, or "ready" once we're past it.
        ms_left = self._expected_interval_ms - self._since_reset_ms
        if ms_left <= 0:
            label = "ready"
            label_colour = QColor(palette.success)
        else:
            label = f"{ms_left / 1000.0:.1f}s"
            label_colour = QColor(palette.text_primary)
        self._paint_label(painter, rect, label, label_colour)

    # -- Helpers ---------------------------------------------------------------

    def _paint_label(self, painter: QPainter, rect: QRectF, text: str, colour: QColor) -> None:
        font = QFont(painter.font())
        font.setPointSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(colour)
        painter.setBrush(QBrush())
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

    def _arc_colour(self, progress: float) -> QColor:
        """Interpolate accent -> warning -> danger across ``progress`` in [0, 1]."""
        palette = TOKENS.palette
        # Clamp for safety -- we might feed > 1 one frame if dt is bursty.
        t = max(0.0, min(1.0, progress))
        if t < 0.5:
            return _lerp_colour(QColor(palette.accent), QColor(palette.warning), t * 2.0)
        return _lerp_colour(QColor(palette.warning), QColor(palette.danger), (t - 0.5) * 2.0)


def _lerp_colour(a: QColor, b: QColor, t: float) -> QColor:
    """Straight-line blend between two ``QColor``s in RGB space."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


__all__ = ["MetronomePanel"]
