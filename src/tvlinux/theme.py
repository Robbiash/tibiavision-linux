"""Design tokens and the Qt stylesheet they drive.

The public surface is tiny:

- :data:`TOKENS` -- the single source of truth for colors, spacing, radii, and
  typography. Feature code should import this rather than re-hardcoding hex
  values.
- :func:`build_qss` -- pure function that renders the application-wide Qt
  stylesheet from a ``Tokens`` instance. Kept pure so tests can assert the
  generated QSS references every token (guarding against dead palette entries).
- :func:`apply` -- unchanged: installs the default QSS onto a ``QApplication``.

Adding a new color is a two-step dance: add the field to :class:`Palette`,
reference it somewhere in :func:`build_qss`. The test in ``tests/test_theme.py``
fails loudly if you forget step two.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """Dark-mode color palette. All values are hex ``#rrggbb`` strings."""

    # Backgrounds, dark -> light.
    bg_app: str = "#0f1115"  # main window chrome
    bg_surface: str = "#181a1f"  # generic widget background
    bg_elevated: str = "#1d2029"  # inputs, lists
    bg_card: str = "#1a1d25"  # grouped content cards
    bg_hover: str = "#262a35"
    bg_pressed: str = "#14161b"

    border_subtle: str = "#272a35"
    border_strong: str = "#3a3e4a"

    text_primary: str = "#e9ecf3"
    text_secondary: str = "#a0a6b8"
    text_muted: str = "#6a7080"

    accent: str = "#22a7d6"
    accent_hover: str = "#35bce8"
    accent_pressed: str = "#1a8ab0"
    accent_fg: str = "#ffffff"
    # rgba() form used for translucent selection rows.
    accent_soft_rgba: str = "rgba(34, 167, 214, 0.18)"

    success: str = "#3ecf7a"
    warning: str = "#f4b740"
    danger: str = "#e05252"
    danger_hover: str = "#ef6565"
    danger_pressed: str = "#b83d3d"


@dataclass(frozen=True)
class DonatePalette:
    """Colors exclusive to the donate dialog (vibrant on purpose).

    Kept separate from :class:`Palette` so the global-QSS coverage test does
    not complain about these tokens being absent from the app-wide stylesheet
    -- they are consumed directly by ``donate_dialog.paintEvent``.
    """

    bg_top: str = "#1a1030"
    bg_bottom: str = "#3a0d4a"
    badge_from: str = "#ffd86b"
    badge_to: str = "#ffb23a"
    badge_text: str = "#2a1500"
    heart: str = "#ff7eb9"


@dataclass(frozen=True)
class Spacing:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24


@dataclass(frozen=True)
class Radius:
    sm: int = 4
    md: int = 8
    lg: int = 12
    pill: int = 999


@dataclass(frozen=True)
class Type:
    font_stack: str = '"Inter", "Noto Sans", "Segoe UI", sans-serif'
    size_caption: int = 11
    size_body: int = 12
    size_heading: int = 14
    size_display: int = 20
    weight_regular: int = 400
    weight_medium: int = 500
    weight_bold: int = 600


@dataclass(frozen=True)
class Tokens:
    palette: Palette = dataclasses.field(default_factory=Palette)
    donate: DonatePalette = dataclasses.field(default_factory=DonatePalette)
    spacing: Spacing = dataclasses.field(default_factory=Spacing)
    radius: Radius = dataclasses.field(default_factory=Radius)
    type: Type = dataclasses.field(default_factory=Type)


TOKENS = Tokens()


def build_qss(tokens: Tokens = TOKENS) -> str:
    """Render the application-wide Qt stylesheet from ``tokens``.

    Pure function; no side effects. Feature code normally calls :func:`apply`
    instead, which runs this and installs the result on the ``QApplication``.
    """
    p = tokens.palette
    s = tokens.spacing
    r = tokens.radius
    t = tokens.type

    return f"""
* {{
    font-family: {t.font_stack};
    font-size: {t.size_body}pt;
    color: {p.text_primary};
}}

QWidget {{
    background-color: {p.bg_surface};
    color: {p.text_primary};
}}

QMainWindow, QDialog {{
    background-color: {p.bg_app};
}}

QLabel {{
    background: transparent;
    color: {p.text_primary};
}}

QLabel[role="caption"] {{
    color: {p.text_secondary};
    font-size: {t.size_caption}pt;
    font-weight: {t.weight_medium};
    text-transform: uppercase;
    letter-spacing: 1px;
}}

QLabel[role="muted"] {{
    color: {p.text_muted};
}}

QLabel[role="success"] {{ color: {p.success}; font-weight: {t.weight_medium}; }}
QLabel[role="warning"] {{ color: {p.warning}; font-weight: {t.weight_medium}; }}
QLabel[role="danger"] {{ color: {p.danger}; font-weight: {t.weight_medium}; }}

/* -- Cards (QFrame#Card) ---------------------------------------------------- */
QFrame#Card {{
    background-color: {p.bg_card};
    border: 1px solid {p.border_subtle};
    border-radius: {r.lg}px;
}}

QFrame[role="hline"] {{
    background-color: {p.border_subtle};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* -- Buttons ---------------------------------------------------------------- */
QPushButton {{
    background-color: {p.bg_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: {r.md}px;
    padding: {s.xs}px {s.md}px;
    min-height: 20px;
    font-weight: {t.weight_medium};
}}
QPushButton:hover {{ background-color: {p.bg_hover}; border-color: {p.border_strong}; }}
QPushButton:pressed {{ background-color: {p.bg_pressed}; }}
QPushButton:focus {{ border-color: {p.accent}; outline: none; }}
QPushButton:disabled {{
    color: {p.text_muted};
    background-color: {p.bg_surface};
    border-color: {p.border_subtle};
}}

QPushButton[variant="primary"] {{
    background-color: {p.accent};
    border-color: {p.accent_pressed};
    color: {p.accent_fg};
}}
QPushButton[variant="primary"]:hover {{ background-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background-color: {p.accent_pressed}; }}

QPushButton[variant="danger"] {{
    background-color: {p.danger};
    border-color: {p.danger_pressed};
    color: {p.accent_fg};
}}
QPushButton[variant="danger"]:hover {{ background-color: {p.danger_hover}; }}

QPushButton[variant="ghost"] {{
    background-color: transparent;
    border-color: transparent;
    color: {p.text_secondary};
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}

/* Preset swatches: 18x18 color chips with a hover ring. */
QPushButton[variant="swatch"] {{
    border: 1px solid {p.border_subtle};
    border-radius: {r.sm}px;
    padding: 0;
    min-height: 18px;
    min-width: 18px;
}}
QPushButton[variant="swatch"]:hover {{
    border: 2px solid {p.text_primary};
}}

/* -- Lists + tree ----------------------------------------------------------- */
QListWidget, QListView, QTreeView {{
    background-color: {p.bg_elevated};
    border: 1px solid {p.border_subtle};
    border-radius: {r.md}px;
    padding: {s.xs}px;
    outline: none;
}}
QListWidget::item, QListView::item {{
    padding: {s.xs}px {s.sm}px;
    border-radius: {r.sm}px;
    border-left: 2px solid transparent;
    color: {p.text_primary};
}}
QListWidget::item:selected, QListView::item:selected {{
    background-color: {p.accent_soft_rgba};
    border-left: 2px solid {p.accent};
    color: {p.text_primary};
}}
QListWidget::item:hover:!selected, QListView::item:hover:!selected {{
    background-color: {p.bg_hover};
}}

/* -- Inputs ----------------------------------------------------------------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {{
    background-color: {p.bg_elevated};
    border: 1px solid {p.border_subtle};
    border-radius: {r.sm}px;
    padding: {s.xs}px {s.sm}px;
    selection-background-color: {p.accent};
    selection-color: {p.accent_fg};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus {{
    border-color: {p.accent};
}}
QComboBox::drop-down {{ border: 0; width: 20px; }}

/* -- Slider ----------------------------------------------------------------- */
QSlider::groove:horizontal {{
    height: 6px;
    background: {p.border_subtle};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {p.accent};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {p.accent_hover}; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 3px; }}

/* -- Checkbox --------------------------------------------------------------- */
QCheckBox {{ spacing: {s.xs}px; color: {p.text_primary}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {p.border_strong};
    border-radius: {r.sm}px;
    background: {p.bg_elevated};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: none;
}}
QCheckBox::indicator:disabled {{
    background: {p.bg_surface};
    border-color: {p.border_subtle};
}}

/* -- Menus ------------------------------------------------------------------ */
QMenu {{
    background-color: {p.bg_elevated};
    border: 1px solid {p.border_subtle};
    border-radius: {r.md}px;
    padding: {s.xs}px;
    color: {p.text_primary};
}}
QMenu::item {{
    padding: {s.xs}px {s.md}px;
    border-radius: {r.sm}px;
}}
QMenu::item:selected {{
    background-color: {p.accent_soft_rgba};
    color: {p.text_primary};
}}

/* -- Toolbar + status bar --------------------------------------------------- */
QToolBar {{
    background: {p.bg_app};
    border: none;
    spacing: {s.xs}px;
    padding: {s.xs}px {s.sm}px;
}}
QToolBar::separator {{
    background: {p.border_subtle};
    width: 1px;
    margin: {s.xs}px {s.sm}px;
}}
QToolButton {{
    background: transparent;
    color: {p.text_secondary};
    border: 1px solid transparent;
    border-radius: {r.sm}px;
    padding: {s.xs}px {s.sm}px;
}}
QToolButton:hover {{
    background: {p.bg_hover};
    color: {p.text_primary};
    border-color: {p.border_subtle};
}}
QToolButton:pressed {{ background: {p.bg_pressed}; }}

QStatusBar {{
    background-color: {p.bg_app};
    color: {p.text_secondary};
    border-top: 1px solid {p.border_subtle};
}}

QToolTip {{
    background-color: {p.bg_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_strong};
    border-radius: {r.sm}px;
    padding: {s.xs}px {s.sm}px;
}}

/* -- Group box (kept around for any holdouts; cards preferred going forward) - */
QGroupBox {{
    background-color: {p.bg_card};
    border: 1px solid {p.border_subtle};
    border-radius: {r.lg}px;
    margin-top: {s.md}px;
    padding-top: {s.md}px;
    font-weight: {t.weight_bold};
    color: {p.text_primary};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: {s.md}px;
    padding: 0 {s.xs}px;
    color: {p.text_secondary};
}}

QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.text_muted}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


# Legacy constant kept for any external tooling that imported the old string
# directly. Feature code should use ``build_qss(TOKENS)``.
DARK_QSS = build_qss(TOKENS)


def apply(app) -> None:  # type: ignore[no-untyped-def]
    """Install the default QSS onto ``app`` (a ``QApplication``)."""
    app.setStyleSheet(build_qss(TOKENS))
