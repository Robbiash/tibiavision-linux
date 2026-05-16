"""Top-level ``Application`` object.

Owns the instance graph:

- ``RegionManager`` (model)
- ``ProfileManager`` (persistence on top of RegionManager)
- ``CaptureCore`` (portal + GStreamer pipeline)
- ``ControlPanel`` (UI)
- one ``MirrorWindow`` per region (created lazily)
- ``AudioTimerManager`` + its dialog
- ``GlobalShortcutManager`` (profile cycle + audio timer hotkeys)
- ``QSystemTrayIcon`` (so closing the control panel doesn't quit the app)

All the cross-object wiring lives here; individual modules stay unaware of each other.
"""

from __future__ import annotations

import contextlib
import json
import traceback
from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QImage, QWindow
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import __app_name__, app_icon_path
from .analyzers import AnalyzerHub, CooldownAnalyzer, EventKind, PixelWatchAnalyzer, PresetWatcher
from .audio_timers import AudioTimer, AudioTimerManager
from .capture import CaptureCore
from .clipboard_watcher import ClipboardWatcher
from .donate_dialog import DonateDialog
from .hud_panels import (
    AudioTimerPanel,
    HuntStatsPanel,
    PartyPanel,
)
from .hunt_history import HuntHistoryStore, HuntRecord
from .hunt_mode import HuntModeManager
from .layer_shell import is_available as layer_shell_available
from .layer_shell import promote_to_overlay as layer_shell_promote
from .logging_config import get_logger
from .mirror_window import MirrorWindow
from .pages.settings_page import load_mirror_placement, load_mirror_safety_raise_enabled
from .paths import triggers_path
from .profiles import ProfileManager
from .region_picker import FloatingRegionPicker
from .regions import Region, RegionManager
from .shell import ShellWindow
from .shortcuts import GlobalShortcutManager, ShortcutSpec
from .smart_hud import SmartHud
from .snap import SNAP_THRESHOLD, MirrorGroupManager, compute_snap
from .theme import apply as apply_theme
from .tibia_reborn_server import TibiaRebornServer
from .trigger_engine import TriggerEngine, default_rules

log = get_logger(__name__)


def _debug_log(
    *,
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
) -> None:
    _ = (run_id, hypothesis_id, location, message, data)
    return


class Application(QObject):
    """Glue object. Not a QApplication subclass - we just hold references."""

    quit_requested = Signal()

    def __init__(
        self,
        *,
        use_portal: bool = True,
        overlay_backend: str = "XWayland (override-redirect)",
    ) -> None:
        super().__init__()
        self._use_portal = use_portal
        # Free-form label for the "Overlay mode" status line in the
        # control panel. Set by ``__main__`` based on the chosen Qt
        # platform plugin and whether layer-shell negotiation succeeded.
        # Purely informational -- the actual always-on-top mechanism is
        # decided in :mod:`tvlinux.mirror_window` from the live QPA name,
        # not this string.
        self._overlay_backend = overlay_backend

        self._regions = RegionManager(self)
        self._profiles = ProfileManager(self._regions)
        self._audio = AudioTimerManager(parent=self)
        self._shortcuts = GlobalShortcutManager(self)

        self._mirrors: dict[UUID, MirrorWindow] = {}
        self._groups = MirrorGroupManager()
        self._last_frame: QImage | None = None
        self._picker: FloatingRegionPicker | None = None
        self._debug_first_frame_logged = False
        self._debug_frame_counter = 0
        self._debug_heartbeat_count = 0
        # Mirror placement policy from settings. Companion mode has been
        # retired from the shell UI, so this effectively stays "floating"
        # in normal operation.
        self._mirror_placement: str = load_mirror_placement()
        # Region ids whose mirror should be raised above whatever the
        # compositor promotes when the FloatingRegionPicker unmaps.
        # Populated just before :meth:`RegionManager.add` so the
        # :meth:`_create_mirror` slot can tell "just drawn via the
        # picker" apart from "loaded from disk at startup".
        self._pending_raise_ids: set[UUID] = set()
        # Mirror ids that were successfully promoted to a wlr-layer-shell
        # overlay surface. Those surfaces sit above fullscreen windows
        # by protocol; the reactive re-raise and safety-net timer skip
        # them (it's wasted work -- the compositor is already doing what
        # we wanted).
        self._layer_shell_promoted: set[UUID] = set()

        # Hunt Mode foundation. The app never interacts with Tibia: the
        # clipboard watcher and history auto-logger only react when the
        # user themselves presses Tibia's "Copy to clipboard" menu entry.
        self._hunt_mode = HuntModeManager(parent=self)
        self._hunt_history = HuntHistoryStore()

        self._reborn_server = TibiaRebornServer(self)

        self._control = ShellWindow(
            regions=self._regions,
            hunt_mode=self._hunt_mode,
            audio_timers=self._audio,
            hunt_history=self._hunt_history,
            reborn_server=self._reborn_server,
            hud_visible=True,
        )
        self._capture = CaptureCore(use_portal=use_portal, parent=self)

        # v2 analyzer hub. Pre-wired so enabling analyzers in v2 is a one-liner here:
        #     self._hub.register(OCRAnalyzer())
        # The hub is only driven when at least one analyzer is registered, so it costs
        # nothing in v1.
        self._hub = AnalyzerHub(self)
        self._capture.frame_buffer_ready.connect(
            lambda arr: self._hub.on_frame_buffer(arr, self._capture.source_size)
        )
        # Only run the per-frame numpy conversion when there's actually an analyzer
        # listening. Default off; flipped on/off by hub registrations.
        self._hub.active_changed.connect(self._on_hub_active_changed)

        # Trigger engine: subscribes to the bus and runs rule actions (the main
        # one out of the box being "LOGIN_DETECTED -> switch_profile"). Rules
        # come from ``triggers.json`` if the user has authored them, otherwise
        # we install the defaults.
        self._triggers = TriggerEngine(
            bus=self._hub,
            profiles=self._profiles,
            parent=self,
        )
        if not self._triggers.load_from_file(triggers_path()):
            self._triggers.set_rules(default_rules())
        self._triggers.rule_fired.connect(self._on_rule_fired)

        # Phase 4 - TibiaCompanion Intelligence Layer. Each of these plugs
        # into the existing ``AnalyzerHub`` (a.k.a. EventBus); no special
        # orchestration needed here.
        self._preset_watcher = PresetWatcher(self._hub, parent=self)
        self._pixel_watch = PixelWatchAnalyzer(self._regions)
        self._cooldown_watch = CooldownAnalyzer(self._regions)
        self._hub.register(self._pixel_watch)
        self._hub.register(self._cooldown_watch)
        self._clipboard_watcher = ClipboardWatcher(
            self._hub, hunt_mode=self._hunt_mode, parent=self
        )
        self._hub.subscribe(EventKind.SPELL_COOLDOWN_SOON, self._on_spell_cooldown_soon)
        # Auto-log captured hunts to the history store when the user has
        # opted in via ``auto_log_to_history`` (default: on). The store
        # writes to disk so the history survives restarts.
        self._clipboard_watcher.hunt_captured.connect(self._on_hunt_captured)
        self._clipboard_watcher.hunt_ignored_while_off.connect(self._on_hunt_ignored_while_off)

        # Keep the tray checkbox in sync with the master Hunt Mode toggle.
        self._hunt_mode.toggled.connect(self._on_hunt_mode_toggled)

        # Smart HUD: strictly click-through overlay that hosts pluggable
        # HudPanel instances. Adding a new panel is a single file + one
        # register_panel() call below; SmartHud itself stays untouched.
        self._hud = SmartHud(bus=self._hub, parent=None)
        self._hud.register_panel(AudioTimerPanel(self._audio))
        self._hud.register_panel(HuntStatsPanel(self._hub))
        self._hud.register_panel(PartyPanel(self._hub))

        self._build_tray()
        self._wire_signals()
        self._refresh_profiles_ui()

        apply_theme(QApplication.instance())

        # Reactive "stay above Tibia" logic. KDE Plasma Wayland (and most
        # other compositors) restacks the focused client on top when the
        # user clicks into Tibia, burying our mirrors despite
        # ``WindowStaysOnTopHint``. ``focusWindowChanged`` fires with
        # ``None`` the instant our app loses focus -- that's the cue to
        # push every visible mirror back up. ``applicationStateChanged``
        # covers the alt-tab-back-in case. Both signals live on
        # :class:`QGuiApplication`; ``.instance()`` is statically typed
        # as ``QCoreApplication``, so we cast to the concrete subclass
        # we actually constructed in ``__main__.main``.
        gapp = cast(QGuiApplication, QGuiApplication.instance())
        if gapp is not None:
            gapp.focusWindowChanged.connect(self._on_focus_window_changed)
            gapp.applicationStateChanged.connect(self._on_app_state_changed)

        # Low-frequency safety-net. Catches compositor state drift the
        # focus-change path misses (e.g. a fullscreen toggle that doesn't
        # route through the normal focus handoff). Only fires while some
        # *other* application holds focus, so it never interrupts user
        # interaction with our own windows. See
        # :func:`tvlinux.pages.settings_page.load_mirror_safety_raise_enabled`
        # for the QSettings-backed off switch.
        self._safety_raise_timer = QTimer(self)
        self._safety_raise_timer.setInterval(1500)
        self._safety_raise_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._safety_raise_timer.timeout.connect(self._safety_raise_tick)
        self._debug_heartbeat_timer = QTimer(self)
        self._debug_heartbeat_timer.setInterval(1000)
        self._debug_heartbeat_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._debug_heartbeat_timer.timeout.connect(self._debug_heartbeat_tick)

    # -- Wiring -----------------------------------------------------------------------

    def _wire_signals(self) -> None:
        # Capture -> UI
        self._capture.started.connect(self._on_capture_started)
        self._capture.errored.connect(self._on_capture_error)
        self._capture.frame_ready.connect(self._on_frame)
        self._capture.size_changed.connect(self._on_source_size_changed)

        # Control panel -> Application
        self._control.add_region_requested.connect(self._open_region_picker)
        self._control.delete_region_requested.connect(self._delete_region)
        self._control.rename_region_requested.connect(self._rename_region)
        self._control.toggle_visible_requested.connect(self._set_visible)
        self._control.toggle_lock_requested.connect(self._set_locked)
        self._control.toggle_glow_requested.connect(self._set_glow)
        self._control.toggle_grid_requested.connect(self._set_grid)
        self._control.opacity_requested.connect(self._set_opacity)
        self._control.border_color_requested.connect(self._set_border_color)
        self._control.corner_radius_requested.connect(self._set_corner_radius)
        self._control.toggle_track_cooldown_requested.connect(self._set_track_cooldown)
        self._control.cooldown_spell_requested.connect(self._set_cooldown_spell)
        self._control.show_all_requested.connect(self._regions.set_all_visible)
        self._control.lock_all_requested.connect(self._set_all_locked)
        self._control.save_profile_requested.connect(self._save_profile)
        self._control.load_profile_requested.connect(self._load_profile)
        self._control.delete_profile_requested.connect(self._delete_profile)
        self._control.import_profile_requested.connect(self._import_profile)
        self._control.export_profile_requested.connect(self._export_profile)
        self._control.open_audio_timers_requested.connect(self._open_audio_timers)
        self._control.donate_requested.connect(self._show_donate)
        self._control.toggle_watch_mode_requested.connect(self._set_watch_mode)
        self._control.hud_visibility_requested.connect(self._on_hud_visibility_from_settings)
        self._control.open_hud_layout_editor_requested.connect(self._open_hud_layout_editor)
        self._control.mirror_placement_changed.connect(self._on_mirror_placement_changed)

        # Region model -> mirrors
        self._regions.region_added.connect(self._create_mirror)
        self._regions.region_removed.connect(self._destroy_mirror)
        self._regions.region_changed.connect(self._update_mirror)
        self._regions.regions_reset.connect(self._rebuild_mirrors)

    def _on_hub_active_changed(self, active: bool) -> None:
        self._capture.buffer_output_enabled = active
        log.debug("capture.buffer_output", enabled=active)

    def _on_rule_fired(self, rule_id: str) -> None:
        """Refresh UI after a trigger-driven action (profile switch etc.).

        Rules can mutate ``ProfileManager`` (e.g. ``switch_profile``). The
        control panel caches the active-profile name in its profile list, so
        we nudge it to re-read after every rule firing. Cheap, and avoids
        having to teach the engine about UI internals.
        """
        self._refresh_profiles_ui()
        self._control.set_status(f"Trigger fired: {rule_id}")

    def _build_tray(self) -> None:
        icon = QIcon(app_icon_path())
        if icon.isNull():
            icon = QIcon.fromTheme("video-display", QIcon())
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(__app_name__)
        menu = QMenu()
        act_show = QAction("Show control panel", menu)
        act_show.triggered.connect(self._show_control)

        self._act_hunt_mode = QAction("Hunt Mode", menu)
        self._act_hunt_mode.setCheckable(True)
        self._act_hunt_mode.setChecked(self._hunt_mode.active)
        self._act_hunt_mode.toggled.connect(self._hunt_mode.set_active)

        act_audio = QAction("Audio timers...", menu)
        act_audio.triggered.connect(self._open_audio_timers)
        act_cycle = QAction("Cycle profile", menu)
        act_cycle.triggered.connect(self._cycle_profile)
        self._act_toggle_hud = QAction("Show HUD overlay", menu)
        self._act_toggle_hud.setCheckable(True)
        self._act_toggle_hud.setChecked(True)
        self._act_toggle_hud.toggled.connect(self._set_hud_visible)
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(self._act_hunt_mode)
        menu.addSeparator()
        menu.addAction(act_audio)
        menu.addAction(self._act_toggle_hud)
        menu.addSeparator()
        menu.addAction(act_cycle)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: (
                self._show_control() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    # -- Lifecycle --------------------------------------------------------------------

    def start(self) -> None:
        self._control.set_status("Requesting capture via XDG ScreenCast portal...")
        self._control.set_overlay_backend(self._overlay_backend)
        log.info("overlay.backend_label", label=self._overlay_backend)
        self._control.show()
        self._hud.show()
        self._capture.start()
        self._register_shortcuts()
        self._debug_heartbeat_timer.start()
        # The reactive / safety-net raise logic only helps on Wayland
        # xdg-toplevel surfaces where the compositor gets to pick Z-order.
        # When the default xcb backend is in use, mirrors are X11
        # override-redirect windows (bypass-WM) -- KWin cannot re-stack
        # them, so raising them on every focus change or on a 1.5 s tick
        # is pure no-op churn. We disable the timer in that case. The
        # ``focusWindowChanged`` / ``applicationStateChanged`` hooks stay
        # connected because they're cheap and still useful under
        # ``--force-wayland``.
        if load_mirror_safety_raise_enabled() and not self._overlay_backend.startswith("XWayland"):
            self._safety_raise_timer.start()

    def _set_hud_visible(self, visible: bool) -> None:
        """Tray-menu toggle for the Smart HUD overlay."""
        if visible:
            self._hud.show()
        else:
            self._hud.hide()
        # Keep the Settings-page checkbox in sync with the tray menu.
        self._control.set_hud_visible(visible)

    def _on_hud_visibility_from_settings(self, visible: bool) -> None:
        """Settings-page toggle for the Smart HUD overlay.

        Also updates the tray-menu checkbox so both surfaces stay in sync.
        """
        if visible:
            self._hud.show()
        else:
            self._hud.hide()
        if hasattr(self, "_act_toggle_hud"):
            self._act_toggle_hud.blockSignals(True)
            self._act_toggle_hud.setChecked(visible)
            self._act_toggle_hud.blockSignals(False)

    def _open_hud_layout_editor(self) -> None:
        """Spawn (or focus) the HUD layout editor companion window.

        Delegates to :meth:`SmartHud.open_layout_editor`, which handles
        idempotency and lifetime. We parent the editor to the main
        shell so closing the app cleans it up automatically.
        """
        self._hud.open_layout_editor(parent=self._control)

    def _quit(self) -> None:
        self._profiles.save_to_disk()
        self._capture.stop()
        self._shortcuts.stop()
        self._reborn_server.stop()
        QApplication.quit()

    # -- Capture feedback -------------------------------------------------------------

    def _on_capture_started(self) -> None:
        self._control.set_status(
            f"Capturing source {self._capture.source_size.width()}x"
            f"{self._capture.source_size.height()}"
        )

    # Rough heuristics that turn the lower-level capture error strings into
    # plain-language recovery guidance for beginners. The technical message
    # is still shown under "Details" so power users (and bug reports) keep
    # the original text.
    _CAPTURE_ERROR_HINTS: tuple[tuple[str, str, str], ...] = (
        (
            "portal",
            "Your desktop didn't grant screen capture.",
            "When the portal dialog appeared you may have cancelled or picked the "
            "wrong window. Click the tray icon \u2192 \u201cReopen capture\u201d (or "
            "restart the app) and choose the Tibia window in the picker.",
        ),
        (
            "pipewiresrc",
            "The PipeWire capture plugin is missing.",
            "TibiaVision reads pixels through GStreamer's PipeWire plugin. "
            "Install it for your distro and restart the app:\n\n"
            "  \u2022 Fedora Workstation:\n"
            "      sudo dnf install gstreamer1-plugin-pipewire\n"
            "  \u2022 Bazzite / Silverblue / Kinoite (immutable):\n"
            "      rpm-ostree install gstreamer1-plugin-pipewire\n"
            "      systemctl reboot\n"
            "  \u2022 Arch:\n"
            "      sudo pacman -S gst-plugin-pipewire\n"
            "  \u2022 Debian / Ubuntu:\n"
            "      sudo apt install gstreamer1.0-pipewire",
        ),
        (
            "gstreamer",
            "GStreamer failed to start.",
            "TibiaVision uses GStreamer to receive frames from the portal. Make "
            "sure your desktop session is Wayland (or XWayland + KDE/GNOME) and "
            "that GStreamer is installed, then restart the app.",
        ),
        (
            "--no-portal",
            "Screen capture is turned off.",
            "You launched TibiaVision with the --no-portal flag, which disables "
            "live capture. Quit the app and relaunch it without that flag.",
        ),
    )

    def _on_capture_error(self, msg: str) -> None:
        self._control.set_status(f"Capture error: {msg}")
        title = "Screen capture stopped"
        body = (
            "TibiaVision couldn't receive frames from the portal. The HUD "
            "and mirrors will stay frozen on the last image until capture "
            "resumes."
        )
        lowered = msg.lower()
        for needle, friendly_title, hint in self._CAPTURE_ERROR_HINTS:
            if needle in lowered:
                title = friendly_title
                body = hint
                break
        box = QMessageBox(self._control)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(body)
        box.setDetailedText(f"Technical details:\n{msg}")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_source_size_changed(self, _size) -> None:  # type: ignore[no-untyped-def]
        self._on_capture_started()

    def _on_frame(self, image: QImage) -> None:
        self._last_frame = image
        self._debug_frame_counter += 1
        if not self._debug_first_frame_logged:
            self._debug_first_frame_logged = True
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H10",
                location="app.py:_on_frame:first_frame",
                message="app_received_first_frame",
                data={
                    "width": int(image.width()),
                    "height": int(image.height()),
                    "mirror_count": len(self._mirrors),
                    "picker_visible": bool(self._picker.isVisible())
                    if self._picker is not None
                    else False,
                },
            )
            # endregion
        elif self._debug_frame_counter % 240 == 0:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H41",
                location="app.py:_on_frame:heartbeat",
                message="frame_heartbeat",
                data={
                    "frame_count": int(self._debug_frame_counter),
                    "width": int(image.width()),
                    "height": int(image.height()),
                    "mirror_count": len(self._mirrors),
                    "picker_visible": bool(self._picker.isVisible())
                    if self._picker is not None
                    else False,
                },
            )
            # endregion
        for mirror in self._mirrors.values():
            mirror.set_frame(image)
        if self._picker is not None and self._picker.isVisible():
            self._picker.set_frame(image)

    # -- Region CRUD actions ---------------------------------------------------------

    def _open_region_picker(self) -> None:
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H6",
            location="app.py:_open_region_picker:entry",
            message="open_region_picker_requested",
            data={
                "has_last_frame": self._last_frame is not None,
                "picker_exists": self._picker is not None,
                "picker_visible": bool(self._picker.isVisible())
                if self._picker is not None
                else False,
                "region_count": len(self._regions),
            },
        )
        # endregion
        if self._last_frame is None:
            QMessageBox.information(
                self._control,
                "No capture yet",
                "Wait for the capture session to start before adding regions.",
            )
            return
        # Re-entry: if a picker is already showing, bring it to the
        # front rather than spawning a second one.
        if self._picker is not None and self._picker.isVisible():
            self._picker.raise_()
            self._picker.activateWindow()
            return

        picker = FloatingRegionPicker(parent=None)
        picker.set_frame(self._last_frame)
        picker.size_to_source()
        picker.region_accepted.connect(self._create_region_from_rect)
        picker.cancelled.connect(self._clear_picker)
        picker.destroyed.connect(lambda *_: self._clear_picker())
        self._picker = picker
        picker.show()

    def _create_region_from_rect(self, rect: QRect) -> None:
        """Persist a region captured from the floating picker.

        Called with a source-stream rectangle; the picker closes itself
        right after emitting the signal, so all we need to do is make
        sure the region is valid and reach the same save path the old
        modal dialog used.
        """
        if not rect.isValid() or rect.isEmpty():
            return
        region = Region(
            name=f"Region {len(self._regions) + 1}",
            rect=rect,
            # Freshly-created gameplay overlays should be immediately
            # non-intrusive: locked mirrors are click-through and don't
            # request keyboard focus. Users can still unlock to reposition.
            locked=True,
        )
        # Tag the id before emitting region_added so _create_mirror
        # (direct signal connection, runs synchronously inside .add)
        # knows to raise the new window above Tibia.
        self._pending_raise_ids.add(region.id)
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H6",
            location="app.py:_create_region_from_rect:before_add",
            message="region_from_picker_ready",
            data={
                "region_id": str(region.id),
                "rect_w": int(rect.width()),
                "rect_h": int(rect.height()),
                "rect_x": int(rect.x()),
                "rect_y": int(rect.y()),
            },
        )
        # endregion
        self._regions.add(region)
        self._profiles.save_to_disk()

    def _clear_picker(self) -> None:
        """Drop our reference so the next Add region spawns a fresh picker."""
        self._picker = None

    def _delete_region(self, region_id: UUID) -> None:
        self._regions.remove(region_id)
        self._profiles.save_to_disk()

    def _rename_region(self, region_id: UUID, new_name: str) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.name = new_name
        self._regions.update(r)
        self._profiles.save_to_disk()

    def _set_visible(self, region_id: UUID, visible: bool) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.visible = visible
        self._regions.update(r)

    def _set_locked(self, region_id: UUID, locked: bool) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H16",
            location="app.py:_set_locked:request",
            message="set_locked_requested",
            data={
                "region_id": str(region_id),
                "prev_locked": bool(r.locked),
                "next_locked": bool(locked),
                "has_geometry": r.geometry is not None,
            },
        )
        # endregion
        r.locked = locked
        self._regions.update(r)

    def _set_all_locked(self, locked: bool) -> None:
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H21",
            location="app.py:_set_all_locked:request",
            message="set_all_locked_requested",
            data={
                "locked": bool(locked),
                "region_count": len(self._regions),
                "mirror_count": len(self._mirrors),
            },
        )
        # endregion
        self._regions.set_all_locked(locked)

    def _set_glow(self, region_id: UUID, on: bool) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.border_glow = on
        self._regions.update(r)

    def _set_grid(self, region_id: UUID, on: bool) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.grid = on
        self._regions.update(r)

    def _set_opacity(self, region_id: UUID, value: float) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.opacity = value
        self._regions.update(r)

    def _set_border_color(self, region_id: UUID, hex_color: str) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.border_color = hex_color
        self._regions.update(r)

    def _set_corner_radius(self, region_id: UUID, radius: int) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.corner_radius = int(radius)
        self._regions.update(r)

    def _set_track_cooldown(self, region_id: UUID, on: bool) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.track_cooldown = bool(on)
        if not r.track_cooldown:
            r.cooldown_spell = "off"
        elif r.cooldown_spell == "off":
            # Back-compat path for older UI surfaces that only send the bool.
            r.cooldown_spell = "exori_gran"
        self._regions.update(r)

    def _set_cooldown_spell(self, region_id: UUID, spell: str) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        if spell not in {"off", "exori_gran", "executors_throw"}:
            spell = "off"
        r.cooldown_spell = spell  # type: ignore[assignment]
        r.track_cooldown = spell != "off"
        self._regions.update(r)

    def _on_spell_cooldown_soon(self, event) -> None:  # type: ignore[no-untyped-def]
        region_id_raw = event.data.get("region_id")
        if region_id_raw is None:
            return
        try:
            region_id = UUID(str(region_id_raw))
        except (ValueError, TypeError):
            return
        mirror = self._mirrors.get(region_id)
        if mirror is not None and mirror.isVisible():
            mirror.trigger_cooldown_alert()
        name = str(event.data.get("name") or "Spell")
        remaining = float(event.data.get("remaining_s") or 0.0)
        self._control.set_status(f"{name}: cooldown almost ready ({remaining:.1f}s)")

    def _set_watch_mode(self, region_id: UUID, mode: str) -> None:
        r = self._regions.get(region_id)
        if r is None:
            return
        r.watch_mode = "change" if mode == "change" else "off"
        self._regions.update(r)
        self._profiles.save_to_disk()

    # -- Phase 5 -- Hunt Mode glue --------------------------------------------

    def _on_hunt_captured(self, session, raw_text: str) -> None:  # type: ignore[no-untyped-def]
        """Append a freshly-captured hunt to the persistent history store."""
        if not self._hunt_mode.config.auto_log_to_history:
            return
        try:
            rec = HuntRecord.from_session(session, raw_text=raw_text)
        except (TypeError, AttributeError):
            return
        self._hunt_history.add(rec)
        self._control.hunt_history_page.refresh()
        self._control.set_status(
            f"Captured hunt: profit {rec.balance} gp ({rec.session_sec // 60}m session)"
        )

    def _on_hunt_mode_toggled(self, active: bool) -> None:
        """Keep the tray checkbox in sync with the master Hunt Mode flag."""
        if hasattr(self, "_act_hunt_mode"):
            self._act_hunt_mode.setChecked(active)

    def _on_hunt_ignored_while_off(self) -> None:
        """Show a friendly nudge when a Tibia paste arrived but Hunt Mode is off.

        Writes to the status-footer label instead of popping a dialog, so
        the hint is visible but non-intrusive. Tray notification is used
        only when the main window is hidden, so a user who has the window
        minimised still gets the feedback.
        """
        msg = "Clipboard ignored: Hunt Mode is OFF. Toggle it on to capture hunts."
        self._control.set_status(msg)
        if hasattr(self, "_tray") and not self._control.isVisible():
            # Some desktops silently reject tray notifications; never crash
            # on a best-effort nudge.
            with contextlib.suppress(Exception):
                self._tray.showMessage(
                    "Hunt Mode is OFF",
                    "TibiaVision saw a Tibia clipboard copy but ignored it "
                    "because Hunt Mode is currently off.",
                    msecs=4000,
                )

    # -- Mirror lifecycle -------------------------------------------------------------

    def _floating_mirrors_enabled(self) -> bool:
        """Should freshly-spawned mirrors actually appear on screen?"""
        return self._mirror_placement in {"floating", "both"}

    def _on_mirror_placement_changed(self, mode: str) -> None:
        """React to the Settings dropdown: show/hide all floating mirrors in place."""
        self._mirror_placement = mode
        show = self._floating_mirrors_enabled()
        for region_id, mirror in self._mirrors.items():
            region = self._regions.get(region_id)
            if region is None:
                continue
            if show and region.visible and not mirror.isVisible():
                mirror.show()
            elif not show and mirror.isVisible():
                mirror.hide()

    def _create_mirror(self, region: Region) -> None:
        if region.id in self._mirrors:
            return
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H3",
            location="app.py:_create_mirror:entry",
            message="creating_mirror",
            data={
                "region_id": str(region.id),
                "visible": bool(region.visible),
                "locked": bool(region.locked),
                "has_geometry": region.geometry is not None,
            },
        )
        # endregion
        mirror = MirrorWindow(region)
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H3",
            location="app.py:_create_mirror:after_ctor",
            message="mirror_ctor_returned",
            data={
                "region_id": str(region.id),
                "is_visible": bool(mirror.isVisible()),
                "window_title": mirror.windowTitle(),
            },
        )
        # endregion
        mirror.rename_requested.connect(self._rename_region)
        mirror.delete_requested.connect(self._delete_region)
        mirror.region_updated.connect(self._on_mirror_region_updated)
        mirror.moved.connect(self._on_mirror_moved)
        mirror.unlink_requested.connect(self._on_unlink_requested)
        self._mirrors[region.id] = mirror
        if region.visible and self._floating_mirrors_enabled():
            # Try to promote to a wlr-layer-shell overlay BEFORE the
            # first show(). The surface role is committed on the first
            # commit; once committed it's permanent, so late-binding
            # is not an option. Promotion is a no-op on non-Wayland
            # sessions or where the library isn't loadable, in which
            # case we fall through to the reactive re-raise path.
            promoted = False
            if layer_shell_available():
                promoted = layer_shell_promote(mirror)
                if promoted:
                    self._layer_shell_promoted.add(region.id)
                    log.info("mirror.layer_shell_promoted", region_id=str(region.id))
                else:
                    log.warning("mirror.layer_shell_promotion_failed", region_id=str(region.id))
            else:
                log.debug("mirror.layer_shell_unavailable_fallback_to_reraise")
            mirror.show()
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H3",
                location="app.py:_create_mirror:after_show",
                message="mirror_show_completed",
                data={
                    "region_id": str(region.id),
                    "promoted": bool(promoted),
                    "pending_raise": region.id in self._pending_raise_ids,
                    "is_visible": bool(mirror.isVisible()),
                },
            )
            # endregion
            # Only run the 3-stage raise on surfaces that need it.
            # Layer-shell overlays outrank fullscreen windows by design;
            # raising them is cosmetic at best and potentially steals a
            # frame from another overlay app (notifications, etc.).
            if not promoted and region.id in self._pending_raise_ids:
                self._raise_mirror_above_game(region.id)
        if self._last_frame is not None:
            mirror.set_frame(self._last_frame)

    def _reraise_sequence(self, mirror: MirrorWindow) -> None:
        """Run the three-stage raise dance on a single mirror.

        Fires immediately, on the next event-loop turn, and again after
        250 ms. The staggered retries cover compositors (Mutter on NVIDIA
        in particular) that defer focus/stacking a frame or two, as well
        as the KWin-on-Plasma case where a freshly-focused Tibia briefly
        wins the Z-order before the compositor applies our `_NET_WM_STATE_ABOVE`
        hint.

        Critically this uses ``raise_()`` only and never
        ``activateWindow()``. Activating a mirror steals keyboard focus
        from Tibia and breaks gameplay (WASD / hotkeys start going to us
        instead of the game). We only need Z-order, not activation.

        ``mirror`` is captured by value into the deferred closures so that
        a regions reset or delete between stages can't raise a now-destroyed
        widget; each closure re-checks ownership + visibility first.
        """
        if mirror is None:
            return
        debug_region_id = str(getattr(mirror, "region_id", "unknown"))
        seq_id = int(getattr(self, "_debug_reraise_seq", 0)) + 1
        self._debug_reraise_seq = seq_id
        pending = int(getattr(self, "_debug_reraise_pending", 0)) + 2
        self._debug_reraise_pending = pending
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H43",
            location="app.py:_reraise_sequence:schedule",
            message="reraise_sequence_scheduled",
            data={
                "seq_id": seq_id,
                "region_id": debug_region_id,
                "pending_callbacks": pending,
            },
        )
        # endregion
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H20",
            location="app.py:_reraise_sequence:immediate",
            message="mirror_reraise_immediate",
            data={
                "region_id": debug_region_id,
                "is_visible": bool(mirror.isVisible()),
            },
        )
        # endregion
        mirror.raise_()
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H33",
            location="app.py:_reraise_sequence:immediate_after_raise",
            message="mirror_reraise_immediate_returned",
            data={
                "region_id": debug_region_id,
                "is_visible": bool(mirror.isVisible()),
            },
        )
        # endregion

        def _still_live() -> MirrorWindow | None:
            # Re-fetch by identity so we never touch a mirror the region
            # manager has since torn down. O(N) is fine: N is dozens at most.
            for m in self._mirrors.values():
                if m is mirror:
                    return m if m.isVisible() else None
            return None

        def _reraise() -> None:
            m = _still_live()
            if m is None:
                pending_now = max(0, int(getattr(self, "_debug_reraise_pending", 0)) - 1)
                self._debug_reraise_pending = pending_now
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H43",
                    location="app.py:_reraise_sequence:single_shot_0_done",
                    message="reraise_zero_ms_callback_completed",
                    data={
                        "seq_id": seq_id,
                        "region_id": debug_region_id,
                        "mirror_live": False,
                        "pending_callbacks": pending_now,
                    },
                )
                # endregion
                return
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H20",
                location="app.py:_reraise_sequence:single_shot_0",
                message="mirror_reraise_zero_ms",
                data={
                    "region_id": debug_region_id,
                    "is_visible": bool(m.isVisible()),
                },
            )
            # endregion
            m.raise_()
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H33",
                location="app.py:_reraise_sequence:single_shot_0_after_raise",
                message="mirror_reraise_zero_ms_returned",
                data={
                    "region_id": debug_region_id,
                    "is_visible": bool(m.isVisible()),
                },
            )
            # endregion
            pending_now = max(0, int(getattr(self, "_debug_reraise_pending", 0)) - 1)
            self._debug_reraise_pending = pending_now
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H43",
                location="app.py:_reraise_sequence:single_shot_0_done",
                message="reraise_zero_ms_callback_completed",
                data={
                    "seq_id": seq_id,
                    "region_id": debug_region_id,
                    "mirror_live": True,
                    "pending_callbacks": pending_now,
                },
            )
            # endregion

        QTimer.singleShot(0, _reraise)

        def _final() -> None:
            m = _still_live()
            if m is not None:
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H20",
                    location="app.py:_reraise_sequence:single_shot_250",
                    message="mirror_reraise_250ms",
                    data={
                        "region_id": debug_region_id,
                        "is_visible": bool(m.isVisible()),
                    },
                )
                # endregion
                m.raise_()
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H33",
                    location="app.py:_reraise_sequence:single_shot_250_after_raise",
                    message="mirror_reraise_250ms_returned",
                    data={
                        "region_id": debug_region_id,
                        "is_visible": bool(m.isVisible()),
                    },
                )
                # endregion
            pending_now = max(0, int(getattr(self, "_debug_reraise_pending", 0)) - 1)
            self._debug_reraise_pending = pending_now
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H43",
                location="app.py:_reraise_sequence:single_shot_250_done",
                message="reraise_250ms_callback_completed",
                data={
                    "seq_id": seq_id,
                    "region_id": debug_region_id,
                    "mirror_live": m is not None,
                    "pending_callbacks": pending_now,
                },
            )
            # endregion

            def _post_final_tick() -> None:
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H40",
                    location="app.py:_reraise_sequence:post_final_tick",
                    message="reraise_post_final_tick_alive",
                    data={
                        "seq_id": seq_id,
                        "region_id": debug_region_id,
                        "pending_callbacks": int(getattr(self, "_debug_reraise_pending", 0)),
                    },
                )
                # endregion

            QTimer.singleShot(1, _post_final_tick)

        QTimer.singleShot(250, _final)

    def _raise_mirror_above_game(self, region_id: UUID) -> None:
        """Re-assert a freshly-spawned mirror above the game window.

        Wayland hands focus to the next valid surface when the
        always-on-top :class:`FloatingRegionPicker` closes; on many
        compositors that lands on Tibia, which would otherwise draw
        on top of the brand new mirror.
        """
        mirror = self._mirrors.get(region_id)
        if mirror is None:
            self._pending_raise_ids.discard(region_id)
            return
        self._reraise_sequence(mirror)
        # Clear the picker-set pending flag at the tail of the same
        # 250 ms window ``_reraise_sequence`` uses, so the bookkeeping
        # stays in lockstep with the old API contract exercised by
        # ``tests.test_mirror_raise``.
        QTimer.singleShot(250, lambda: self._pending_raise_ids.discard(region_id))

    def _raise_all_mirrors(self) -> None:
        """Run the three-stage raise on every visible mirror.

        Hooked up to ``QGuiApplication.focusWindowChanged`` (fires when
        focus leaves all of our surfaces, typically because the user
        clicked into Tibia) and ``applicationStateChanged`` (fires when
        we regain ``ApplicationActive`` after an alt-tab round-trip).

        Mirrors promoted to a wlr-layer-shell overlay surface are
        skipped: the protocol already guarantees they sit above all
        xdg-shell windows including fullscreen ones, and raising /
        activating a layer-shell surface is unnecessary in most
        compositors.
        """
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H30",
            location="app.py:_raise_all_mirrors:entry",
            message="raise_all_mirrors_called",
            data={
                "mirror_count": len(self._mirrors),
                "promoted_count": len(self._layer_shell_promoted),
                "active_window_is_none": QApplication.activeWindow() is None,
            },
        )
        # endregion
        for rid, mirror in self._mirrors.items():
            if rid in self._layer_shell_promoted:
                continue
            if mirror.isVisible():
                self._reraise_sequence(mirror)

    def _auto_lock_all_mirrors_for_gameplay(self) -> None:
        """Hardening: force mirrors into click-through mode on focus loss.

        Losing focus to Tibia while any mirror is unlocked is a common
        "why can't I move/cast?" trap: unlocked mirrors are intentionally
        interactive and can intercept mouse/keyboard input. As soon as
        focus leaves our process, lock everything so every mirror becomes
        transparent-for-input before the user resumes gameplay.
        """
        unlocked_count = sum(1 for region in self._regions if not region.locked)
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H34",
            location="app.py:_auto_lock_all_mirrors_for_gameplay:count",
            message="auto_lock_evaluated",
            data={
                "unlocked_count": int(unlocked_count),
                "region_count": (len(self._regions) if hasattr(self._regions, "__len__") else -1),
                "mirror_count": len(self._mirrors),
            },
        )
        # endregion
        if unlocked_count == 0:
            return
        self._regions.set_all_locked(True)
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H34",
            location="app.py:_auto_lock_all_mirrors_for_gameplay:applied",
            message="auto_lock_applied",
            data={
                "locked_regions": (len(self._regions) if hasattr(self._regions, "__len__") else -1),
            },
        )
        # endregion
        self._control.set_status("Auto-locked all mirrors for gameplay (click-through).")

    def _on_focus_window_changed(self, window: QWindow | None) -> None:
        """Re-raise mirrors when focus moves outside our app.

        Qt reports ``None`` when no window in *our process* holds focus.
        On KDE Plasma Wayland this is how we find out that the user just
        clicked the Tibia window: focus leaves our surfaces, the
        compositor promotes Tibia on top, and unless we push our mirrors
        back above it right now they stay buried until the user clicks
        one of our windows again.
        """
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H30",
            location="app.py:_on_focus_window_changed:entry",
            message="focus_window_changed",
            data={
                "window_is_none": window is None,
                "mirror_count": len(self._mirrors),
                "region_count": (len(self._regions) if hasattr(self._regions, "__len__") else -1),
            },
        )
        # endregion
        if window is not None:
            # Focus stayed inside our app (control panel, a mirror, the
            # picker, etc.). Nothing to do.
            return
        self._auto_lock_all_mirrors_for_gameplay()
        self._raise_all_mirrors()

    def _on_app_state_changed(self, state: Qt.ApplicationState) -> None:
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H30",
            location="app.py:_on_app_state_changed:entry",
            message="application_state_changed",
            data={
                "state": int(getattr(state, "value", -1)),
                "mirror_count": len(self._mirrors),
            },
        )
        # endregion
        if state == Qt.ApplicationState.ApplicationActive:
            self._raise_all_mirrors()

    def _safety_raise_tick(self) -> None:
        """Periodic safety-net: re-raise mirrors when another app has focus.

        Only fires while ``QApplication.activeWindow()`` is ``None``
        (i.e., focus is on some other app, typically Tibia). Skipped
        while the user is interacting with any of our own windows so we
        never disrupt their drag/resize/menu gestures.
        """
        active = QApplication.activeWindow()
        if active is not None:
            return
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H30",
            location="app.py:_safety_raise_tick:entry",
            message="safety_raise_tick_running",
            data={
                "mirror_count": len(self._mirrors),
                "promoted_count": len(self._layer_shell_promoted),
            },
        )
        # endregion
        for rid, mirror in self._mirrors.items():
            if rid in self._layer_shell_promoted:
                continue
            if mirror.isVisible():
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H33",
                    location="app.py:_safety_raise_tick:before_raise",
                    message="safety_tick_about_to_raise_mirror",
                    data={
                        "region_id": str(rid),
                    },
                )
                # endregion
                mirror.raise_()
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H33",
                    location="app.py:_safety_raise_tick:after_raise",
                    message="safety_tick_raise_returned",
                    data={
                        "region_id": str(rid),
                    },
                )
                # endregion

    def _debug_heartbeat_tick(self) -> None:
        if self._debug_heartbeat_count >= 120:
            self._debug_heartbeat_timer.stop()
            return
        self._debug_heartbeat_count += 1
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H40",
            location="app.py:_debug_heartbeat_tick",
            message="app_heartbeat",
            data={
                "beat": int(self._debug_heartbeat_count),
                "frame_count": int(self._debug_frame_counter),
                "mirror_count": len(self._mirrors),
                "visible_mirror_count": int(
                    sum(1 for m in self._mirrors.values() if m.isVisible())
                ),
                "active_window_is_none": QApplication.activeWindow() is None,
            },
        )
        # endregion

    def _destroy_mirror(self, region_id: UUID) -> None:
        mirror = self._mirrors.pop(region_id, None)
        if mirror is not None:
            mirror.close()
            mirror.deleteLater()
        self._pending_raise_ids.discard(region_id)
        self._layer_shell_promoted.discard(region_id)
        self._groups.forget(region_id)
        self._refresh_peer_flags()

    def _update_mirror(self, region: Region) -> None:
        mirror = self._mirrors.get(region.id)
        if mirror is None:
            self._create_mirror(region)
            return
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H2",
            location="app.py:_update_mirror:before_set_region",
            message="updating_mirror_from_region_change",
            data={
                "region_id": str(region.id),
                "locked": bool(region.locked),
                "visible": bool(region.visible),
                "has_geometry": region.geometry is not None,
            },
        )
        # endregion
        try:
            mirror.set_region(region)
        except Exception as exc:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H11",
                location="app.py:_update_mirror:set_region_exception",
                message="mirror_set_region_failed",
                data={
                    "region_id": str(region.id),
                    "exc_type": type(exc).__name__,
                    "exc": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            )
            # endregion
            raise
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H11",
            location="app.py:_update_mirror:after_set_region",
            message="mirror_set_region_completed",
            data={
                "region_id": str(region.id),
                "is_visible": bool(mirror.isVisible()),
                "has_saved_geometry": region.geometry is not None,
            },
        )
        # endregion
        # set_region honours region.visible on its own, which would
        # re-show a mirror we intentionally hid while in companion-only
        # mode. Re-assert the placement policy here.
        if not self._floating_mirrors_enabled() and mirror.isVisible():
            mirror.hide()

    def _rebuild_mirrors(self, regions: list[Region]) -> None:
        for rid in list(self._mirrors.keys()):
            self._destroy_mirror(rid)
        for r in regions:
            self._create_mirror(r)

    def _on_mirror_region_updated(self, region: Region) -> None:
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H13",
            location="app.py:_on_mirror_region_updated:entry",
            message="mirror_emitted_region_updated",
            data={
                "region_id": str(region.id),
                "locked": bool(region.locked),
                "visible": bool(region.visible),
                "has_geometry": region.geometry is not None,
            },
        )
        # endregion
        self._regions.update(region)
        # region agent log
        _debug_log(
            run_id="mirror-crash-debug",
            hypothesis_id="H25",
            location="app.py:_on_mirror_region_updated:after_update",
            message="mirror_region_update_applied",
            data={
                "region_id": str(region.id),
                "has_geometry": region.geometry is not None,
            },
        )
        # endregion
        QTimer.singleShot(500, self._profiles.save_to_disk)

    # -- Snap + group drag ------------------------------------------------------------

    def _on_mirror_moved(self, region_id: UUID, delta: QPoint, final: bool) -> None:
        if final:
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H26",
                location="app.py:_on_mirror_moved:final_entry",
                message="final_move_signal_received",
                data={
                    "region_id": str(region_id),
                    "delta_x": int(delta.x()),
                    "delta_y": int(delta.y()),
                },
            )
            # endregion
        if delta.isNull():
            if final:
                self._try_snap(region_id)
            return
        peers = self._groups.peers(region_id)
        if peers:
            for peer_id in peers:
                peer = self._mirrors.get(peer_id)
                if peer is None or not peer.isVisible():
                    continue
                peer._suppress_next_move = True
                peer.move(peer.pos() + delta)
        if final:
            self._try_snap(region_id)
            # region agent log
            _debug_log(
                run_id="mirror-crash-debug",
                hypothesis_id="H26",
                location="app.py:_on_mirror_moved:final_exit",
                message="final_move_signal_processed",
                data={
                    "region_id": str(region_id),
                },
            )
            # endregion

    def _try_snap(self, region_id: UUID) -> None:
        mirror = self._mirrors.get(region_id)
        if mirror is None or not mirror.isVisible():
            return
        region = self._regions.get(region_id)
        if region is None or region.locked:
            return
        src_rect = mirror.geometry()
        peers = self._groups.peers(region_id)
        for other_id, other in self._mirrors.items():
            if other_id == region_id or other_id in peers:
                continue
            if not other.isVisible():
                continue
            other_region = self._regions.get(other_id)
            if other_region is None or other_region.locked:
                continue
            snapped = compute_snap(src_rect, other.geometry(), SNAP_THRESHOLD)
            if snapped is not None and snapped != src_rect:
                # region agent log
                _debug_log(
                    run_id="mirror-crash-debug",
                    hypothesis_id="H26",
                    location="app.py:_try_snap:apply",
                    message="applying_snap_after_final_move",
                    data={
                        "region_id": str(region_id),
                        "other_id": str(other_id),
                        "from_x": int(src_rect.x()),
                        "from_y": int(src_rect.y()),
                        "to_x": int(snapped.x()),
                        "to_y": int(snapped.y()),
                    },
                )
                # endregion
                mirror.setGeometry(snapped)
                self._groups.join(region_id, other_id)
                self._refresh_peer_flags()
                return

    def _on_unlink_requested(self, region_id: UUID) -> None:
        self._groups.unlink(region_id)
        self._refresh_peer_flags()

    def _refresh_peer_flags(self) -> None:
        for rid, mirror in self._mirrors.items():
            mirror.set_has_peers(bool(self._groups.peers(rid)))

    # -- Profiles ---------------------------------------------------------------------

    def _refresh_profiles_ui(self) -> None:
        self._control.set_profiles(self._profiles.names(), self._profiles.active)

    def _save_profile(self, name: str) -> None:
        # Prevent silent overwrites. Re-saving the active profile keeps the
        # existing "save" shortcut semantics; saving over a *different*
        # existing profile asks for confirmation first.
        existing = set(self._profiles.names())
        if name in existing and name != self._profiles.active:
            box = QMessageBox(self._control)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Overwrite profile?")
            box.setText(f"Profile \u201c{name}\u201d already exists.")
            box.setInformativeText(
                "Saving will replace its saved regions with your current setup. "
                "This cannot be undone."
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            )
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        try:
            self._profiles.save_profile_as(name)
        except OSError as e:
            QMessageBox.critical(
                self._control,
                "Couldn't save profile",
                f"Saving the profile failed:\n\n{e}\n\n"
                "Check that the config folder is writable and try again.",
            )
            return
        self._refresh_profiles_ui()
        self._control.set_status(f"Saved profile '{name}'.")

    def _load_profile(self, name: str) -> None:
        self._profiles.load_profile(name)
        self._refresh_profiles_ui()

    def _delete_profile(self, name: str) -> None:
        try:
            self._profiles.delete_profile(name)
        except ValueError as e:
            QMessageBox.warning(self._control, "Cannot delete", str(e))
        self._refresh_profiles_ui()

    def _import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._control, "Import profile", "", "Profile JSON (*.json)"
        )
        if not path:
            return
        try:
            new_name = self._profiles.import_from(Path(path))
        except FileNotFoundError:
            QMessageBox.warning(
                self._control,
                "File not found",
                "That profile file no longer exists. Pick another one and try again.",
            )
            return
        except json.JSONDecodeError as e:
            QMessageBox.warning(
                self._control,
                "Couldn't read that profile",
                "The file isn't a valid profile export.\n\n"
                f"Details: {e.msg} (line {e.lineno}, column {e.colno})",
            )
            return
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.critical(
                self._control,
                "Couldn't read that profile",
                f"Reading the profile failed:\n\n{e}",
            )
            return
        except (KeyError, ValueError, TypeError) as e:
            QMessageBox.warning(
                self._control,
                "Profile file looks broken",
                f"That file doesn't look like a TibiaVision profile export.\n\nDetails: {e}",
            )
            return
        self._refresh_profiles_ui()
        self._control.set_status(f"Imported profile '{new_name}'.")

    def _export_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._control,
            "Export profile",
            f"{self._profiles.active}.json",
            "Profile JSON (*.json)",
        )
        if not path:
            return
        try:
            self._profiles.export_current_to(Path(path))
        except OSError as e:
            QMessageBox.critical(
                self._control,
                "Couldn't save that file",
                f"Exporting the profile failed:\n\n{e}\n\n"
                "Pick a folder you have permission to write to and try again.",
            )
            return
        self._control.set_status(f"Exported profile '{self._profiles.active}'.")

    def _cycle_profile(self) -> None:
        target = self._profiles.next_profile()
        self._refresh_profiles_ui()
        self._control.set_status(f"Switched to profile '{target}'.")

    # -- Audio timers + shortcuts -----------------------------------------------------

    def _show_donate(self) -> None:
        dlg = DonateDialog(self._control)
        dlg.exec()

    def _open_audio_timers(self) -> None:
        """Show the main window on the Audio Timers page."""
        self._show_control()
        self._control.navigate_to("audio_timers")

    def _register_shortcuts(self) -> None:
        shortcuts = [
            ShortcutSpec(
                id="cycle_profile",
                description="TibiaVision-Linux: cycle to the next profile",
                default_trigger="CTRL+SHIFT+p",
            ),
            ShortcutSpec(
                id="hide_all",
                description="TibiaVision-Linux: hide all mirror windows",
                default_trigger="CTRL+SHIFT+h",
            ),
            ShortcutSpec(
                id="show_all",
                description="TibiaVision-Linux: show all mirror windows",
                default_trigger="CTRL+SHIFT+s",
            ),
        ]
        for slot in range(10):
            shortcuts.append(
                ShortcutSpec(
                    id=f"audio_start_{slot}",
                    description=f"TibiaVision-Linux: start audio timer in slot {slot}",
                )
            )

        self._shortcuts.register_handler("cycle_profile", self._cycle_profile)
        self._shortcuts.register_handler("hide_all", lambda: self._regions.set_all_visible(False))
        self._shortcuts.register_handler("show_all", lambda: self._regions.set_all_visible(True))
        for slot in range(10):
            self._shortcuts.register_handler(f"audio_start_{slot}", self._make_audio_starter(slot))
        self._shortcuts.start(shortcuts)

    def _make_audio_starter(self, slot: int):  # type: ignore[no-untyped-def]
        def _handler() -> None:
            t: AudioTimer | None = self._audio.get_by_slot(slot)
            if t is not None:
                self._audio.start(t.id)

        return _handler

    # -- Misc -------------------------------------------------------------------------

    def _show_control(self) -> None:
        self._control.show()
        self._control.raise_()
        self._control.activateWindow()
