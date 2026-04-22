"""Reusable, token-driven widget factories.

Keeping these here means individual screens do not hand-roll tiny QSS snippets
for cards / separators / swatches / pill buttons. Everything reads from
:mod:`tvlinux.theme` so a change to the palette or spacing scale propagates to
every feature automatically.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from .theme import TOKENS


def card(parent: QWidget | None = None) -> QFrame:
    """Return a rounded surface container used for grouping controls.

    The returned frame has ``objectName == "Card"`` so the global QSS
    (``QFrame#Card``) applies its background / border / radius automatically.
    Callers attach a ``QVBoxLayout`` (or similar) to it and add children.
    """
    frame = QFrame(parent)
    frame.setObjectName("Card")
    frame.setFrameShape(QFrame.Shape.NoFrame)
    layout = QVBoxLayout(frame)
    s = TOKENS.spacing
    layout.setContentsMargins(s.md, s.md, s.md, s.md)
    layout.setSpacing(s.sm)
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


def muted_label(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", "muted")
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
    text_color = "#000000" if c.lightness() > 160 else "#ffffff"
    r = TOKENS.radius
    btn.setStyleSheet(
        f"QPushButton {{ background: {hex_color}; color: {text_color};"
        f" border: 1px solid {TOKENS.palette.border_strong};"
        f" border-radius: {r.sm}px;"
        f" padding: 0 {TOKENS.spacing.sm}px;"
        f" font-weight: {TOKENS.type.weight_medium};"
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
