"""Settings page: Hunt Mode master switch + capture behaviour.

The app never interacts with Tibia. The only moving part here is the
Hunt Mode toggle (which gates the clipboard watcher and history
auto-logger) and a single checkbox for whether freshly captured hunts
should auto-append to Hunt History.

To get fresh numbers on the HUD, the user presses Tibia's built-in
"Copy to clipboard" menu entry themselves -- we never synthesize
clicks or keystrokes.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..hunt_mode import HuntModeManager
from ..theme import TOKENS
from ..ui_helpers import card, muted_label, section_label

# Typical "comfortable form" width -- wider than this and the fields
# feel stranded in fullscreen, narrower than this and they squeeze.
_CONTENT_MAX_WIDTH = 860


class SettingsPage(QWidget):
    def __init__(
        self,
        hunt_mode: HuntModeManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = hunt_mode
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

        # --- Hunt Mode master ------------------------------------------
        master = card(inner, compact=True)
        ml = master.body_layout
        ml.addWidget(section_label("HUNT MODE", master))
        self._chk_active = QCheckBox("Hunt Mode is active", master)
        self._chk_active.toggled.connect(lambda on: self._mode.set_active(on))
        ml.addWidget(self._chk_active)
        ml.addWidget(
            muted_label(
                "When off, the app ignores the clipboard and never logs a "
                "hunt. Use this as your 'I'm done playing' switch.",
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
                "'Copy to clipboard' menu entry on the Hunt Analyser or "
                "Party Hunt widget. The app watches the OS clipboard -- it "
                "never clicks, types, or touches the Tibia window for you.",
                capture_card,
            )
        )
        outer.addWidget(capture_card)

        outer.addStretch(1)

    # -- Config sync ------------------------------------------------------

    def _refresh_from_config(self) -> None:
        cfg = self._mode.config
        self._chk_active.blockSignals(True)
        self._chk_active.setChecked(cfg.active)
        self._chk_active.blockSignals(False)
        self._chk_auto_log.blockSignals(True)
        self._chk_auto_log.setChecked(cfg.auto_log_to_history)
        self._chk_auto_log.blockSignals(False)


__all__ = ["SettingsPage"]
