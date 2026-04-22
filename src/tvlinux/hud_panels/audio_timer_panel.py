"""HUD panel that renders live audio-timer countdowns.

Connects directly to :class:`~tvlinux.audio_timers.AudioTimerManager` signals
so it works without any analyzer being enabled -- this is the "it does
something on day one" panel that validates the whole Smart HUD pipeline.

Each running timer gets a row:

    [name                  0:42] [======---------]

The bar fills from the right (remaining / duration) and shifts colour as
the remaining fraction drops:

- ``>= 50%`` - theme ``success`` green
- ``< 50%``  - theme ``warning`` amber
- ``< 10%``  - theme ``danger`` red + a brief post-fire flash

Non-running timers are omitted entirely. The panel shrinks to zero rows
when nothing is running and paints nothing, so it doesn't clutter the
screen idly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from PySide6.QtCore import QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from ..analyzers import Event
from ..audio_timers import AudioTimer, AudioTimerManager
from ..smart_hud import Anchor, HudPanel
from ..theme import TOKENS

_ROW_HEIGHT = 26.0
_ROW_GAP = 6.0
_PANEL_PADDING = 10.0
_PANEL_WIDTH = 260.0
_BAR_HEIGHT = 10.0
# How long after a timer fires we keep flashing its row. Long enough that a
# player glancing back to the HUD still catches it, short enough not to be
# annoying.
_FIRE_FLASH_MS = 900.0


@dataclass
class _Row:
    """One timer's live state inside the panel."""

    name: str
    duration_s: float
    remaining_s: float
    fire_flash_ms: float = 0.0  # countdown; > 0 means currently flashing


@dataclass
class _PanelState:
    """All of the panel's mutable state, collected for easy inspection in tests."""

    rows: dict[UUID, _Row] = field(default_factory=dict)


class AudioTimerPanel(HudPanel):
    """Shows a live countdown row per running audio timer."""

    id = "audio_timers"
    anchor: Anchor = "top_right"

    def __init__(self, manager: AudioTimerManager) -> None:
        self._manager = manager
        self._state = _PanelState()

        # Seed from current manager state so panels instantiated after
        # timers are already running show up immediately.
        for timer in manager.all():
            if manager.is_running(timer.id):
                self._state.rows[timer.id] = _Row(
                    name=timer.name,
                    duration_s=timer.duration_s,
                    remaining_s=manager.remaining(timer.id),
                )

        manager.countdown_tick.connect(self._on_tick_signal)
        manager.timer_fired.connect(self._on_timer_fired)
        manager.timer_removed.connect(self._on_timer_removed)
        manager.timer_changed.connect(self._on_timer_changed)

    # -- Introspection hooks (used in tests) -----------------------------------

    @property
    def state(self) -> _PanelState:
        return self._state

    # -- Signal handlers -------------------------------------------------------

    def _on_tick_signal(self, tid: UUID, remaining_s: float) -> None:
        row = self._state.rows.get(tid)
        if row is None:
            # First tick for a freshly-started timer: look up its metadata.
            timer = next(
                (t for t in self._manager.all() if t.id == tid),
                None,
            )
            if timer is None:
                return
            row = _Row(
                name=timer.name,
                duration_s=timer.duration_s,
                remaining_s=remaining_s,
            )
            self._state.rows[tid] = row
        else:
            row.remaining_s = max(0.0, remaining_s)

    def _on_timer_fired(self, tid: UUID) -> None:
        row = self._state.rows.get(tid)
        if row is not None:
            row.remaining_s = 0.0
            row.fire_flash_ms = _FIRE_FLASH_MS

    def _on_timer_removed(self, tid: UUID) -> None:
        self._state.rows.pop(tid, None)

    def _on_timer_changed(self, timer: AudioTimer) -> None:
        row = self._state.rows.get(timer.id)
        if row is None:
            return
        row.name = timer.name
        row.duration_s = timer.duration_s

    # -- HudPanel overrides ----------------------------------------------------

    def on_event(self, event: Event) -> None:
        # Audio timers are driven off the AudioTimerManager signals, not the
        # event bus. Future: if analyzers ever publish TIMER_START events we
        # could react here. For now, deliberately a no-op.
        del event

    def on_tick(self, dt_ms: float) -> None:
        # Drain fire-flash countdowns + drop finished rows. Using the HUD's
        # dt rather than wall-clock so tests can drive animations
        # deterministically.
        finished: list[UUID] = []
        for tid, row in self._state.rows.items():
            if row.fire_flash_ms > 0.0:
                row.fire_flash_ms = max(0.0, row.fire_flash_ms - dt_ms)
                if row.fire_flash_ms == 0.0 and row.remaining_s <= 0.0:
                    finished.append(tid)
        for tid in finished:
            self._state.rows.pop(tid, None)

    def preferred_size(self) -> QSizeF:
        # Panel grows with the number of active rows. A zero-row panel
        # collapses to a tiny size and paints nothing.
        n = max(1, len(self._state.rows))
        h = _PANEL_PADDING * 2 + n * _ROW_HEIGHT + max(0, n - 1) * _ROW_GAP
        return QSizeF(_PANEL_WIDTH, h)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        if not self._state.rows:
            return

        palette = TOKENS.palette

        # Frosted background card so the bars read cleanly over any game UI.
        bg = QColor(palette.bg_card)
        bg.setAlpha(200)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(palette.border_strong), 1.0))
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 10.0, 10.0)
        painter.drawPath(path)

        font = painter.font()
        font.setPointSize(10)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        fm = QFontMetricsF(font)

        y = rect.top() + _PANEL_PADDING
        for row in self._state.rows.values():
            self._paint_row(painter, rect, y, row, fm)
            y += _ROW_HEIGHT + _ROW_GAP

    # -- Row painting ----------------------------------------------------------

    def _paint_row(
        self,
        painter: QPainter,
        rect: QRectF,
        y: float,
        row: _Row,
        fm: QFontMetricsF,
    ) -> None:
        palette = TOKENS.palette
        fraction = self._fraction(row)
        colour = self._row_colour(row, fraction)

        left = rect.left() + _PANEL_PADDING
        right = rect.right() - _PANEL_PADDING

        # Row label: "Food          0:42"
        label_rect = QRectF(left, y, right - left, _ROW_HEIGHT - _BAR_HEIGHT - 2.0)
        painter.setPen(QColor(palette.text_primary))
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            row.name,
        )
        remaining_text = _format_remaining(row.remaining_s)
        rt_width = fm.horizontalAdvance(remaining_text)
        painter.setPen(QColor(palette.text_secondary))
        painter.drawText(
            QRectF(right - rt_width, y, rt_width, label_rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            remaining_text,
        )

        # Progress bar track.
        bar_y = y + label_rect.height() + 2.0
        track = QRectF(left, bar_y, right - left, _BAR_HEIGHT)
        track_colour = QColor(palette.bg_elevated)
        track_colour.setAlpha(220)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_colour)
        painter.drawRoundedRect(track, 4.0, 4.0)

        # Progress bar fill.
        fill_w = track.width() * fraction
        if fill_w > 0.0:
            fill = QRectF(track.left(), track.top(), fill_w, track.height())
            painter.setBrush(colour)
            painter.drawRoundedRect(fill, 4.0, 4.0)

    @staticmethod
    def _fraction(row: _Row) -> float:
        if row.duration_s <= 0.0:
            return 0.0
        return max(0.0, min(1.0, row.remaining_s / row.duration_s))

    @staticmethod
    def _row_colour(row: _Row, fraction: float) -> QColor:
        palette = TOKENS.palette
        if row.fire_flash_ms > 0.0:
            # Pulse the fire-flash from bright danger back to baseline. The
            # squared envelope keeps the flash punchy at the start and
            # tapers smoothly rather than linearly.
            t = row.fire_flash_ms / _FIRE_FLASH_MS
            c = QColor(palette.danger)
            c.setAlphaF(0.6 + 0.4 * t * t)
            return c
        if fraction < 0.10:
            return QColor(palette.danger)
        if fraction < 0.50:
            return QColor(palette.warning)
        return QColor(palette.success)


def _format_remaining(seconds: float) -> str:
    """Format ``seconds`` as ``M:SS`` (no leading zero on minutes)."""
    total = int(max(0.0, seconds))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


__all__ = ["AudioTimerPanel"]
