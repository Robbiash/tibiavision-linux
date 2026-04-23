"""Hotbar cheat-sheet HUD panel.

Reads Tibia's own ``clientoptions.json`` via
:mod:`tvlinux.tibia_data` and renders the active preset's keybindings
as a compact table. Reacts to :data:`EventKind.LOGIN_DETECTED` (fired by
:class:`~tvlinux.analyzers.preset_watcher.PresetWatcher`) so the panel
auto-refreshes the moment the user switches characters.

Why this exists
---------------
Players (especially alt-hoppers) forget which key does what on a fresh
character. Rather than make the user key those in manually, we lift
them straight from Tibia's config. Zero duplicated state, zero OCR.

Panel layout
------------
Two tightly-packed columns of ``key  label`` rows, sorted by the natural
hotkey ordering (F-keys first, then digits, then the rest). Capped at
``_MAX_ROWS`` rows so a preset with 60 obscure bindings doesn't eat the
whole screen -- the first page is enough to jog memory for the usual 12
combat keys.

Empty-preset handling
---------------------
If we can't read ``clientoptions.json`` (Tibia not installed, permission
error, or no active preset) the panel collapses to zero height and
paints nothing, matching the other HudPanels' "invisible when irrelevant"
contract.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from ..analyzers import AnalyzerHub, Event, EventKind
from ..logging_config import get_logger
from ..smart_hud import Anchor, HudPanel
from ..theme import TOKENS
from ..tibia_data import (
    HotkeyBinding,
    current_preset_name,
    iter_hotkey_bindings,
    read_client_options,
)

log = get_logger(__name__)

_PANEL_WIDTH = 300.0
_PADDING = 10.0
_ROW_HEIGHT = 20.0
_HEADER_HEIGHT = 22.0
_KEY_COLUMN = 90.0  # reserved width for the keysequence string
_MAX_ROWS = 14

# Sort weight per keysequence family; lower sorts first. Anything that
# doesn't match drops to the end in string order.
_KEY_GROUPS: list[tuple[str, int]] = [
    ("F", 0),
    ("0", 10),
    ("1", 10),
    ("2", 10),
    ("3", 10),
    ("4", 10),
    ("5", 10),
    ("6", 10),
    ("7", 10),
    ("8", 10),
    ("9", 10),
]


class HotbarPanel(HudPanel):
    """Renders the active Tibia hotkey preset as a HUD cheat-sheet."""

    id = "hotbar"
    anchor: Anchor = "bottom_left"

    def __init__(self, bus: AnalyzerHub) -> None:
        self._bus = bus
        self._preset_name: str | None = None
        self._rows: list[HotkeyBinding] = []
        # Subscribe once; unsubscribe handle kept so tests / future
        # "destroy panel" flows can clean up.
        self._unsubscribe = bus.subscribe(EventKind.LOGIN_DETECTED, self._on_login)
        self.refresh()

    # -- Public API ------------------------------------------------------------

    @property
    def preset_name(self) -> str | None:
        return self._preset_name

    @property
    def rows(self) -> list[HotkeyBinding]:
        return list(self._rows)

    def refresh(self, preset_name: str | None = None) -> None:
        """Re-read ``clientoptions.json`` and rebuild the row list.

        ``preset_name`` overrides the detected active preset (useful for
        testing "show me this other preset").
        """
        options = read_client_options()
        if options is None:
            self._preset_name = None
            self._rows = []
            return
        self._preset_name = preset_name or current_preset_name(options)
        rows = list(iter_hotkey_bindings(options, self._preset_name))
        self._rows = self._sort_rows(rows)[:_MAX_ROWS]

    def set_rows(self, preset_name: str | None, rows: Iterable[HotkeyBinding]) -> None:
        """Testing hook: set rows directly without touching the filesystem."""
        self._preset_name = preset_name
        self._rows = list(rows)[:_MAX_ROWS]

    # -- Subscription handler --------------------------------------------------

    def _on_login(self, event: Event) -> None:
        del event  # signal only; we re-read regardless of payload
        self.refresh()

    # -- HudPanel overrides ----------------------------------------------------

    def preferred_size(self) -> QSizeF:
        if not self._rows:
            return QSizeF(0.0, 0.0)
        h = _PADDING * 2 + _HEADER_HEIGHT + len(self._rows) * _ROW_HEIGHT
        return QSizeF(_PANEL_WIDTH, h)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        if not self._rows:
            return

        palette = TOKENS.palette

        # Frosted card background, same language as AudioTimerPanel so the
        # HUD feels like one unified overlay.
        bg = QColor(palette.bg_card)
        bg.setAlpha(210)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(palette.border_strong), 1.0))
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 10.0, 10.0)
        painter.drawPath(path)

        # Header line: preset name in accent colour so character switches
        # are immediately visible on screen.
        header_font = QFont(painter.font())
        header_font.setPointSize(10)
        header_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(header_font)
        painter.setPen(QColor(palette.accent))
        header_rect = QRectF(
            rect.left() + _PADDING,
            rect.top() + _PADDING,
            rect.width() - _PADDING * 2,
            _HEADER_HEIGHT,
        )
        painter.drawText(
            header_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"HOTKEYS  {self._preset_name or ''}".rstrip(),
        )

        # Rows.
        row_font = QFont(painter.font())
        row_font.setPointSize(10)
        row_font.setWeight(QFont.Weight.Medium)
        painter.setFont(row_font)
        fm = QFontMetricsF(row_font)

        y = rect.top() + _PADDING + _HEADER_HEIGHT
        for row in self._rows:
            self._paint_row(painter, rect, y, row, fm)
            y += _ROW_HEIGHT

    # -- Helpers ---------------------------------------------------------------

    def _paint_row(
        self,
        painter: QPainter,
        rect: QRectF,
        y: float,
        row: HotkeyBinding,
        fm: QFontMetricsF,
    ) -> None:
        palette = TOKENS.palette
        left = rect.left() + _PADDING

        key_rect = QRectF(left, y, _KEY_COLUMN, _ROW_HEIGHT)
        label_rect = QRectF(
            left + _KEY_COLUMN,
            y,
            rect.width() - _PADDING * 2 - _KEY_COLUMN,
            _ROW_HEIGHT,
        )

        painter.setPen(QColor(palette.text_secondary))
        painter.drawText(
            key_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            row.keysequence,
        )

        # Colour label by kind so the panel reads at a glance.
        if row.kind == "item":
            colour = QColor(palette.text_primary)
        elif row.kind == "spell":
            colour = QColor(palette.accent)
        else:
            colour = QColor(palette.text_secondary)
        painter.setPen(colour)

        label = row.label
        # Elide if too wide to keep rows single-line.
        elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, label_rect.width())
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided,
        )

    @staticmethod
    def _sort_rows(rows: Iterable[HotkeyBinding]) -> list[HotkeyBinding]:
        """Order bindings F1..F12 first, then digits, then the rest.

        Sort stability is important so two runs of the same preset paint in
        the same order -- players build muscle memory from row position.
        """

        def weight(row: HotkeyBinding) -> tuple[int, int, str]:
            ks = row.keysequence
            # F-keys: "F1" .. "F12". Grab the number so F2 < F10.
            if ks.startswith("F") and ks[1:].isdigit():
                return (0, int(ks[1:]), "")
            # Shift+F-keys / Ctrl+F-keys: second priority group.
            if ("Shift+F" in ks or "Ctrl+F" in ks) and ks.rsplit("+", 1)[-1][1:].isdigit():
                return (1, int(ks.rsplit("+", 1)[-1][1:]), "")
            # Bare digits.
            if ks.isdigit():
                return (2, int(ks), "")
            # Everything else, alphabetical.
            return (9, 0, ks)

        return sorted(rows, key=weight)


__all__ = ["HotbarPanel"]
