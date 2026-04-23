"""Modern hunt-stats HUD card.

Renders the solo Hunt Analyser payload that
:class:`~tvlinux.clipboard_watcher.ClipboardWatcher` publishes on the
bus. Between clipboard refreshes we extrapolate the session timer and
rate numbers with :mod:`tvlinux.stats_math` so the card feels alive
instead of frozen.

Visual language
---------------
Kept deliberately simple -- one card, five rows, one icon. Matches the
existing :class:`~tvlinux.hud_panels.audio_timer_panel.AudioTimerPanel`
look (frosted dark card, rounded 8px, tabular monospaced numbers) so
the HUD reads as a single overlay rather than a collage.

Layout per row::

    [icon]  LABEL                VALUE

- icon is a 12px SVG cached once to a QPixmap on panel construction.
- LABEL is uppercase caption grey.
- VALUE is right-aligned tabular mono, coloured per row:
    * PROFIT row is success green when >= 0 and danger red when < 0.
    * Everything else uses the primary text colour.

The panel collapses to zero size until the first
``HUNT_STATS_UPDATE`` lands so day-one users with the HUD on don't see
an empty frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..analyzers import AnalyzerHub, Event, EventKind
from ..hunt_parser import HuntSession
from ..smart_hud import Anchor, HudPanel
from ..stats_math import humanize_gp, live_extrapolate
from ..theme import TOKENS

# Panel geometry. Match AudioTimerPanel's 260 width so both anchor top
# corners share the same rail.
_PANEL_WIDTH = 270.0
_PADDING = 12.0
_HEADER_HEIGHT = 22.0
_ROW_HEIGHT = 22.0
_ICON_SIZE = 12


@dataclass
class _IconCache:
    """Cached QPixmaps for the SVG icons we render per frame."""

    coin_gold: QPixmap
    arrow_up: QPixmap
    sword: QPixmap
    heart: QPixmap


def _rasterize_svg(path: str, size: int) -> QPixmap:
    """Render ``path`` to an ``size x size`` :class:`QPixmap`.

    We pre-rasterize once at panel construction so the 60 fps paint loop
    never touches the SVG renderer -- blitting a pixmap is orders of
    magnitude cheaper.
    """
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(path)
    painter = QPainter(pix)
    try:
        renderer.render(painter, QRect(0, 0, size, size))
    finally:
        painter.end()
    return pix


def _asset_icon(name: str) -> str:
    """Return the filesystem path of a bundled asset icon."""
    return str(Path(__file__).parent.parent / "assets" / "icons" / name)


class HuntStatsPanel(HudPanel):
    """Live session timer + profit / XP / DPS / HPS row card."""

    id = "hunt_stats"
    anchor: Anchor = "top_right"

    def __init__(self, bus: AnalyzerHub) -> None:
        self._session: HuntSession | None = None
        self._session_ms_live: float = 0.0
        self._xp_per_h_live: int = 0
        self._profit_per_h_live: int = 0

        self._icons = _IconCache(
            coin_gold=_rasterize_svg(_asset_icon("coin_gold.svg"), _ICON_SIZE),
            arrow_up=_rasterize_svg(_asset_icon("arrow_up_green.svg"), _ICON_SIZE),
            sword=_rasterize_svg(_asset_icon("sword.svg"), _ICON_SIZE),
            heart=_rasterize_svg(_asset_icon("heart.svg"), _ICON_SIZE),
        )

        self._unsubscribe = bus.subscribe(EventKind.HUNT_STATS_UPDATE, self._on_update)

    # -- Introspection (tests) ------------------------------------------------

    @property
    def session(self) -> HuntSession | None:
        return self._session

    @property
    def session_ms_live(self) -> float:
        return self._session_ms_live

    # -- Bus handler ----------------------------------------------------------

    def _on_update(self, event: Event) -> None:
        data = dict(event.data)
        # ``event.data`` is the ``asdict(HuntSession)`` form -- coerce the
        # serialized ``session`` (seconds float) back into a ``timedelta``.
        session_value = data.get("session")
        if isinstance(session_value, timedelta):
            session_td = session_value
        else:
            # asdict serializes timedelta as the object itself (dict is
            # shallow), so in the common in-process path we keep the
            # timedelta. A JSON-round-tripped payload would serialize as
            # seconds -- support both for robustness.
            try:
                session_td = timedelta(seconds=float(session_value or 0.0))
            except (TypeError, ValueError):
                return
        try:
            self._session = HuntSession(
                session=session_td,
                raw_xp_gain=int(data["raw_xp_gain"]),
                xp_gain=int(data["xp_gain"]),
                raw_xp_per_h=int(data["raw_xp_per_h"]),
                xp_per_h=int(data["xp_per_h"]),
                loot=int(data["loot"]),
                supplies=int(data["supplies"]),
                balance=int(data["balance"]),
                damage=int(data["damage"]),
                damage_per_h=int(data["damage_per_h"]),
                healing=int(data["healing"]),
                healing_per_h=int(data["healing_per_h"]),
                captured_at=float(data.get("captured_at", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            return

    # -- HudPanel overrides ---------------------------------------------------

    def on_tick(self, dt_ms: float) -> None:
        del dt_ms
        if self._session is None:
            return
        # ``live_extrapolate`` reads wall-clock ``monotonic()`` itself; we
        # just refresh our cached values so paint() can render without
        # doing any math.
        (
            self._session_ms_live,
            self._xp_per_h_live,
            self._profit_per_h_live,
        ) = live_extrapolate(self._session)

    def preferred_size(self) -> QSizeF:
        if self._session is None:
            return QSizeF(0.0, 0.0)
        h = _PADDING * 2 + _HEADER_HEIGHT + _ROW_HEIGHT * 4
        return QSizeF(_PANEL_WIDTH, h)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        if self._session is None:
            return
        self._paint_card(painter, rect)
        self._paint_header(painter, rect)
        self._paint_rows(painter, rect)

    # -- Paint helpers --------------------------------------------------------

    def _paint_card(self, painter: QPainter, rect: QRectF) -> None:
        palette = TOKENS.palette
        bg = QColor(palette.bg_card)
        bg.setAlpha(210)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(palette.border_strong), 1.0))
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 10.0, 10.0)
        painter.drawPath(path)

    def _paint_header(self, painter: QPainter, rect: QRectF) -> None:
        palette = TOKENS.palette
        header_font = QFont(painter.font())
        header_font.setPointSize(10)
        header_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(header_font)

        label_rect = QRectF(
            rect.left() + _PADDING,
            rect.top() + _PADDING,
            rect.width() - _PADDING * 2,
            _HEADER_HEIGHT,
        )
        # Title on the left, live timer on the right. Using a single rect
        # with two alignments keeps everything baseline-aligned without
        # having to measure.
        painter.setPen(QColor(palette.text_secondary))
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            "SESSION",
        )

        timer_text = _format_duration_ms(self._session_ms_live)
        mono_font = QFont(painter.font())
        mono_font.setFamily("monospace")
        mono_font.setPointSize(12)
        mono_font.setWeight(QFont.Weight.Bold)
        painter.setFont(mono_font)
        painter.setPen(QColor(palette.text_primary))
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            timer_text,
        )

    def _paint_rows(self, painter: QPainter, rect: QRectF) -> None:
        assert self._session is not None  # narrowed by paint()
        palette = TOKENS.palette

        y = rect.top() + _PADDING + _HEADER_HEIGHT

        rows: list[tuple[QPixmap, str, str, QColor]] = [
            (
                self._icons.coin_gold,
                "PROFIT",
                ("+" if self._session.balance >= 0 else "") + humanize_gp(self._session.balance),
                QColor(palette.success) if self._session.balance >= 0 else QColor(palette.danger),
            ),
            (
                self._icons.arrow_up,
                "XP/h",
                humanize_gp(self._xp_per_h_live or self._session.xp_per_h),
                QColor(palette.text_primary),
            ),
            (
                self._icons.sword,
                "DAMAGE/h",
                humanize_gp(self._session.damage_per_h),
                QColor(palette.text_primary),
            ),
            (
                self._icons.heart,
                "HEALING/h",
                humanize_gp(self._session.healing_per_h),
                QColor(palette.text_primary),
            ),
        ]

        for icon, label, value, colour in rows:
            self._paint_row(painter, rect, y, icon, label, value, colour)
            y += _ROW_HEIGHT

    def _paint_row(
        self,
        painter: QPainter,
        rect: QRectF,
        y: float,
        icon: QPixmap,
        label: str,
        value: str,
        value_colour: QColor,
    ) -> None:
        palette = TOKENS.palette

        icon_y = y + (_ROW_HEIGHT - _ICON_SIZE) / 2.0
        target = QRect(
            int(rect.left() + _PADDING),
            int(icon_y),
            _ICON_SIZE,
            _ICON_SIZE,
        )
        painter.drawPixmap(target, icon, QRect(0, 0, icon.width(), icon.height()))

        # Label column. Starts right of the icon with a little gutter.
        label_font = QFont(painter.font())
        label_font.setPointSize(10)
        label_font.setWeight(QFont.Weight.Medium)
        painter.setFont(label_font)
        painter.setPen(QColor(palette.text_secondary))

        label_left = rect.left() + _PADDING + _ICON_SIZE + 8
        label_rect = QRectF(
            label_left,
            y,
            rect.width() - _PADDING - _ICON_SIZE - 8 - _PADDING,
            _ROW_HEIGHT,
        )
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            label,
        )

        value_font = QFont(painter.font())
        value_font.setFamily("monospace")
        value_font.setPointSize(11)
        value_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(value_font)
        painter.setPen(value_colour)
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            value,
        )

def _format_duration_ms(ms: float) -> str:
    """Render live-extrapolated ms as ``HH:MM:SS``.

    We keep seconds visible so users see the timer ticking frame-to-frame
    rather than having to wait a whole minute for the display to update.
    """
    total_seconds = max(0, int(ms // 1000))
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


__all__ = ["HuntStatsPanel"]
