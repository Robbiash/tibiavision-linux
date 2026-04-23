"""Shell window: nav rail + stacked pages + status footer.

The shell is the new top-level window for the app, replacing the legacy
:class:`~tvlinux.control_panel.ControlPanel` main window with a cleaner,
more scalable layout:

- **NavRail** on the left: one vertical button per page. Keyboard-friendly
  (each entry has a visible shortcut), screenreader-friendly (every entry
  has an accessible name), and tiny enough that we don't need to squash
  it on narrow monitors.
- **PageHeader** at the top of the content area: shows the current page's
  title, a short subtitle, and a few page-scoped actions (add region, etc.).
- **QStackedWidget** in the middle: each page is an independently built
  QWidget in :mod:`tvlinux.pages`.
- **StatusFooter** at the bottom: a Hunt Mode indicator (live-updating from
  :class:`HuntModeManager`), a profile chip that pops up Save/Load/Import/
  Export, and a running status string.

The shell re-exposes every signal the old ControlPanel did (regions,
profiles, tray actions). Existing ``Application`` wiring in ``app.py``
therefore stays almost unchanged -- just swap ``ControlPanel(regions)`` for
``ShellWindow(regions, hunt_mode, ...)``.
"""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .audio_timers import AudioTimerManager
from .hunt_history import HuntHistoryStore
from .hunt_mode import HuntModeManager
from .hunt_refresh import HuntRefresher
from .key_listener import PassiveKeyListener
from .pages import (
    AboutPage,
    AudioTimersPage,
    HuntHistoryPage,
    RegionsPage,
    SettingsPage,
)
from .regions import RegionManager
from .theme import TOKENS
from .ui_helpers import default_icon_size, hline, icon, muted_label, pill_button

# -- Page registry -----------------------------------------------------------


PAGES: list[tuple[str, str, str]] = [
    # (key, label, subtitle)
    ("regions", "Regions", "Manage capture regions, colors, and behaviour."),
    ("hunt_history", "Hunt History", "Past hunts, loot split, notes."),
    ("audio_timers", "Audio Timers", "Countdown timers with sounds + global hotkeys."),
    ("settings", "Settings", "Hunt Mode, trigger key, and calibration."),
    ("about", "About", "What this app is and isn't."),
]


# -- Nav rail ----------------------------------------------------------------


class NavRail(QWidget):
    """Vertical list of page buttons.

    Implemented on top of a ``QButtonGroup`` of ``QToolButton``s rather than
    a ``QListWidget`` so each entry can show an icon + label + shortcut
    indicator and still honour the design-system's QSS for toggled state.
    """

    navigated = Signal(str)  # page key

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(184)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            TOKENS.spacing.md, TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.md
        )
        outer.setSpacing(TOKENS.spacing.xs)

        brand = QLabel("TibiaVision", self)
        brand.setStyleSheet(
            f"font-size: {TOKENS.type.size_heading}pt; " f"font-weight: {TOKENS.type.weight_bold};"
        )
        outer.addWidget(brand)
        outer.addWidget(muted_label("Linux edition", self))
        outer.addSpacing(TOKENS.spacing.md)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}

        icon_for = {
            "regions": "layers",
            "hunt_history": "line-chart",
            "audio_timers": "volume-2",
            "settings": "settings",
            "about": "info",
        }
        for key, label, _subtitle in PAGES:
            btn = QToolButton(self)
            btn.setText(f"  {label}")
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.setIcon(icon(icon_for.get(key, "circle")))
            btn.setIconSize(default_icon_size())
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setProperty("role", "nav")
            btn.setSizePolicy(
                btn.sizePolicy().horizontalPolicy(),
                btn.sizePolicy().verticalPolicy(),
            )
            btn.setMinimumHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            outer.addWidget(btn)

        outer.addStretch(1)

    def set_current(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)


# -- Page header -------------------------------------------------------------


class PageHeader(QWidget):
    """Title + subtitle + per-page action slot above the content."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.md, TOKENS.spacing.lg, TOKENS.spacing.md
        )
        layout.setSpacing(TOKENS.spacing.md)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self._title = QLabel("", self)
        self._title.setStyleSheet(
            f"font-size: {TOKENS.type.size_display}pt; " f"font-weight: {TOKENS.type.weight_bold};"
        )
        self._subtitle = QLabel("", self)
        self._subtitle.setProperty("role", "muted")
        text_col.addWidget(self._title)
        text_col.addWidget(self._subtitle)
        layout.addLayout(text_col, 1)

        self._actions_host = QWidget(self)
        self._actions_row = QHBoxLayout(self._actions_host)
        self._actions_row.setContentsMargins(0, 0, 0, 0)
        self._actions_row.setSpacing(TOKENS.spacing.xs)
        layout.addWidget(self._actions_host)

    def set_page(self, title: str, subtitle: str) -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)

    def set_actions(self, buttons: list[QPushButton]) -> None:
        while self._actions_row.count():
            item = self._actions_row.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.setParent(None)
        for b in buttons:
            self._actions_row.addWidget(b)


# -- Status footer -----------------------------------------------------------


class StatusFooter(QWidget):
    """Hunt-mode dot + profile chip + status message."""

    save_profile_requested = Signal(str)
    load_profile_requested = Signal(str)
    delete_profile_requested = Signal(str)
    export_profile_requested = Signal()
    import_profile_requested = Signal()

    def __init__(
        self,
        hunt_mode: HuntModeManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = hunt_mode
        self._profiles: list[str] = []
        self._current_profile: str | None = None
        self.setObjectName("StatusFooter")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            TOKENS.spacing.lg, TOKENS.spacing.sm, TOKENS.spacing.lg, TOKENS.spacing.sm
        )
        layout.setSpacing(TOKENS.spacing.md)

        self._hunt_dot = QLabel("\u25cf", self)
        self._hunt_label = QLabel("Hunt Mode: off", self)
        layout.addWidget(self._hunt_dot)
        layout.addWidget(self._hunt_label)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setProperty("role", "vline")
        layout.addWidget(sep)

        self._profile_chip = QPushButton("Profile: default", self)
        self._profile_chip.setProperty("variant", "ghost")
        self._profile_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self._profile_chip.clicked.connect(self._show_profile_menu)
        layout.addWidget(self._profile_chip)

        layout.addStretch(1)

        self._status = QLabel("", self)
        self._status.setProperty("role", "muted")
        layout.addWidget(self._status)

        self._mode.toggled.connect(lambda _a: self._refresh_hunt_mode())
        self._refresh_hunt_mode()

    def _refresh_hunt_mode(self) -> None:
        on = self._mode.active
        colour = TOKENS.palette.success if on else TOKENS.palette.text_muted
        self._hunt_dot.setStyleSheet(f"color: {colour}; font-size: 18pt;")
        self._hunt_label.setText("Hunt Mode: ON" if on else "Hunt Mode: off")

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_profiles(self, names: list[str], current: str | None) -> None:
        self._profiles = list(names)
        self._current_profile = current
        label = f"Profile: {current}" if current else "Profile: (unsaved)"
        self._profile_chip.setText(label)

    def _show_profile_menu(self) -> None:
        menu = QMenu(self)
        if self._profiles:
            load_menu = menu.addMenu("Load...")
            for name in self._profiles:
                act = QAction(name, load_menu)
                act.triggered.connect(lambda _=False, n=name: self.load_profile_requested.emit(n))
                load_menu.addAction(act)
            delete_menu = menu.addMenu("Delete...")
            for name in self._profiles:
                act = QAction(name, delete_menu)
                act.triggered.connect(lambda _=False, n=name: self._confirm_delete(n))
                delete_menu.addAction(act)
            menu.addSeparator()
        act_save = menu.addAction("Save as...")
        act_save.triggered.connect(self._save_as)
        act_import = menu.addAction("Import...")
        act_import.triggered.connect(self.import_profile_requested.emit)
        act_export = menu.addAction("Export...")
        act_export.triggered.connect(self.export_profile_requested.emit)
        menu.exec(self._profile_chip.mapToGlobal(self._profile_chip.rect().bottomLeft()))

    def _save_as(self) -> None:
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name:")
        if ok and name.strip():
            self.save_profile_requested.emit(name.strip())

    def _confirm_delete(self, name: str) -> None:
        if (
            QMessageBox.question(self, "Delete profile?", f"Delete profile '{name}'?")
            == QMessageBox.StandardButton.Yes
        ):
            self.delete_profile_requested.emit(name)


# -- Shell window ------------------------------------------------------------


class ShellWindow(QMainWindow):
    """New main window. API-compatible with the legacy ControlPanel."""

    # Region signals (forwarded from RegionsPage)
    add_region_requested = Signal()
    delete_region_requested = Signal(UUID)
    rename_region_requested = Signal(UUID, str)
    toggle_visible_requested = Signal(UUID, bool)
    toggle_lock_requested = Signal(UUID, bool)
    toggle_glow_requested = Signal(UUID, bool)
    toggle_grid_requested = Signal(UUID, bool)
    opacity_requested = Signal(UUID, float)
    border_color_requested = Signal(UUID, str)
    corner_radius_requested = Signal(UUID, int)
    toggle_track_cooldown_requested = Signal(UUID, bool)
    toggle_watch_mode_requested = Signal(UUID, str)
    show_all_requested = Signal(bool)
    lock_all_requested = Signal(bool)

    # Profile signals (from status footer)
    save_profile_requested = Signal(str)
    load_profile_requested = Signal(str)
    delete_profile_requested = Signal(str)
    export_profile_requested = Signal()
    import_profile_requested = Signal()

    # Tray / menu shortcuts
    open_audio_timers_requested = Signal()
    donate_requested = Signal()

    def __init__(
        self,
        regions: RegionManager,
        hunt_mode: HuntModeManager,
        refresher: HuntRefresher,
        key_listener: PassiveKeyListener,
        audio_timers: AudioTimerManager,
        hunt_history: HuntHistoryStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._regions = regions
        self._mode = hunt_mode
        self.setWindowTitle("TibiaVision-Linux")
        self.resize(1020, 720)
        self.setMinimumSize(880, 560)

        self._build_chrome(hunt_mode)
        self._build_pages(hunt_mode, refresher, key_listener, audio_timers, hunt_history)
        self._build_header_actions()
        self.nav.set_current("regions")
        self._on_nav("regions")

    # -- UI scaffolding --------------------------------------------------

    def _build_chrome(self, hunt_mode: HuntModeManager) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.nav = NavRail(self)
        self.nav.navigated.connect(self._on_nav)
        body.addWidget(self.nav)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.header = PageHeader(self)
        content_layout.addWidget(self.header)
        content_layout.addWidget(hline(self))

        self.stack = QStackedWidget(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.stack, 1)

        body.addWidget(content, 1)
        root.addLayout(body, 1)

        root.addWidget(hline(self))
        self.footer = StatusFooter(hunt_mode, self)
        root.addWidget(self.footer)

        self.setCentralWidget(central)

        # Forward profile signals.
        self.footer.save_profile_requested.connect(self.save_profile_requested)
        self.footer.load_profile_requested.connect(self.load_profile_requested)
        self.footer.delete_profile_requested.connect(self.delete_profile_requested)
        self.footer.export_profile_requested.connect(self.export_profile_requested)
        self.footer.import_profile_requested.connect(self.import_profile_requested)

    def _build_pages(
        self,
        hunt_mode: HuntModeManager,
        refresher: HuntRefresher,
        key_listener: PassiveKeyListener,
        audio_timers: AudioTimerManager,
        hunt_history: HuntHistoryStore,
    ) -> None:
        self.regions_page = RegionsPage(self._regions, self)
        self.hunt_history_page = HuntHistoryPage(hunt_history, self)
        self.audio_timers_page = AudioTimersPage(audio_timers, self)
        self.settings_page = SettingsPage(hunt_mode, refresher, key_listener, self)
        self.about_page = AboutPage(self)

        self.pages_by_key: dict[str, QWidget] = {
            "regions": self.regions_page,
            "hunt_history": self.hunt_history_page,
            "audio_timers": self.audio_timers_page,
            "settings": self.settings_page,
            "about": self.about_page,
        }
        for key, _label, _subtitle in PAGES:
            self.stack.addWidget(self.pages_by_key[key])

        # Forward region-page signals to the shell surface so app.py can
        # bind them the same way it bound ControlPanel's signals.
        rp = self.regions_page
        rp.add_region_requested.connect(self.add_region_requested)
        rp.delete_region_requested.connect(self.delete_region_requested)
        rp.rename_region_requested.connect(self.rename_region_requested)
        rp.toggle_visible_requested.connect(self.toggle_visible_requested)
        rp.toggle_lock_requested.connect(self.toggle_lock_requested)
        rp.toggle_glow_requested.connect(self.toggle_glow_requested)
        rp.toggle_grid_requested.connect(self.toggle_grid_requested)
        rp.opacity_requested.connect(self.opacity_requested)
        rp.border_color_requested.connect(self.border_color_requested)
        rp.corner_radius_requested.connect(self.corner_radius_requested)
        rp.toggle_track_cooldown_requested.connect(self.toggle_track_cooldown_requested)
        rp.toggle_watch_mode_requested.connect(self.toggle_watch_mode_requested)
        rp.show_all_requested.connect(self.show_all_requested)
        rp.lock_all_requested.connect(self.lock_all_requested)

    def _build_header_actions(self) -> None:
        # Regions page default action set.
        self._region_actions: dict[str, QPushButton] = {}

        def make_action(
            label: str, variant: str = "default", icon_name: str | None = None
        ) -> QPushButton:
            btn = pill_button(label, variant=variant, parent=self)
            if icon_name:
                btn.setIcon(icon(icon_name))
                btn.setIconSize(default_icon_size())
            return btn

        self._region_actions["add"] = make_action("Add region", variant="primary", icon_name="plus")
        self._region_actions["show"] = make_action("Show all", icon_name="eye")
        self._region_actions["hide"] = make_action("Hide all", icon_name="eye-off")
        self._region_actions["lock"] = make_action("Lock all", icon_name="lock")
        self._region_actions["unlock"] = make_action("Unlock all", icon_name="unlock")

        self._region_actions["add"].clicked.connect(self.add_region_requested.emit)
        self._region_actions["show"].clicked.connect(lambda: self.show_all_requested.emit(True))
        self._region_actions["hide"].clicked.connect(lambda: self.show_all_requested.emit(False))
        self._region_actions["lock"].clicked.connect(lambda: self.lock_all_requested.emit(True))
        self._region_actions["unlock"].clicked.connect(lambda: self.lock_all_requested.emit(False))

        # Hunt Mode toggle for hunt history header.
        self._hunt_mode_button = make_action("Hunt Mode: off", variant="ghost")
        self._hunt_mode_button.clicked.connect(lambda: self._mode.toggle())
        self._mode.toggled.connect(self._sync_hunt_mode_button)
        self._sync_hunt_mode_button(self._mode.active)

        self._donate_button = make_action("Donate", icon_name="heart")
        self._donate_button.clicked.connect(self.donate_requested.emit)

    def _sync_hunt_mode_button(self, on: bool) -> None:
        self._hunt_mode_button.setText("Hunt Mode: ON" if on else "Hunt Mode: off")
        self._hunt_mode_button.setProperty("variant", "primary" if on else "ghost")
        self._hunt_mode_button.style().unpolish(self._hunt_mode_button)
        self._hunt_mode_button.style().polish(self._hunt_mode_button)

    # -- Navigation ------------------------------------------------------

    def _on_nav(self, key: str) -> None:
        page = self.pages_by_key.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        title, subtitle = next(
            ((label, subtitle) for k, label, subtitle in PAGES if k == key),
            ("", ""),
        )
        self.header.set_page(title, subtitle)
        self.nav.set_current(key)
        self.header.set_actions(self._actions_for(key))

    def _actions_for(self, key: str) -> list[QPushButton]:
        if key == "regions":
            return [
                self._region_actions["add"],
                self._region_actions["show"],
                self._region_actions["hide"],
                self._region_actions["lock"],
                self._region_actions["unlock"],
            ]
        if key == "hunt_history":
            return [self._hunt_mode_button]
        if key == "about":
            return [self._donate_button]
        return []

    def navigate_to(self, key: str) -> None:
        self._on_nav(key)

    # -- Back-compat surface --------------------------------------------

    def set_status(self, text: str) -> None:
        self.footer.set_status(text)

    def set_profiles(self, names: list[str], current: str | None) -> None:
        self.footer.set_profiles(names, current)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Hide to tray instead of actually quitting; the app lifecycle is
        # owned by the Application object, same as ControlPanel.
        event.ignore()
        self.hide()


__all__ = ["PAGES", "NavRail", "PageHeader", "ShellWindow", "StatusFooter"]
