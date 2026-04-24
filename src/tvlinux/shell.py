"""Shell window: nav rail + stacked pages + status footer.

The shell is the new top-level window for the app, replacing the legacy
:class:`~tvlinux.control_panel.ControlPanel` main window with a cleaner,
more scalable layout:

- **NavRail** on the left: one vertical button per page. Screenreader-friendly
  (every entry has an accessible name) and tiny enough that we don't need to
  squash it on narrow monitors.
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

from PySide6.QtCore import Property, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QPainter, QRadialGradient, QResizeEvent
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
from .motion import cross_fade, hover_ease, lerp, pulse, stop_pulse
from .pages import (
    AboutPage,
    AudioTimersPage,
    CompanionPage,
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
    (
        "companion",
        "Companion",
        "In-app live tiles for every region. Works when Tibia is fullscreen.",
    ),
    ("hunt_history", "Hunt History", "Past hunts, loot split, notes."),
    ("audio_timers", "Audio Timers", "Countdown timers with sounds + global hotkeys."),
    ("settings", "Settings", "Hunt Mode master switch and capture behaviour."),
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
        edition = muted_label("Linux edition", brand_wrap, wrap=False)
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
        for key, label, subtitle in PAGES:
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
            # a11y + keyboard-nav surface. The subtitle is what the
            # PageHeader shows, so re-using it here keeps screen
            # reader output consistent with the visible caption.
            btn.setToolTip(subtitle)
            btn.setAccessibleName(label)
            btn.setAccessibleDescription(subtitle)
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            btn.clicked.connect(lambda _=False, k=key: self.navigated.emit(k))
            # Micro-interaction: drive a dynamic hoverProgress property
            # that QSS can't animate on its own. Under offscreen Qt this
            # collapses to a snap (see motion.reduce_motion) so tests
            # stay deterministic.
            btn.setProperty("hoverProgress", 0.0)
            hover_ease(btn)
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
        self._donate_btn.setAccessibleName("Donate")
        self._donate_btn.setAccessibleDescription("Support development")
        self._donate_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._donate_btn.setProperty("hoverProgress", 0.0)
        hover_ease(self._donate_btn)
        self._donate_btn.clicked.connect(self.donate_clicked)
        outer.addWidget(self._donate_btn)

    def set_current(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def set_compact(self, compact: bool) -> None:
        """Slim the rail down at narrow window widths.

        Trims the fixed width by ~10% and drops outer padding so the
        content column gains back ~24px. Kept conservative so the rail
        still reads as a sidebar, not a toolbar.
        """
        s = TOKENS.spacing
        if compact:
            self.setFixedWidth(196)
            layout = self.layout()
            if layout is not None:
                layout.setContentsMargins(s.sm, s.md, s.sm, s.sm)
        else:
            self.setFixedWidth(220)
            layout = self.layout()
            if layout is not None:
                layout.setContentsMargins(s.md, s.lg, s.md, s.md)


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

    def set_compact(self, compact: bool) -> None:
        """Pull in the outer header margins for narrow widths."""
        s = TOKENS.spacing
        layout = self.layout()
        if layout is None:
            return
        if compact:
            layout.setContentsMargins(s.lg, s.md, s.lg, s.sm)
        else:
            layout.setContentsMargins(s.xl, s.lg, s.xl, s.md)

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


class _HuntDot(QWidget):
    """Neon status dot with an optional breathing pulse.

    Drawn rather than styled so we can animate both the fill opacity
    and a soft outer glow off a single :func:`motion.pulse`. A plain
    ``QLabel`` can't do either without a per-state QSS rewrite.

    The widget exposes ``pulseT`` as a Qt property (0..1) so
    :func:`motion.pulse` can interpolate it via ``QPropertyAnimation``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pulse_t = 1.0
        self._active = False
        self.setFixedSize(22, 22)

    @Property(float)
    def pulseT(self) -> float:
        return self._pulse_t

    @pulseT.setter  # type: ignore[no-redef]
    def pulseT(self, value: float) -> None:
        self._pulse_t = float(value)
        self.update()

    def set_active(self, active: bool) -> None:
        """Flip the dot between muted-grey and pulsing-neon-green."""
        if active == self._active:
            return
        self._active = active
        if active:
            pulse(self, prop=b"pulseT", min_value=0.55, max_value=1.0, period_ms=1600)
        else:
            stop_pulse(self)
            self._pulse_t = 1.0
        self.update()

    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        inner_r = min(rect.width(), rect.height()) / 2.0 - 3.0
        centre = rect.center()

        if self._active:
            base = QColor(TOKENS.palette.success)
            # Outer halo fades in/out with the pulse for a soft breath.
            glow_radius = inner_r + 6.0 + 3.0 * self._pulse_t
            grad = QRadialGradient(centre, glow_radius)
            halo = QColor(base)
            halo.setAlphaF(0.35 * self._pulse_t)
            grad.setColorAt(0.0, halo)
            halo_edge = QColor(base)
            halo_edge.setAlphaF(0.0)
            grad.setColorAt(1.0, halo_edge)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            painter.drawEllipse(centre, glow_radius, glow_radius)

            fill = QColor(base)
            fill.setAlphaF(lerp(0.65, 1.0, self._pulse_t))
            painter.setBrush(fill)
            painter.drawEllipse(centre, inner_r, inner_r)
        else:
            muted = QColor(TOKENS.palette.text_muted)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(muted)
            painter.drawEllipse(centre, inner_r, inner_r)


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

        self._hunt_dot = _HuntDot(self)
        self._hunt_dot.setToolTip("Hunt Mode status indicator")
        self._hunt_dot.setAccessibleName("Hunt Mode indicator")
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
        self._hunt_dot.set_active(on)
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
    hud_visibility_requested = Signal(bool)
    open_hud_layout_editor_requested = Signal()
    mirror_placement_changed = Signal(str)

    def __init__(
        self,
        regions: RegionManager,
        hunt_mode: HuntModeManager,
        audio_timers: AudioTimerManager,
        hunt_history: HuntHistoryStore,
        parent: QWidget | None = None,
        *,
        hud_visible: bool = True,
    ) -> None:
        super().__init__(parent)
        self._regions = regions
        self._mode = hunt_mode
        self._tray_notice_shown = False
        self.setWindowTitle("TibiaVision-Linux")
        self.resize(1020, 720)
        self.setMinimumSize(880, 560)

        self._build_chrome(hunt_mode)
        self._build_pages(hunt_mode, audio_timers, hunt_history, hud_visible=hud_visible)
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
        audio_timers: AudioTimerManager,
        hunt_history: HuntHistoryStore,
        *,
        hud_visible: bool = True,
    ) -> None:
        self.regions_page = RegionsPage(self._regions, self)
        self.companion_page = CompanionPage(self._regions, self)
        self.hunt_history_page = HuntHistoryPage(hunt_history, self)
        self.audio_timers_page = AudioTimersPage(audio_timers, self)
        self.settings_page = SettingsPage(hunt_mode, self, hud_visible=hud_visible)
        self.settings_page.hud_visibility_requested.connect(self.hud_visibility_requested)
        self.settings_page.open_hud_layout_editor_requested.connect(
            self.open_hud_layout_editor_requested
        )
        self.settings_page.mirror_placement_changed.connect(self.mirror_placement_changed)
        self.about_page = AboutPage(self)

        self.pages_by_key: dict[str, QWidget] = {
            "regions": self.regions_page,
            "companion": self.companion_page,
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
            # Icon-only buttons have no visible text, so screen readers
            # and keyboard users see nothing without an accessible name.
            # Mirror the tooltip so Orca announces something meaningful.
            btn.setAccessibleName(tooltip)
            btn.setAccessibleDescription(tooltip)
            # Force strong focus so Tab can reach the bulk-action
            # cluster; Qt defaults to TabFocus only on some platforms.
            btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        # Eased cross-fade between pages. cross_fade() no-ops on same
        # index and snaps under reduce_motion (offscreen Qt / opt-out
        # env), so tests can still observe setCurrentIndex immediately.
        target_index = self.stack.indexOf(page)
        if target_index >= 0:
            cross_fade(self.stack, target_index)
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

    def set_hud_visible(self, visible: bool) -> None:
        """Sync the Settings page checkbox with tray-menu HUD toggles."""
        self.settings_page.set_hud_visible(visible)

    # Below this window width the shell switches into a more compact
    # layout (slimmer nav rail, tighter header margins). Chosen so that
    # the default min-size (880px) just barely fits -- users dragging the
    # window smaller than the default get the benefit automatically, and
    # the transition is invisible near fullscreen.
    _COMPACT_BREAKPOINT = 980

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        compact = event.size().width() < self._COMPACT_BREAKPOINT
        if getattr(self, "_is_compact", None) != compact:
            self._is_compact = compact
            self.nav.set_compact(compact)
            self.header.set_compact(compact)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Hide to tray instead of actually quitting; the app lifecycle is
        # owned by the Application object, same as ControlPanel.
        event.ignore()
        self._maybe_show_tray_notice()
        self.hide()

    def _maybe_show_tray_notice(self) -> None:
        """Explain once that closing just hides to the tray.

        Uses ``QSettings`` so the message shows only on the first close,
        regardless of which page the user was on. We avoid blocking the
        hide() behind a modal so the close feels instant; the notice is
        shown non-modally after the window is already hidden via QTimer.
        """
        if self._tray_notice_shown:
            return
        from PySide6.QtCore import QSettings

        settings = QSettings()
        already = bool(settings.value("ui/tray_close_notice_shown", False, type=bool))
        if already:
            self._tray_notice_shown = True
            return
        settings.setValue("ui/tray_close_notice_shown", True)
        self._tray_notice_shown = True

        box = QMessageBox(self)
        box.setWindowTitle("TibiaVision is still running")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "Closing this window just hides it to the system tray.\n\n"
            "The HUD overlay, audio timers, and global hotkeys keep working "
            "in the background. Use the tray icon to reopen this window, or "
            "choose \u201cQuit\u201d in the tray menu to fully exit."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        # Show non-modally so we don't fight with the hide() call above.
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.show()


__all__ = ["PAGES", "NavRail", "PageHeader", "ShellWindow", "StatusFooter"]
