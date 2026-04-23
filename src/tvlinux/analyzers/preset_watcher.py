"""File-watch based character-switch detector.

Publishes :data:`~tvlinux.analyzers.base.EventKind.LOGIN_DETECTED` whenever
the user's active Tibia hotkey preset changes -- which Tibia does on every
login because each character binds to its own preset (and
``autoSwitchHotkeyPreset`` is on by default).

Why not OCR?
------------
The old :class:`~tvlinux.analyzers.login_name.LoginNameAnalyzer` was a
stub meant to OCR the character-name strip on every frame. Watching
``clientoptions.json`` is strictly better:

* Zero CV / Tesseract dependency.
* Fires in milliseconds after Tibia persists the change, not after a
  second-or-two OCR cadence.
* Works even if the character-name strip is off-screen or hidden by a
  mirror window.

This producer therefore lives in the ``analyzers`` package for
discoverability but is *not* an :class:`Analyzer` subclass -- it has no
per-frame work. It is wired into :class:`~tvlinux.app.Application`
alongside the hub and publishes onto the bus directly.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer

from ..logging_config import get_logger
from ..tibia_data import client_options_path, current_preset_name, read_client_options
from .base import AnalyzerHub, Event, EventKind

log = get_logger(__name__)

# Tibia rewrites the options file as a single atomic ``mv``; QFileSystemWatcher
# can fire a single rename as both "file removed" and "file changed" on some
# filesystems. Coalescing notifications over a short window means we only
# re-read once per real change.
_DEBOUNCE_MS = 150


class PresetWatcher(QObject):
    """Watch ``clientoptions.json`` and emit LOGIN_DETECTED on preset changes.

    :param bus: the application's :class:`EventBus` / ``AnalyzerHub``.
    :param path: override for ``clientoptions.json`` (tests).
    """

    id = "preset_watcher"

    def __init__(
        self,
        bus: AnalyzerHub,
        *,
        path: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus
        self._path: Path | None = path if path is not None else client_options_path()
        self._last_preset: str | None = None
        self._watcher: QFileSystemWatcher | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._recheck)

        if self._path is None:
            log.info("preset_watcher.no_tibia_install")
            return

        self._install_watcher(self._path)
        # Seed ``_last_preset`` from the file so we don't spuriously publish
        # on startup. The first real change produces the first event.
        self._last_preset = self._read_preset()

    # -- Public surface --------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def last_preset(self) -> str | None:
        return self._last_preset

    def check_now(self) -> None:
        """Synchronously re-read the file and publish if the preset changed.

        Exposed for the app to force a pass (e.g. on HUD toggle) and for
        tests to avoid driving the real QFileSystemWatcher.
        """
        self._recheck()

    # -- Internals -------------------------------------------------------------

    def _install_watcher(self, path: Path) -> None:
        self._watcher = QFileSystemWatcher(self)
        # Watch both the file *and* its parent: on rename-replace (Tibia's
        # atomic write) the inode changes and the path watch on the old
        # inode is dropped. Parent-dir watch catches the replacement.
        self._watcher.addPath(str(path))
        parent = str(path.parent)
        if parent:
            self._watcher.addPath(parent)
        self._watcher.fileChanged.connect(self._on_fs_event)
        self._watcher.directoryChanged.connect(self._on_fs_event)

    def _on_fs_event(self, _changed_path: str) -> None:
        # Re-add the file path in case a rename-replace dropped it.
        if (
            self._watcher is not None
            and self._path is not None
            and str(self._path) not in self._watcher.files()
        ):
            self._watcher.addPath(str(self._path))
        self._debounce.start()

    def _recheck(self) -> None:
        current = self._read_preset()
        if current is None:
            return
        if current == self._last_preset:
            return
        previous = self._last_preset
        self._last_preset = current
        log.info("preset_watcher.changed", previous=previous, current=current)
        self._bus.publish(
            Event(
                analyzer_id=self.id,
                kind=EventKind.LOGIN_DETECTED,
                data={
                    "name": current,
                    "preset": current,
                    "previous": previous,
                    "source": "clientoptions",
                },
            )
        )

    def _read_preset(self) -> str | None:
        if self._path is None:
            return None
        options = read_client_options(self._path)
        if options is None:
            return None
        return current_preset_name(options)


__all__ = ["PresetWatcher"]
