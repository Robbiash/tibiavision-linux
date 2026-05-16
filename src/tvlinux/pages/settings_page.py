"""Settings page: Hunt Mode master switch + capture behaviour + HUD overlay.

The app never interacts with Tibia. The only moving parts here are:

- **Hunt Mode** -- gates the clipboard watcher and history auto-logger.
- **Auto-log** -- whether freshly captured hunts should append to Hunt History.
- **HUD overlay** -- show/hide the click-through on-screen HUD without needing
  the tray menu.

To get fresh numbers on the HUD, the user presses Tibia's built-in
"Copy to clipboard" menu entry themselves -- we never synthesize
clicks or keystrokes.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..hunt_mode import HuntModeManager
from ..theme import TOKENS
from ..ui_helpers import card, muted_label, pill_button, section_label

# Typical "comfortable form" width -- wider than this and the fields
# feel stranded in fullscreen, narrower than this and they squeeze.
_CONTENT_MAX_WIDTH = 860


class SettingsPage(QWidget):
    """Settings surface.

    Emits ``hud_visibility_requested(bool)`` when the user toggles the HUD
    checkbox. The Application wiring is responsible for reflecting the tray
    menu in :meth:`set_hud_visible` so the checkbox stays authoritative.
    """

    hud_visibility_requested = Signal(bool)
    open_hud_layout_editor_requested = Signal()
    # Emits the currently selected mirror placement key.
    mirror_placement_changed = Signal(str)

    def __init__(
        self,
        hunt_mode: HuntModeManager,
        parent: QWidget | None = None,
        *,
        hud_visible: bool = True,
    ) -> None:
        super().__init__(parent)
        self._mode = hunt_mode
        self._initial_hud_visible = hud_visible
        self._build_ui()
        self._refresh_from_config()
        self._mode.config_changed.connect(lambda _cfg: self._refresh_from_config())
        self._mode.toggled.connect(lambda _a: self._refresh_from_config())

    # -- UI build ---------------------------------------------------------

    def _build_ui(self) -> None:
        s = TOKENS.spacing

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page_layout.addWidget(scroll)

        content = QWidget(scroll)
        content_row = QHBoxLayout(content)
        content_row.setContentsMargins(s.lg, s.md, s.lg, s.lg)
        content_row.setSpacing(0)
        content_row.addStretch(1)

        inner = QWidget(content)
        inner.setMaximumWidth(_CONTENT_MAX_WIDTH)
        inner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(s.md)
        content_row.addWidget(inner, 10)
        content_row.addStretch(1)

        scroll.setWidget(content)

        # --- Overlay engine (diagnostic) --------------------------------
        # Shows which always-on-top mechanism is currently in effect so
        # the user can self-report in bug threads instead of us having
        # to ask them to run the app from a terminal and read logs.
        # Populated by :meth:`set_overlay_backend`; hidden until the
        # Application wiring tells us which backend was chosen.
        overlay_card = card(inner, compact=True)
        ol = overlay_card.body_layout
        ol.addWidget(section_label("OVERLAY ENGINE", overlay_card))
        self._lbl_overlay_backend = QLabel("Overlay mode: detecting...", overlay_card)
        self._lbl_overlay_backend.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        ol.addWidget(self._lbl_overlay_backend)
        ol.addWidget(
            muted_label(
                "XWayland (override-redirect) is the default and the most "
                "reliable: mirrors are bypass-WM X11 windows, which KWin and "
                "Mutter cannot re-stack below a fullscreen / focused Tibia. "
                "Wayland (layer-shell) is an opt-in via --force-wayland for "
                "systems with a matching layer-shell-qt install. Wayland "
                "(best-effort) is the fallback when layer-shell-qt fails to "
                "load -- stacking is then a compositor hint and may lose "
                "the race against Tibia.",
                overlay_card,
            )
        )
        self._overlay_card = overlay_card
        overlay_card.hide()
        outer.addWidget(overlay_card)

        # --- Hunt Mode master ------------------------------------------
        master = card(inner, compact=True)
        ml = master.body_layout
        ml.addWidget(section_label("HUNT MODE", master))
        self._chk_active = QCheckBox("Hunt Mode is active", master)
        self._chk_active.toggled.connect(lambda on: self._mode.set_active(on))
        ml.addWidget(self._chk_active)
        ml.addWidget(
            muted_label(
                "Hunt Mode must be ON for the app to read anything from the "
                "clipboard. When off, the app ignores the clipboard completely "
                "and never logs a hunt. Use this as your \u201cI'm done "
                "playing\u201d switch.",
                master,
            )
        )
        outer.addWidget(master)

        # --- Capture behaviour -----------------------------------------
        capture_card = card(inner, compact=True)
        cl = capture_card.body_layout
        cl.addWidget(section_label("CAPTURE BEHAVIOUR", capture_card))
        self._chk_auto_log = QCheckBox("Auto-log captured hunts to Hunt History", capture_card)
        self._chk_auto_log.toggled.connect(
            lambda on: self._mode.update_config(auto_log_to_history=on)
        )
        cl.addWidget(self._chk_auto_log)
        cl.addWidget(
            muted_label(
                "Fresh numbers arrive only when you press Tibia's built-in "
                "\u201cCopy to clipboard\u201d menu entry on the Hunt Analyser "
                "or Party Hunt widget. The app watches the OS clipboard -- it "
                "never clicks, types, or touches the Tibia window for you.",
                capture_card,
            )
        )
        outer.addWidget(capture_card)

        # --- HUD overlay -----------------------------------------------
        hud_card = card(inner, compact=True)
        hl = hud_card.body_layout
        hl.addWidget(section_label("HUD OVERLAY", hud_card))
        self._chk_hud = QCheckBox("Show HUD overlay on screen", hud_card)
        self._chk_hud.setChecked(self._initial_hud_visible)
        self._chk_hud.toggled.connect(self.hud_visibility_requested.emit)
        hl.addWidget(self._chk_hud)
        hl.addWidget(
            muted_label(
                "The HUD is a strictly click-through overlay drawn on top of "
                "the game. Every mouse click and keypress passes right through "
                "to Tibia. Turn it off to hide the overlay without closing the "
                "app; you can also toggle it from the system tray menu.",
                hud_card,
            )
        )
        # Layout editor launcher. Lives in a companion window (rather
        # than toggling the live HUD into editable mode) so the HUD
        # stays strictly click-through at all times -- see
        # :mod:`tvlinux.hud_layout_editor` for the rationale.
        self._btn_open_layout = pill_button("Open layout editor", variant="ghost", parent=hud_card)
        self._btn_open_layout.setToolTip(
            "Open a companion window where you can drag HUD panels to new positions"
        )
        self._btn_open_layout.setAccessibleName("Open HUD layout editor")
        self._btn_open_layout.clicked.connect(self.open_hud_layout_editor_requested.emit)
        hl.addWidget(self._btn_open_layout)
        outer.addWidget(hud_card)

        # --- Mirror placement ------------------------------------------
        placement_card = card(inner, compact=True)
        pc = placement_card.body_layout
        pc.addWidget(section_label("MIRROR PLACEMENT", placement_card))
        pc.addWidget(
            muted_label(
                "Floating mirrors are the supported placement mode. "
                "Regions stay as click-through overlays above the game "
                "when locked.",
                placement_card,
            )
        )
        placement_row = QHBoxLayout()
        placement_row.setContentsMargins(0, 0, 0, 0)
        placement_row.setSpacing(TOKENS.spacing.sm)
        placement_label = QLabel("Show mirrors as", placement_card)
        placement_label.setAccessibleName("Mirror placement")
        placement_row.addWidget(placement_label)
        self._cmb_placement = QComboBox(placement_card)
        self._cmb_placement.setAccessibleName("Mirror placement selector")
        self._cmb_placement.setToolTip("Floating: classic click-through mirrors above the game.")
        for key, label in PLACEMENT_CHOICES:
            self._cmb_placement.addItem(label, key)
        current = load_mirror_placement()
        idx = max(0, self._cmb_placement.findData(current))
        self._cmb_placement.setCurrentIndex(idx)
        self._cmb_placement.currentIndexChanged.connect(self._on_placement_changed)
        placement_row.addWidget(self._cmb_placement, 1)
        pc.addLayout(placement_row)
        outer.addWidget(placement_card)

        outer.addStretch(1)

    # -- Public API -------------------------------------------------------

    def set_hud_visible(self, visible: bool) -> None:
        """Reflect an external HUD toggle (tray menu) into the checkbox."""
        if self._chk_hud.isChecked() == visible:
            return
        self._chk_hud.blockSignals(True)
        self._chk_hud.setChecked(visible)
        self._chk_hud.blockSignals(False)

    def set_overlay_backend(self, label: str) -> None:
        """Populate the "Overlay engine" info card.

        Safe to call multiple times. An empty ``label`` hides the
        card (useful for tests that don't route through ``__main__``).
        """
        text = (label or "").strip()
        if not text:
            self._overlay_card.hide()
            return
        self._lbl_overlay_backend.setText(f"Overlay mode: {text}")
        self._overlay_card.show()

    def current_mirror_placement(self) -> str:
        """Return the current placement mode key."""
        data = self._cmb_placement.currentData()
        return str(data) if data in _PLACEMENT_KEYS else "floating"

    # -- Mirror placement -------------------------------------------------

    def _on_placement_changed(self, _index: int) -> None:
        key = self.current_mirror_placement()
        save_mirror_placement(key)
        self.mirror_placement_changed.emit(key)

    # -- Config sync ------------------------------------------------------

    def _refresh_from_config(self) -> None:
        cfg = self._mode.config
        self._chk_active.blockSignals(True)
        self._chk_active.setChecked(cfg.active)
        self._chk_active.blockSignals(False)
        self._chk_auto_log.blockSignals(True)
        self._chk_auto_log.setChecked(cfg.auto_log_to_history)
        self._chk_auto_log.blockSignals(False)


# -- Mirror placement helpers ----------------------------------------------

# Single source of truth for the placement dropdown, also used by
# :mod:`tvlinux.app` when honoring the persisted value on launch.
PLACEMENT_CHOICES: list[tuple[str, str]] = [
    ("floating", "Floating mirrors (default)"),
]
_PLACEMENT_KEYS = {key for key, _ in PLACEMENT_CHOICES}
_PLACEMENT_SETTINGS_KEY = "ui/mirror_placement"
_SAFETY_RAISE_SETTINGS_KEY = "ui/mirror_safety_raise_enabled"


def load_mirror_placement() -> str:
    """Read the persisted placement mode, falling back to ``floating``."""
    settings = QSettings()
    value = settings.value(_PLACEMENT_SETTINGS_KEY, "floating")
    if value in _PLACEMENT_KEYS:
        return str(value)
    return "floating"


def save_mirror_placement(value: str) -> None:
    """Persist the placement mode for the next launch."""
    if value not in _PLACEMENT_KEYS:
        return
    QSettings().setValue(_PLACEMENT_SETTINGS_KEY, value)


def load_mirror_safety_raise_enabled() -> bool:
    """Whether the periodic "keep mirrors above Tibia" safety-net is on.

    Default ``True`` because the failure mode (mirror sinks behind the game)
    is the most common complaint and the cost (one raise pass every 1.5 s
    only while another app is active) is negligible. Power users who
    notice the raise nudging their window stack can flip this off via
    ``QSettings``; there is no UI toggle yet because the vast majority of
    users want the default.
    """
    settings = QSettings()
    return bool(settings.value(_SAFETY_RAISE_SETTINGS_KEY, True, type=bool))


def save_mirror_safety_raise_enabled(enabled: bool) -> None:
    QSettings().setValue(_SAFETY_RAISE_SETTINGS_KEY, bool(enabled))


__all__ = [
    "PLACEMENT_CHOICES",
    "SettingsPage",
    "load_mirror_placement",
    "load_mirror_safety_raise_enabled",
    "save_mirror_placement",
    "save_mirror_safety_raise_enabled",
]
