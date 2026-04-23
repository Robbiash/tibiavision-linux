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
    """Vertical list of page buttons with brand + persistent footer actions.

    Implemented on top of a ``QButtonGroup`` of ``QToolButton``s rather than
    a ``QListWidget`` so each entry can show an icon + label and style its
    :checked state directly through the design-system QSS.

    The rail carries three zones:

    1. **Brand** (top): product name + edition caption.
    2. **Navigation** (middle): one button per page.
    3. **Footer** (bottom): cross-cutting actions that should always be
       reachable (currently just "Donate"; more can be added without
       touching page-level header actions).
    """

    navigated = Signal(str)  # page key
    donate_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(220)

        s = TOKENS.spacing
        t = TOKENS.type

        outer = QVBoxLayout(self)
        outer.setContentsMargins(s.md, s.lg, s.md, s.md)
        outer.setSpacing(s.xs)

        # -- Brand block ---------------------------------------------------
        brand_wrap = QWidget(self)
        brand_wrap.setObjectName("NavBrand")
        brand_col = QVBoxLayout(brand_wrap)
        brand_col.setContentsMargins(s.sm, s.xs, s.sm, s.sm)
        brand_col.setSpacing(2)

        brand = QLabel("TibiaVision", brand_wrap)
        brand.setStyleSheet(
            f"font-size: {t.size_display - 2}pt;"
            f"font-weight: {t.weight_bold};"
            f"color: {TOKENS.palette.text_primary};"
        )
        brand_col.addWidget(brand)
        edition = muted_label("Linux edition", brand_wrap)
        edition.setStyleSheet(
            f"font-size: {t.size_caption}pt;"
            "letter-spacing: 1px;"
            "text-transform: uppercase;"
            f"color: {TOKENS.palette.text_muted};"
        )
        brand_col.addWidget(edition)
        outer.addWidget(brand_wrap)

        # Subtle divider between brand and nav items.
        outer.addSpacing(s.sm)
        outer.addWidget(hline(self))
        outer.addSpacing(s.sm)

        # -- Nav buttons ---------------------------------------------------
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
            btn.setText(label)
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.setIcon(icon(icon_for.get(key, "circle")))
            btn.setIconSize(default_icon_size())
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setProperty("role", "nav")
            btn.setMinimumHeight(38)
            btn.setSizePolicy(
                btn.sizePolicy().horizontalPolicy(),
                btn.sizePolicy().verticalPolicy(),
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            outer.addWidget(btn)

        outer.addStretch(1)

        # -- Persistent footer actions ------------------------------------
        outer.addWidget(hline(self))
        outer.addSpacing(s.xs)

        self._donate_btn = QToolButton(self)
        self._donate_btn.setText("Donate")
        self._donate_btn.setIcon(icon("heart"))
        self._donate_btn.setIconSize(default_icon_size())
        self._donate_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._donate_btn.setProperty("role", "nav-footer")
        self._donate_btn.setMinimumHeight(34)
        self._donate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._donate_btn.setToolTip("Support development")
        self._donate_btn.clicked.connect(self.donate_clicked)
        outer.addWidget(self._donate_btn)

    def set_current(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)


# -- Page header -------------------------------------------------------------


class PageHeader(QWidget):
    """Title + subtitle + per-page action slot above the content.

    Action widgets are *reparented* to the header's action host once and then
    shown/hidden depending on the active page -- never orphaned with
    ``setParent(None)``, which would turn them into stray top-level windows.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        s = TOKENS.spacing

        layout = QHBoxLayout(self)
        layout.setContentsMargins(s.xl, s.lg, s.xl, s.md)
        layout.setSpacing(s.md)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title = QLabel("", self)
        self._title.setStyleSheet(
            f"font-size: {TOKENS.type.size_display}pt; font-weight: {TOKENS.type.weight_bold};"
        )
        self._subtitle = QLabel("", self)
        self._subtitle.setProperty("role", "muted")
        text_col.addWidget(self._title)
        text_col.addWidget(self._subtitle)
        layout.addLayout(text_col, 1)

        self._actions_host = QWidget(self)
        self._actions_row = QHBoxLayout(self._actions_host)
        self._actions_row.setContentsMargins(0, 0, 0, 0)
        self._actions_row.setSpacing(s.xs)
        layout.addWidget(self._actions_host, 0, Qt.AlignmentFlag.AlignRight)

        # Buttons that have ever been registered as actions live here
        # so we can hide the ones we don't currently need instead of
        # re-parenting them to ``None`` (which would spawn top-level
        # windows -- the "floating Donate button" bug).
        self._registered: set[QWidget] = set()

    @property
    def actions_host(self) -> QWidget:
        return self._actions_host

    def set_page(self, title: str, subtitle: str) -> None:
        self._title.setText(title)
        self._subtitle.setText(subtitle)

    def register_action(self, widget: QWidget) -> None:
        """Register ``widget`` as a header action without showing it yet.

        Reparents the widget onto the actions host and hides it so it does
        not appear as an orphan top-level window before ``set_actions``.
        """
        widget.setParent(self._actions_host)
        widget.hide()
        self._registered.add(widget)

    def set_actions(self, widgets: list[QWidget]) -> None:
        # Remove all items from the layout without touching their parents.
        while self._actions_row.count():
            self._actions_row.takeAt(0)
        # Hide everything we've ever registered; we'll re-show just the
        # ones for this page.
        for w in self._registered:
            w.hide()
        for w in widgets:
            if w not in self._registered:
                self.register_action(w)
            w.show()
            self._actions_row.addWidget(w)


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
        self.nav.donate_clicked.connect(self.donate_requested)
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
        # Regions page default action set. The primary action (Add region)
        # keeps its full label; bulk actions collapse to icon-only ghost
        # buttons with tooltips so the header never overflows on narrow
        # windows.
        self._region_actions: dict[str, QPushButton] = {}

        def make_pill(label: str, variant: str, icon_name: str | None) -> QPushButton:
            btn = pill_button(label, variant=variant, parent=self.header.actions_host)
            if icon_name:
                btn.setIcon(icon(icon_name))
                btn.setIconSize(default_icon_size())
            self.header.register_action(btn)
            return btn

        def make_icon_button(icon_name: str, tooltip: str) -> QPushButton:
            btn = QPushButton(self.header.actions_host)
            btn.setProperty("variant", "icon")
            btn.setIcon(icon(icon_name))
            btn.setIconSize(default_icon_size())
            btn.setFixedSize(36, 36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tooltip)
            self.header.register_action(btn)
            return btn

        self._region_actions["add"] = make_pill("Add region", variant="primary", icon_name="plus")
        self._region_actions["show"] = make_icon_button("eye", "Show all regions")
        self._region_actions["hide"] = make_icon_button("eye-off", "Hide all regions")
        self._region_actions["lock"] = make_icon_button("lock", "Lock all regions")
        self._region_actions["unlock"] = make_icon_button("unlock", "Unlock all regions")

        self._region_actions["add"].clicked.connect(self.add_region_requested.emit)
        self._region_actions["show"].clicked.connect(lambda: self.show_all_requested.emit(True))
        self._region_actions["hide"].clicked.connect(lambda: self.show_all_requested.emit(False))
        self._region_actions["lock"].clicked.connect(lambda: self.lock_all_requested.emit(True))
        self._region_actions["unlock"].clicked.connect(lambda: self.lock_all_requested.emit(False))

        # Subtle divider between the primary action and the icon-only
        # bulk-action cluster.
        self._regions_sep = QFrame(self.header.actions_host)
        self._regions_sep.setFrameShape(QFrame.Shape.VLine)
        self._regions_sep.setProperty("role", "vline")
        self._regions_sep.setFixedHeight(24)
        self.header.register_action(self._regions_sep)

        # Hunt Mode toggle for hunt history header.
        self._hunt_mode_button = make_pill("Hunt Mode: off", variant="ghost", icon_name=None)
        self._hunt_mode_button.clicked.connect(lambda: self._mode.toggle())
        self._mode.toggled.connect(self._sync_hunt_mode_button)
        self._sync_hunt_mode_button(self._mode.active)

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

    def _actions_for(self, key: str) -> list[QWidget]:
        if key == "regions":
            return [
                self._region_actions["add"],
                self._regions_sep,
                self._region_actions["show"],
                self._region_actions["hide"],
                self._region_actions["lock"],
                self._region_actions["unlock"],
            ]
        if key == "hunt_history":
            return [self._hunt_mode_button]
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
