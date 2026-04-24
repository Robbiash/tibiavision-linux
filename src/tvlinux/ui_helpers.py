"""Reusable, token-driven widget factories.

Keeping these here means individual screens do not hand-roll tiny QSS snippets
for cards / separators / swatches / pill buttons. Everything reads from
:mod:`tvlinux.theme` so a change to the palette or spacing scale propagates to
every feature automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import TOKENS


class _Card(QFrame):
    """Rounded surface container. Attribute ``body_layout`` is the QVBoxLayout
    that children should be added to (avoids `.layout()` returning ``Optional``).
    """

    body_layout: QVBoxLayout


def card(parent: QWidget | None = None, *, compact: bool = False) -> _Card:
    """Return a rounded surface container used for grouping controls.

    The returned frame has ``objectName == "Card"`` so the global QSS
    (``QFrame#Card``) applies its background / border / radius automatically.
    Callers attach children via ``frame.body_layout.addWidget(...)``.

    Set ``compact=True`` for dense form-heavy pages (like Settings) where
    the standard ``lg`` padding wastes horizontal room -- uses ``md``
    margins instead, which visibly reclaims ~16px of usable width on
    each side without making fullscreen look empty.
    """
    frame = _Card(parent)
    frame.setObjectName("Card")
    frame.setFrameShape(QFrame.Shape.NoFrame)
    layout = QVBoxLayout(frame)
    s = TOKENS.spacing
    # Generous padding by default (the Jurojin look leans on breathing
    # room inside cards). Compact cards trade a bit of that for width.
    pad = s.md if compact else s.lg
    layout.setContentsMargins(pad, pad, pad, pad)
    layout.setSpacing(s.sm)
    frame.body_layout = layout
    return frame


def hline(parent: QWidget | None = None) -> QFrame:
    """Return a 1 px subtle horizontal separator styled against the palette."""
    line = QFrame(parent)
    line.setProperty("role", "hline")
    line.setFrameShape(QFrame.Shape.NoFrame)
    line.setFixedHeight(1)
    return line


def section_label(text: str, parent: QWidget | None = None) -> QLabel:
    """Small uppercase caption used as a section header inside a card."""
    label = QLabel(text, parent)
    label.setProperty("role", "caption")
    return label


def muted_label(
    text: str,
    parent: QWidget | None = None,
    *,
    wrap: bool = True,
) -> QLabel:
    """Return a muted caption label that wraps long text by default.

    ``QLabel`` defaults to ``wordWrap == False``, which inflates the
    layout's minimum width whenever a muted helper line runs long --
    the exact cause of the Settings page looking squeezed at narrow
    widths. Default to wrapping on; short chips can opt out via
    ``wrap=False``.
    """
    label = QLabel(text, parent)
    label.setProperty("role", "muted")
    if wrap:
        label.setWordWrap(True)
    return label


def pill_button(
    text: str,
    *,
    variant: str = "default",
    parent: QWidget | None = None,
) -> QPushButton:
    """Return a stylesheet-driven button.

    ``variant`` maps to the ``QPushButton[variant="..."]`` selectors in the
    global QSS: ``"default"`` (no variant attribute), ``"primary"``,
    ``"danger"``, ``"ghost"``, ``"swatch"``.
    """
    btn = QPushButton(text, parent)
    if variant != "default":
        btn.setProperty("variant", variant)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def swatch_button(
    hex_color: str,
    tooltip: str,
    parent: QWidget | None = None,
    size: int = 18,
) -> QPushButton:
    """18x18 color swatch used for the preset border palette."""
    btn = QPushButton(parent)
    btn.setProperty("variant", "swatch")
    btn.setFixedSize(size, size)
    btn.setToolTip(tooltip)
    # Icon-only: mirror the tooltip to the accessibility tree so
    # screen readers announce the preset name and Tab can reach the
    # button.
    btn.setAccessibleName(tooltip)
    btn.setAccessibleDescription(f"Set border color to {tooltip.lower()}")
    btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    # Background color must be inline (per-instance), border / radius come
    # from the QSS swatch variant.
    btn.setStyleSheet(f'QPushButton[variant="swatch"] {{ background: {hex_color}; }}')
    return btn


def color_picker_button(parent: QWidget | None = None) -> QPushButton:
    """Hex-picker button. Caller updates its background + label via
    :func:`apply_color_swatch`.
    """
    btn = QPushButton(parent)
    btn.setFixedHeight(28)
    btn.setMinimumWidth(88)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip("Pick custom border color")
    btn.setAccessibleName("Pick custom border color")
    btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return btn


def apply_color_swatch(btn: QPushButton, hex_color: str) -> None:
    """Paint ``btn`` to preview ``hex_color`` and set its label.

    Text color flips between black/white based on the luminance of the
    background so the label stays legible on both light and dark swatches.
    Used by the control panel's hex picker so the button itself acts as the
    preview.
    """
    c = QColor(hex_color)
    if not c.isValid():
        hex_color = TOKENS.palette.accent
        c = QColor(hex_color)
    # Threshold tuned for the Jurojin palette: the dark accent_fg reads
    # well on any background brighter than mid-gray; pure white wins below.
    text_color = TOKENS.palette.accent_fg if c.lightness() > 140 else TOKENS.palette.text_primary
    r = TOKENS.radius
    btn.setStyleSheet(
        f"QPushButton {{ background: {hex_color}; color: {text_color};"
        f" border: 2px solid {TOKENS.palette.border_strong};"
        f" border-radius: {r.sm}px;"
        f" padding: 0 {TOKENS.spacing.sm}px;"
        f" font-weight: {TOKENS.type.weight_bold};"
        " }"
    )
    btn.setText(hex_color.upper())


# -- Bundled icons -----------------------------------------------------------

_ASSETS_PKG = "tvlinux.assets.icons"


def icon(name: str) -> QIcon:
    """Load a bundled SVG icon by name (without extension).

    Falls back to an empty :class:`QIcon` if the asset is missing, so a typo
    degrades to "no icon" instead of a hard crash. The name lookup searches
    the ``tvlinux.assets.icons`` package, which ships the Lucide SVGs used by
    the toolbar.
    """
    try:
        path = resources.files(_ASSETS_PKG).joinpath(f"{name}.svg")
        as_path = Path(str(path))
        if as_path.exists():
            return QIcon(str(as_path))
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return QIcon()


def default_icon_size() -> QSize:
    return QSize(18, 18)


# -- Collapsible card --------------------------------------------------------


class _ClickableRow(QWidget):
    """Transparent widget whose left-click emits :pyattr:`clicked`.

    Used as the clickable header of :class:`CollapsibleCard` so the entire
    header strip (not just the chevron) toggles the card.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class CollapsibleCard(QFrame):
    """Card with a clickable header that hides/shows its body.

    API mirrors what :func:`card` gave callers -- add children via
    ``card.body_layout.addWidget(...)`` / ``addLayout(...)``. Extras
    (counts, badges) can be pinned to the right side of the header via
    :meth:`add_header_widget`.

    Header clicks (anywhere on the strip, not just the chevron) toggle
    the body. Emits :pyattr:`toggled` with the new expanded state.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        expanded: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.NoFrame)

        s = TOKENS.spacing

        outer = QVBoxLayout(self)
        # Tighter vertical padding than card() because the header already
        # provides visual weight; keep horizontal lg for content breathing room.
        outer.setContentsMargins(s.lg, s.md, s.lg, s.md)
        outer.setSpacing(s.sm)

        self._header = _ClickableRow(self)
        self._header_row = QHBoxLayout(self._header)
        self._header_row.setContentsMargins(0, 0, 0, 0)
        self._header_row.setSpacing(s.sm)

        self._chevron = QLabel("\u25be", self._header)  # down-pointing small triangle
        self._chevron.setProperty("role", "muted")
        self._chevron.setFixedWidth(12)
        self._header_row.addWidget(self._chevron)

        self._title = QLabel(title, self._header)
        self._title.setProperty("role", "caption")
        self._header_row.addWidget(self._title)

        self._header_row.addStretch(1)
        self._header.clicked.connect(self.toggle)

        outer.addWidget(self._header)

        self._body = QWidget(self)
        self.body_layout = QVBoxLayout(self._body)
        self.body_layout.setContentsMargins(0, s.xs, 0, 0)
        self.body_layout.setSpacing(s.sm)
        outer.addWidget(self._body)

        self._expanded = True
        if not expanded:
            self.set_expanded(False)

    def add_header_widget(self, widget: QWidget) -> None:
        """Pin ``widget`` to the right-hand side of the header.

        Useful for counts / badges that should stay visible even when the
        card is collapsed (they render next to the title).
        """
        self._header_row.addWidget(widget)

    def set_expanded(self, on: bool) -> None:
        if on == self._expanded:
            return
        self._expanded = on
        self._body.setVisible(on)
        # Right-pointing triangle when collapsed, down-pointing when expanded.
        self._chevron.setText("\u25be" if on else "\u25b8")
        self.toggled.emit(on)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded


# -- Empty state -------------------------------------------------------------


class EmptyState(QFrame):
    """Centred first-run / "nothing yet" widget with an optional CTA.

    Used in place of a bare muted label when a page can be entered with
    zero content (Regions, Hunt History, Audio Timers). The framing
    matters: a crisp illustration-like glyph + a clear primary action
    is the difference between "broken" and "I know what to do next"
    for a first-time user.

    ``icon_name`` resolves via :func:`icon`; the glyph falls back to a
    simple bullet if the asset is missing so the layout still renders.
    """

    action_clicked = Signal()

    def __init__(
        self,
        *,
        icon_name: str,
        title: str,
        subtitle: str,
        action_label: str | None = None,
        on_action: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        s = TOKENS.spacing

        outer = QVBoxLayout(self)
        outer.setContentsMargins(s.xl, s.xl, s.xl, s.xl)
        outer.setSpacing(s.md)
        outer.addStretch(1)

        glyph = QLabel(self)
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setPixmap(icon(icon_name).pixmap(QSize(48, 48)))
        # If the icon is missing, setPixmap receives a null pixmap and
        # the label renders empty; fall back to a soft bullet so the
        # visual hierarchy still has an anchor point at the top.
        if glyph.pixmap().isNull():
            glyph.setText("\u25cb")
            glyph.setStyleSheet(
                f"color: {TOKENS.palette.text_muted};"
                f" font-size: 36pt;"
                f" font-weight: {TOKENS.type.weight_bold};"
            )
        outer.addWidget(glyph, 0, Qt.AlignmentFlag.AlignHCenter)

        heading = QLabel(title, self)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet(
            f"font-size: {TOKENS.type.size_heading + 2}pt;"
            f" font-weight: {TOKENS.type.weight_bold};"
            f" color: {TOKENS.palette.text_primary};"
        )
        outer.addWidget(heading)

        caption = QLabel(subtitle, self)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setWordWrap(True)
        caption.setProperty("role", "muted")
        outer.addWidget(caption)

        if action_label is not None:
            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            btn = pill_button(action_label, variant="primary", parent=self)
            btn.clicked.connect(self.action_clicked.emit)
            if on_action is not None:
                self.action_clicked.connect(on_action)
            btn_row.addWidget(btn)
            btn_row.addStretch(1)
            outer.addSpacing(s.xs)
            outer.addLayout(btn_row)

        outer.addStretch(1)


def empty_state(
    *,
    icon_name: str,
    title: str,
    subtitle: str,
    action_label: str | None = None,
    on_action: Callable[[], None] | None = None,
    parent: QWidget | None = None,
) -> EmptyState:
    """Factory for :class:`EmptyState`. Prefer this over constructing directly."""
    return EmptyState(
        icon_name=icon_name,
        title=title,
        subtitle=subtitle,
        action_label=action_label,
        on_action=on_action,
        parent=parent,
    )
