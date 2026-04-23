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
    """Jurojin-inspired dark palette: deep midnight navy + electric cyan."""

    # Backgrounds, dark -> light.
    bg_app: str = "#0B0E14"  # deep midnight navy (main window chrome)
    bg_surface: str = "#121620"  # sidebars / scroll areas
    bg_elevated: str = "#181D29"  # inputs, dropdowns, menus
    bg_card: str = "#151A25"  # grouped content cards
    bg_hover: str = "#1E2536"
    bg_pressed: str = "#0F131C"

    border_subtle: str = "#222A3A"
    border_strong: str = "#333F58"

    text_primary: str = "#F8FAFC"  # clean white
    text_secondary: str = "#94A3B8"  # cool gray
    text_muted: str = "#475569"

    # Signature Jurojin neon teal/cyan.
    accent: str = "#00E5FF"
    accent_hover: str = "#33EAFF"
    accent_pressed: str = "#00B3CC"
    accent_fg: str = "#0B0E14"  # high-contrast dark text on neon buttons
    accent_soft_rgba: str = "rgba(0, 229, 255, 0.12)"

    success: str = "#10B981"  # neon green
    warning: str = "#F59E0B"
    danger: str = "#F43F5E"  # vibrant urgent rose/red
    danger_hover: str = "#FB7185"
    danger_pressed: str = "#E11D48"


@dataclass(frozen=True)
class DonatePalette:
    """Colors exclusive to the donate dialog.

    Kept separate from :class:`Palette` so the global-QSS coverage test does
    not complain about these tokens being absent from the app-wide stylesheet
    -- they are consumed directly by ``donate_dialog.paintEvent``.
    """

    bg_top: str = "#1A1033"  # deep rich purple fade
    bg_bottom: str = "#0B0E14"
    badge_from: str = "#00E5FF"
    badge_to: str = "#00B3CC"
    badge_text: str = "#0B0E14"
    heart: str = "#F43F5E"


@dataclass(frozen=True)
class Spacing:
    xs: int = 6
    sm: int = 10
    md: int = 16
    lg: int = 24
    xl: int = 32


@dataclass(frozen=True)
class Radius:
    sm: int = 6
    md: int = 10
    lg: int = 16
    pill: int = 999


@dataclass(frozen=True)
class Type:
    font_stack: str = '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    size_caption: int = 11
    size_body: int = 13
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
    font-weight: {t.weight_bold};
    text-transform: uppercase;
    letter-spacing: 1.5px;
}}

QLabel[role="muted"] {{
    color: {p.text_muted};
}}

QLabel[role="success"] {{ color: {p.success}; font-weight: {t.weight_medium}; }}
QLabel[role="warning"] {{ color: {p.warning}; font-weight: {t.weight_medium}; }}
QLabel[role="danger"] {{ color: {p.danger}; font-weight: {t.weight_medium}; }}

/* -- Cards (QFrame#Card) ----------------------------------------------------
   Glassmorphic elevated surface. Ultra-thin border for depth, no heavy
   shadow -- depth comes from the background delta with bg_surface. */
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

/* -- Buttons ----------------------------------------------------------------
   Default buttons are borderless -- the bg_elevated -> bg_hover transition
   carries the affordance. Primary / danger variants are filled and bold
   with dark accent_fg text so they pop off the neon backgrounds. */
QPushButton {{
    background-color: {p.bg_elevated};
    color: {p.text_primary};
    border: 1px solid transparent;
    border-radius: {r.md}px;
    padding: {s.sm}px {s.lg}px;
    min-height: 20px;
    font-weight: {t.weight_medium};
}}
QPushButton:hover {{ background-color: {p.bg_hover}; }}
QPushButton:pressed {{ background-color: {p.bg_pressed}; }}
QPushButton:focus {{ border: 1px solid {p.accent}; outline: none; }}
QPushButton:disabled {{
    color: {p.text_muted};
    background-color: {p.bg_surface};
    border-color: {p.border_subtle};
}}

QPushButton[variant="primary"] {{
    background-color: {p.accent};
    color: {p.accent_fg};
    font-weight: {t.weight_bold};
    border: 1px solid {p.accent_pressed};
}}
QPushButton[variant="primary"]:hover {{ background-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background-color: {p.accent_pressed}; }}

QPushButton[variant="danger"] {{
    background-color: {p.danger};
    color: {p.accent_fg};
    font-weight: {t.weight_bold};
    border: 1px solid {p.danger_pressed};
}}
QPushButton[variant="danger"]:hover {{ background-color: {p.danger_hover}; }}
QPushButton[variant="danger"]:pressed {{ background-color: {p.danger_pressed}; }}

QPushButton[variant="ghost"] {{
    background-color: transparent;
    border-color: transparent;
    color: {p.text_secondary};
}}
QPushButton[variant="ghost"]:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}

/* Icon-only ghost button: square, centered icon, subtle hover halo.
   Use for bulk / secondary actions that benefit from compactness
   (e.g. Show/Hide/Lock/Unlock in the page header). Pair with tooltip. */
QPushButton[variant="icon"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {r.md}px;
    padding: 0;
    color: {p.text_secondary};
}}
QPushButton[variant="icon"]:hover {{
    background-color: {p.bg_hover};
    border-color: {p.border_subtle};
    color: {p.text_primary};
}}
QPushButton[variant="icon"]:pressed {{ background-color: {p.bg_pressed}; }}
QPushButton[variant="icon"]:focus {{ border-color: {p.accent}; outline: none; }}

/* Preset swatches: 18x18 color chips with a thick hover ring for tactile
   feedback. Border bumped to 2 px so the chip reads as a "button" rather
   than a plain div. */
QPushButton[variant="swatch"] {{
    border: 2px solid {p.border_strong};
    border-radius: {r.sm}px;
    padding: 0;
    min-height: 18px;
    min-width: 18px;
}}
QPushButton[variant="swatch"]:hover {{
    border: 2px solid {p.text_primary};
}}

/* -- Lists + tree -----------------------------------------------------------
   Selected row uses a soft translucent accent fill plus a 3 px solid leader
   line -- classic Jurojin "active table" cue. */
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
    border-left: 3px solid transparent;
    color: {p.text_primary};
}}
QListWidget::item:selected, QListView::item:selected {{
    background-color: {p.accent_soft_rgba};
    border-left: 3px solid {p.accent};
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

/* -- Checkbox ---------------------------------------------------------------
   Toggle-switch look: 32x18 pill that fills with accent when checked.
   image: none suppresses Qt's default tick so the indicator reads as a
   pure toggle background. No sliding thumb -- that would require a
   custom QAbstractButton. */
QCheckBox {{ spacing: {s.sm}px; color: {p.text_primary}; }}
QCheckBox::indicator {{
    width: 32px; height: 18px;
    border-radius: 9px;
    background: {p.bg_elevated};
    border: 1px solid {p.border_strong};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border: 1px solid {p.accent};
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

/* -- Group box (legacy; cards are preferred) ------------------------------- */
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

/* -- Scrollbars -------------------------------------------------------------
   Sleek macOS / Discord style: 8 px track, rounded handle, no groove fill. */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_muted}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.text_muted}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* -- Navigation rail -------------------------------------------------------
   Left-side page switcher. Background sits one step below the app chrome
   so the content area reads as the foreground surface. Nav buttons gain
   a 3 px accent bar on their leading edge when checked -- a Jurojin
   trademark and a far clearer "selected row" cue than pure background
   shading. */
QWidget#NavRail {{
    background-color: {p.bg_app};
    border-right: 1px solid {p.border_subtle};
}}

QToolButton[role="nav"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-left: 3px solid transparent;
    border-radius: {r.md}px;
    padding: {s.xs}px {s.md}px;
    color: {p.text_secondary};
    text-align: left;
    font-weight: {t.weight_medium};
}}
QToolButton[role="nav"]:hover {{
    background-color: {p.bg_hover};
    color: {p.text_primary};
}}
QToolButton[role="nav"]:checked {{
    background-color: {p.accent_soft_rgba};
    border-left: 3px solid {p.accent};
    color: {p.text_primary};
    font-weight: {t.weight_bold};
}}
QToolButton[role="nav"]:focus {{ outline: none; }}

QToolButton[role="nav-footer"] {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {r.md}px;
    padding: {s.xs}px {s.md}px;
    color: {p.text_secondary};
    text-align: left;
}}
QToolButton[role="nav-footer"]:hover {{
    background-color: {p.bg_hover};
    color: {p.danger};
    border-color: {p.border_subtle};
}}

/* Vertical divider used between header action clusters. */
QFrame[role="vline"] {{
    background-color: {p.border_subtle};
    max-width: 1px;
    min-width: 1px;
    border: none;
}}

/* Empty-state placeholder inside cards (e.g. "No regions yet"). */
QLabel[role="empty-state"] {{
    color: {p.text_muted};
    font-style: italic;
    padding: {s.lg}px;
    qproperty-alignment: AlignCenter;
}}
"""


# Legacy constant kept for any external tooling that imported the old string
# directly. Feature code should use ``build_qss(TOKENS)``.
DARK_QSS = build_qss(TOKENS)


def apply(app) -> None:  # type: ignore[no-untyped-def]
    """Install the default QSS onto ``app`` (a ``QApplication``)."""
    app.setStyleSheet(build_qss(TOKENS))
