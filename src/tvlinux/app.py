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

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, QPoint, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QImage
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import __app_name__, app_icon_path
from .analyzers import AnalyzerHub, PixelWatchAnalyzer, PresetWatcher
from .audio_timers import AudioTimer, AudioTimerManager, AudioTimersDialog
from .capture import CaptureCore
from .clipboard_watcher import ClipboardWatcher
from .control_panel import ControlPanel
from .donate_dialog import DonateDialog
from .hud_panels import (
    AudioTimerPanel,
    HotbarPanel,
    HuntStatsPanel,
    MetronomePanel,
    PartyPanel,
)
from .logging_config import get_logger
from .mirror_window import MirrorWindow
from .paths import triggers_path
from .profiles import ProfileManager
from .region_picker import RegionPickerDialog
from .regions import Region, RegionManager
from .shortcuts import GlobalShortcutManager, ShortcutSpec
from .smart_hud import SmartHud
from .snap import SNAP_THRESHOLD, MirrorGroupManager, compute_snap
from .theme import apply as apply_theme
from .trigger_engine import TriggerEngine, default_rules

log = get_logger(__name__)


class Application(QObject):
    """Glue object. Not a QApplication subclass - we just hold references."""

    quit_requested = Signal()

    def __init__(self, *, use_portal: bool = True) -> None:
        super().__init__()
        self._use_portal = use_portal

        self._regions = RegionManager(self)
        self._profiles = ProfileManager(self._regions)
        self._audio = AudioTimerManager(parent=self)
        self._shortcuts = GlobalShortcutManager(self)

        self._mirrors: dict[UUID, MirrorWindow] = {}
        self._groups = MirrorGroupManager()
        self._last_frame: QImage | None = None
        self._picker: RegionPickerDialog | None = None
        self._audio_dialog: AudioTimersDialog | None = None

        self._control = ControlPanel(self._regions)
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
        self._hub.register(self._pixel_watch)
        self._clipboard_watcher = ClipboardWatcher(self._hub, parent=self)

        # Smart HUD: strictly click-through overlay that hosts pluggable
        # HudPanel instances. Adding a new panel is a single file + one
        # register_panel() call below; SmartHud itself stays untouched.
        self._hud = SmartHud(bus=self._hub, parent=None)
        self._hud.register_panel(AudioTimerPanel(self._audio))
        self._hud.register_panel(MetronomePanel())
        self._hud.register_panel(HotbarPanel(self._hub))
        self._hud.register_panel(HuntStatsPanel(self._hub))
        self._hud.register_panel(PartyPanel(self._hub))

        self._build_tray()
        self._wire_signals()
        self._refresh_profiles_ui()

        apply_theme(QApplication.instance())

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
        self._control.show_all_requested.connect(self._regions.set_all_visible)
        self._control.lock_all_requested.connect(self._regions.set_all_locked)
        self._control.save_profile_requested.connect(self._save_profile)
        self._control.load_profile_requested.connect(self._load_profile)
        self._control.delete_profile_requested.connect(self._delete_profile)
        self._control.import_profile_requested.connect(self._import_profile)
        self._control.export_profile_requested.connect(self._export_profile)
        self._control.open_audio_timers_requested.connect(self._open_audio_timers)
        self._control.donate_requested.connect(self._show_donate)

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
        self._control.show()
        self._hud.show()
        self._capture.start()
        self._register_shortcuts()

    def _set_hud_visible(self, visible: bool) -> None:
        """Tray-menu toggle for the Smart HUD overlay."""
        if visible:
            self._hud.show()
        else:
            self._hud.hide()

    def _quit(self) -> None:
        self._profiles.save_to_disk()
        self._capture.stop()
        self._shortcuts.stop()
        QApplication.quit()

    # -- Capture feedback -------------------------------------------------------------

    def _on_capture_started(self) -> None:
        self._control.set_status(
            f"Capturing source {self._capture.source_size.width()}x"
            f"{self._capture.source_size.height()}"
        )

    def _on_capture_error(self, msg: str) -> None:
        self._control.set_status(f"Capture error: {msg}")
        QMessageBox.warning(self._control, "Capture error", msg)

    def _on_source_size_changed(self, _size) -> None:  # type: ignore[no-untyped-def]
        self._on_capture_started()

    def _on_frame(self, image: QImage) -> None:
        self._last_frame = image
        for mirror in self._mirrors.values():
            mirror.set_frame(image)
        if self._picker is not None and self._picker.isVisible():
            self._picker.set_frame(image)

    # -- Region CRUD actions ---------------------------------------------------------

    def _open_region_picker(self) -> None:
        if self._last_frame is None:
            QMessageBox.information(
                self._control,
                "No capture yet",
                "Wait for the capture session to start before adding regions.",
            )
            return
        dlg = RegionPickerDialog(self._control)
        self._picker = dlg
        dlg.set_frame(self._last_frame)
        try:
            if dlg.exec() == RegionPickerDialog.DialogCode.Accepted:
                rect = dlg.selected_rect()
                if rect.isValid() and not rect.isEmpty():
                    region = Region(
                        name=f"Region {len(self._regions) + 1}",
                        rect=rect,
                    )
                    self._regions.add(region)
                    self._profiles.save_to_disk()
        finally:
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
        r.locked = locked
        self._regions.update(r)

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
        self._regions.update(r)

    # -- Mirror lifecycle -------------------------------------------------------------

    def _create_mirror(self, region: Region) -> None:
        if region.id in self._mirrors:
            return
        mirror = MirrorWindow(region)
        mirror.rename_requested.connect(self._rename_region)
        mirror.delete_requested.connect(self._delete_region)
        mirror.region_updated.connect(self._on_mirror_region_updated)
        mirror.moved.connect(self._on_mirror_moved)
        mirror.unlink_requested.connect(self._on_unlink_requested)
        self._mirrors[region.id] = mirror
        if region.visible:
            mirror.show()
        if self._last_frame is not None:
            mirror.set_frame(self._last_frame)

    def _destroy_mirror(self, region_id: UUID) -> None:
        mirror = self._mirrors.pop(region_id, None)
        if mirror is not None:
            mirror.close()
            mirror.deleteLater()
        self._groups.forget(region_id)
        self._refresh_peer_flags()

    def _update_mirror(self, region: Region) -> None:
        mirror = self._mirrors.get(region.id)
        if mirror is None:
            self._create_mirror(region)
            return
        mirror.set_region(region)

    def _rebuild_mirrors(self, regions: list[Region]) -> None:
        for rid in list(self._mirrors.keys()):
            self._destroy_mirror(rid)
        for r in regions:
            self._create_mirror(r)

    def _on_mirror_region_updated(self, region: Region) -> None:
        self._regions.update(region)
        QTimer.singleShot(500, self._profiles.save_to_disk)

    # -- Snap + group drag ------------------------------------------------------------

    def _on_mirror_moved(self, region_id: UUID, delta: QPoint, final: bool) -> None:
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
        self._profiles.save_profile_as(name)
        self._refresh_profiles_ui()

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
        if path:
            new_name = self._profiles.import_from(Path(path))
            self._refresh_profiles_ui()
            self._control.set_status(f"Imported profile '{new_name}'.")

    def _export_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._control,
            "Export profile",
            f"{self._profiles.active}.json",
            "Profile JSON (*.json)",
        )
        if path:
            self._profiles.export_current_to(Path(path))
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
        if self._audio_dialog is None:
            self._audio_dialog = AudioTimersDialog(self._audio, self._control)
        self._audio_dialog.show()
        self._audio_dialog.raise_()
        self._audio_dialog.activateWindow()

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
