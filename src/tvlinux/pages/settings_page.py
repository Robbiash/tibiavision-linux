"""Settings page -- Hunt Mode configuration + calibration.

Lets the user:

- Toggle Hunt Mode on/off (mirrors the tray menu and status footer).
- Pick the trigger key (default: space).
- Tune ``min_refresh_interval_sec`` and the optional auto-fire fallback.
- Calibrate the two :class:`~tvlinux.hunt_mode.CopyAnchor` positions for
  the Hunt Analyser and Party Hunt widgets.
- See at a glance which backends are available (pynput/evdev for keys,
  xdotool/ydotool for clicks), and surface the fix command when something
  is missing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..hunt_mode import CopyAnchor, HuntModeManager
from ..hunt_refresh import HuntRefresher
from ..key_listener import BackendStatus, PassiveKeyListener
from ..theme import TOKENS
from ..ui_helpers import card, muted_label, pill_button, section_label

# Typical "comfortable form" width -- wider than this and the fields start
# feeling stranded in fullscreen, narrower than this and they squeeze.
_CONTENT_MAX_WIDTH = 860


class SettingsPage(QWidget):
    """Hunt Mode controls, grouped by concern."""

    def __init__(
        self,
        hunt_mode: HuntModeManager,
        refresher: HuntRefresher,
        key_listener: PassiveKeyListener,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = hunt_mode
        self._refresher = refresher
        self._listener = key_listener
        self._build_ui()
        self._mode.config_changed.connect(lambda _cfg: self._sync_from_config())
        self._mode.toggled.connect(lambda _a: self._sync_from_config())
        self._sync_from_config()

    def _build_ui(self) -> None:
        s = TOKENS.spacing

        # Wrap the whole page in a vertical scroll area so dense content
        # never clips vertically at short window heights -- the scroll
        # stays invisible when everything fits.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page_layout.addWidget(scroll)

        # Constrain the form to a comfortable reading width. Without this
        # the cards stretch edge-to-edge on ultrawide monitors and the
        # form fields feel stranded; with it, Settings reads like a
        # focused configuration sheet at any window size.
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
                "When off, the app never listens to the clipboard, never presses "
                "any key for you, and never logs a hunt to history. Use this as "
                "your 'I'm done playing' switch.",
                master,
            )
        )
        outer.addWidget(master)

        # --- Trigger key -----------------------------------------------
        trigger = card(inner, compact=True)
        tl = trigger.body_layout
        tl.addWidget(section_label("TRIGGER KEY", trigger))

        # Header row: the most important control -- key picker + enabled
        # toggle -- gets its own horizontal row with real stretch on the
        # QLineEdit so it grows/shrinks gracefully.
        key_row = QHBoxLayout()
        key_row.setSpacing(s.sm)
        key_row.addWidget(QLabel("Listen for key:", trigger))
        self._edit_key = QLineEdit(trigger)
        self._edit_key.setMaxLength(16)
        self._edit_key.setPlaceholderText("space")
        self._edit_key.setMinimumWidth(120)
        self._edit_key.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._edit_key.editingFinished.connect(self._commit_trigger_key)
        key_row.addWidget(self._edit_key, 1)
        self._chk_key_enabled = QCheckBox("Enabled", trigger)
        self._chk_key_enabled.toggled.connect(self._commit_trigger_enabled)
        key_row.addWidget(self._chk_key_enabled)
        tl.addLayout(key_row)

        tl.addWidget(
            muted_label(
                "Observer-only. The key is never consumed -- Tibia still gets "
                "every keystroke. Pick a key you already press a lot while "
                "hunting (space auto-targets).",
                trigger,
            )
        )

        # Secondary controls live in a QFormLayout so long labels wrap
        # above the field on narrow widths and sit beside it on wider
        # ones. Qt does that split automatically via WrapLongRows.
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(s.md)
        form.setVerticalSpacing(s.sm)
        form.setContentsMargins(0, s.sm, 0, 0)

        self._spin_rate = QSpinBox(trigger)
        self._spin_rate.setRange(1, 3600)
        self._spin_rate.setSuffix(" s")
        self._spin_rate.setMinimumWidth(110)
        self._spin_rate.valueChanged.connect(self._commit_min_interval)
        form.addRow("Min seconds between refreshes:", self._spin_rate)

        self._spin_auto = QSpinBox(trigger)
        self._spin_auto.setRange(0, 3600)
        self._spin_auto.setSuffix(" s")
        self._spin_auto.setMinimumWidth(110)
        self._spin_auto.valueChanged.connect(self._commit_auto_interval)
        form.addRow("Auto-fire every (fallback, 0 = off):", self._spin_auto)

        self._edit_title = QLineEdit(trigger)
        self._edit_title.setPlaceholderText("Tibia")
        self._edit_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._edit_title.editingFinished.connect(self._commit_title_substring)
        form.addRow("Only fire when window title contains:", self._edit_title)

        tl.addLayout(form)

        outer.addWidget(trigger)

        # --- Calibration ----------------------------------------------
        calib = card(inner, compact=True)
        cl = calib.body_layout
        cl.addWidget(section_label("CALIBRATE COPY BUTTONS", calib))
        cl.addWidget(
            muted_label(
                "Teach the app where the 'Copy to clipboard' option sits for each "
                "widget. Do this once per Tibia session layout.",
                calib,
            )
        )

        # Each anchor gets a vertical block: title + wrapping status +
        # right-aligned button cluster. Vertical stacks reflow well at
        # every width and keep the Calibrate/Clear buttons next to the
        # status they act on.
        for kind, title in (("hunt", "Hunt Analyser"), ("party", "Party Hunt")):
            block = QWidget(calib)
            block.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            bl = QVBoxLayout(block)
            bl.setContentsMargins(0, s.xs, 0, s.xs)
            bl.setSpacing(s.xs)

            header_row = QHBoxLayout()
            header_row.setSpacing(s.sm)
            name = QLabel(title, block)
            name.setStyleSheet(f"font-weight: {TOKENS.type.weight_bold};")
            header_row.addWidget(name)
            header_row.addStretch(1)
            btn = pill_button("Calibrate", parent=block)
            clear = pill_button("Clear", variant="ghost", parent=block)
            header_row.addWidget(btn)
            header_row.addWidget(clear)
            bl.addLayout(header_row)

            val = QLabel("(not calibrated)", block)
            val.setProperty("role", "muted")
            val.setWordWrap(True)
            bl.addWidget(val)

            btn.clicked.connect(lambda _=False, k=kind, v=val: self._run_calibration(k, v))
            clear.clicked.connect(lambda _=False, k=kind, v=val: self._clear_anchor(k, v))

            cl.addWidget(block)
            setattr(self, f"_{kind}_anchor_label", val)

        outer.addWidget(calib)

        # --- Diagnostics ----------------------------------------------
        diag = card(inner, compact=True)
        dl = diag.body_layout
        dl.addWidget(section_label("DIAGNOSTICS", diag))
        self._lbl_listener = QLabel("", diag)
        self._lbl_listener.setWordWrap(True)
        dl.addWidget(self._lbl_listener)
        self._lbl_tool = QLabel("", diag)
        self._lbl_tool.setWordWrap(True)
        dl.addWidget(self._lbl_tool)
        outer.addWidget(diag)

        outer.addStretch(1)

        self._update_diagnostics()

    # -- State sync ---------------------------------------------------

    def _sync_from_config(self) -> None:
        cfg = self._mode.config
        widgets = [
            self._chk_active,
            self._edit_key,
            self._chk_key_enabled,
            self._spin_rate,
            self._spin_auto,
            self._edit_title,
        ]
        for w in widgets:
            w.blockSignals(True)
        self._chk_active.setChecked(cfg.active)
        self._edit_key.setText(cfg.trigger_key)
        self._chk_key_enabled.setChecked(cfg.trigger_key_enabled)
        self._spin_rate.setValue(max(1, cfg.min_refresh_interval_sec))
        self._spin_auto.setValue(max(0, cfg.auto_fire_fallback_sec))
        self._edit_title.setText(cfg.tibia_window_substring)
        for w in widgets:
            w.blockSignals(False)
        self._update_anchor_labels()

    def _update_anchor_labels(self) -> None:
        cfg = self._mode.config
        for kind, anchor in (
            ("hunt", cfg.copy_anchor_hunt),
            ("party", cfg.copy_anchor_party),
        ):
            lbl = getattr(self, f"_{kind}_anchor_label", None)
            if not isinstance(lbl, QLabel):
                continue
            if anchor is None:
                lbl.setText("(not calibrated)")
            else:
                lbl.setText(
                    f"right-click ({anchor.right_click_x}, {anchor.right_click_y}) "
                    f"-> menu ({anchor.menu_x}, {anchor.menu_y})"
                )

    # -- Commit handlers ----------------------------------------------

    def _commit_trigger_key(self) -> None:
        key = self._edit_key.text().strip() or "space"
        self._mode.update_config(trigger_key=key)
        self._listener.set_trigger_key(key)

    def _commit_trigger_enabled(self, on: bool) -> None:
        self._mode.update_config(trigger_key_enabled=on)

    def _commit_min_interval(self, value: int) -> None:
        self._mode.update_config(min_refresh_interval_sec=int(value))

    def _commit_auto_interval(self, value: int) -> None:
        self._mode.update_config(auto_fire_fallback_sec=int(value))

    def _commit_title_substring(self) -> None:
        sub = self._edit_title.text().strip()
        self._mode.update_config(tibia_window_substring=sub)
        self._listener.set_tibia_substring(sub)

    # -- Calibration --------------------------------------------------

    def _run_calibration(self, kind: str, _label: QLabel) -> None:
        QMessageBox.information(
            self,
            f"Calibrate {kind}",
            "In the next 5 seconds, move your mouse to the EXACT spot where "
            "you would right-click the widget. Hold still until the app "
            "captures the position. Then repeat for the 'Copy to clipboard' "
            "menu item. Press OK to start.",
        )

        self._step_calibration(kind, step=1)

    def _step_calibration(self, kind: str, *, step: int) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle(f"Calibrate {kind} - step {step}/2")
        if step == 1:
            msg.setText(
                "Place the mouse over the Tibia widget where you would "
                "right-click. Press OK and hold still for 3 seconds."
            )
        else:
            msg.setText(
                "Now place the mouse over the 'Copy to clipboard' menu "
                "item (open the right-click menu if needed). Press OK "
                "and hold still for 3 seconds."
            )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if msg.exec() != QMessageBox.StandardButton.Ok:
            return

        def capture() -> None:
            pos = QCursor.pos()
            if step == 1:
                self._pending_right = (pos.x(), pos.y())
                self._step_calibration(kind, step=2)
            else:
                right = getattr(self, "_pending_right", (0, 0))
                anchor = CopyAnchor(
                    right_click_x=int(right[0]),
                    right_click_y=int(right[1]),
                    menu_x=int(pos.x()),
                    menu_y=int(pos.y()),
                )
                if kind == "hunt":
                    self._mode.update_config(copy_anchor_hunt=anchor)
                else:
                    self._mode.update_config(copy_anchor_party=anchor)
                self._pending_right = (0, 0)
                self._update_anchor_labels()

        QTimer.singleShot(3000, capture)

    def _clear_anchor(self, kind: str, _label: QLabel) -> None:
        if kind == "hunt":
            self._mode.update_config(copy_anchor_hunt=None)
        else:
            self._mode.update_config(copy_anchor_party=None)
        self._update_anchor_labels()

    # -- Diagnostics --------------------------------------------------

    def _update_diagnostics(self) -> None:
        status: BackendStatus = self._listener._status  # type: ignore[attr-defined]
        if status.available:
            self._lbl_listener.setText(f"Key listener: OK ({status.backend})")
            self._lbl_listener.setProperty("role", "success")
        else:
            self._lbl_listener.setText(
                f"Key listener: unavailable ({status.backend}). {status.reason}"
            )
            self._lbl_listener.setProperty("role", "warning")

        if self._refresher.available:
            self._lbl_tool.setText(f"Click tool: OK ({self._refresher.tool})")
            self._lbl_tool.setProperty("role", "success")
        else:
            self._lbl_tool.setText(
                "Click tool: unavailable. Install xdotool (X11) or ydotool (Wayland)."
            )
            self._lbl_tool.setProperty("role", "warning")
        # Force QSS re-eval after property changes.
        for lbl in (self._lbl_listener, self._lbl_tool):
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        # Keep the diagnostics fresh when the user revisits the page.
        self._update_diagnostics()


__all__ = ["SettingsPage"]
