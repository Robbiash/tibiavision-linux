"""Party Hunt HUD card.

Consumes :data:`~tvlinux.analyzers.base.EventKind.PARTY_HUNT_UPDATE`
events that :class:`~tvlinux.clipboard_watcher.ClipboardWatcher` emits
when the user right-clicks the in-game Party Hunt widget and picks
"Copy to clipboard".

Rendered as a dark frosted card with a header showing the member count
and total balance, then one row per member sorted by balance
descending (top earner up top). Per-row colour shows balance polarity
at a glance: green for net-positive, red for net-negative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..analyzers import AnalyzerHub, Event, EventKind
from ..hunt_parser import PartyHuntSession, PartyMember
from ..smart_hud import Anchor, HudPanel
from ..stats_math import humanize_gp, party_count
from ..theme import TOKENS

_PANEL_WIDTH = 290.0
_PADDING = 12.0
_HEADER_HEIGHT = 22.0
_ROW_HEIGHT = 20.0
_ICON_SIZE = 10
_MAX_ROWS = 8


@dataclass
class _IconCache:
    arrow_up: QPixmap
    arrow_down: QPixmap


def _rasterize_svg(path: str, size: int) -> QPixmap:
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
    return str(Path(__file__).parent.parent / "assets" / "icons" / name)


class PartyPanel(HudPanel):
    """Compact party-hunt scoreboard card."""

    id = "party_hunt"
    anchor: Anchor = "top_right"

    def __init__(self, bus: AnalyzerHub) -> None:
        self._session: PartyHuntSession | None = None
        self._icons = _IconCache(
            arrow_up=_rasterize_svg(_asset_icon("arrow_up_green.svg"), _ICON_SIZE),
            arrow_down=_rasterize_svg(_asset_icon("arrow_down_red.svg"), _ICON_SIZE),
        )
        self._unsubscribe = bus.subscribe(EventKind.PARTY_HUNT_UPDATE, self._on_update)

    # -- Introspection (tests) ------------------------------------------------

    @property
    def session(self) -> PartyHuntSession | None:
        return self._session

    @property
    def members_sorted(self) -> list[PartyMember]:
        if self._session is None:
            return []
        return sorted(self._session.members, key=lambda m: m.balance, reverse=True)

    # -- Bus handler ----------------------------------------------------------

    def _on_update(self, event: Event) -> None:
        data = dict(event.data)
        try:
            session_value = data.get("session")
            session_td = (
                session_value
                if isinstance(session_value, timedelta)
                else timedelta(seconds=float(session_value or 0.0))
            )
            # ``asdict`` recurses into member dataclasses, so rebuild.
            raw_members = data.get("members") or []
            members = [
                PartyMember(
                    name=str(m["name"]),
                    loot=int(m["loot"]),
                    supplies=int(m["supplies"]),
                    balance=int(m["balance"]),
                    damage=_optional_int(m.get("damage")),
                    healing=_optional_int(m.get("healing")),
                )
                for m in raw_members
                if isinstance(m, dict) and "name" in m
            ]
            self._session = PartyHuntSession(
                session=session_td,
                loot_type=str(data.get("loot_type", "")),
                loot=int(data["loot"]),
                supplies=int(data["supplies"]),
                balance=int(data["balance"]),
                members=members,
                captured_at=float(data.get("captured_at", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            return

    # -- HudPanel overrides ---------------------------------------------------

    def preferred_size(self) -> QSizeF:
        if self._session is None:
            return QSizeF(0.0, 0.0)
        row_count = min(_MAX_ROWS, max(1, party_count(self._session)))
        h = _PADDING * 2 + _HEADER_HEIGHT + row_count * _ROW_HEIGHT
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
        assert self._session is not None
        palette = TOKENS.palette

        caption_font = QFont(painter.font())
        caption_font.setPointSize(10)
        caption_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(caption_font)
        painter.setPen(QColor(palette.text_secondary))

        count = party_count(self._session)
        label_rect = QRectF(
            rect.left() + _PADDING,
            rect.top() + _PADDING,
            rect.width() - _PADDING * 2,
            _HEADER_HEIGHT,
        )
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"PARTY ({count})",
        )

        total_font = QFont(painter.font())
        total_font.setFamily("monospace")
        total_font.setPointSize(11)
        total_font.setWeight(QFont.Weight.Bold)
        painter.setFont(total_font)
        total_colour = (
            QColor(palette.success) if self._session.balance >= 0 else QColor(palette.danger)
        )
        painter.setPen(total_colour)
        sign = "+" if self._session.balance >= 0 else ""
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"{sign}{humanize_gp(self._session.balance)} gp",
        )

    def _paint_rows(self, painter: QPainter, rect: QRectF) -> None:
        palette = TOKENS.palette
        members = self.members_sorted[:_MAX_ROWS]

        name_font = QFont(painter.font())
        name_font.setPointSize(10)
        name_font.setWeight(QFont.Weight.Medium)

        value_font = QFont(painter.font())
        value_font.setFamily("monospace")
        value_font.setPointSize(10)
        value_font.setWeight(QFont.Weight.DemiBold)

        fm_name = QFontMetricsF(name_font)

        y = rect.top() + _PADDING + _HEADER_HEIGHT
        for idx, member in enumerate(members):
            colour = QColor(palette.success) if member.balance >= 0 else QColor(palette.danger)
            icon = self._icons.arrow_up if member.balance >= 0 else self._icons.arrow_down

            # Icon.
            icon_y = y + (_ROW_HEIGHT - _ICON_SIZE) / 2.0
            painter.drawPixmap(
                QRect(int(rect.left() + _PADDING), int(icon_y), _ICON_SIZE, _ICON_SIZE),
                icon,
                QRect(0, 0, icon.width(), icon.height()),
            )

            # Name. Top earner (idx 0) gets full-brightness white; others
            # slightly dimmed so the eye naturally lands on the MVP row.
            painter.setFont(name_font)
            name_colour = (
                QColor(palette.text_primary) if idx == 0 else QColor(palette.text_secondary)
            )
            painter.setPen(name_colour)

            name_left = rect.left() + _PADDING + _ICON_SIZE + 8
            value_width = 110.0
            name_width = rect.right() - _PADDING - value_width - name_left
            name_rect = QRectF(name_left, y, name_width, _ROW_HEIGHT)
            name = fm_name.elidedText(member.name, Qt.TextElideMode.ElideRight, name_rect.width())
            painter.drawText(
                name_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                name,
            )

            painter.setFont(value_font)
            painter.setPen(colour)
            value_rect = QRectF(
                rect.right() - _PADDING - value_width,
                y,
                value_width,
                _ROW_HEIGHT,
            )
            sign = "+" if member.balance >= 0 else ""
            painter.drawText(
                value_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{sign}{humanize_gp(member.balance)}",
            )

            y += _ROW_HEIGHT


def _optional_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, int | float | str):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return None


__all__ = ["PartyPanel"]
