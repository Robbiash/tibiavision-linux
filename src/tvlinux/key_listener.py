"""Passive (observer-only) keyboard listener for Hunt Mode triggers.

The idea: Hunt Mode lets the user pick a key they already press constantly
during normal gameplay (default: Space, Tibia's auto-target key). We *observe*
that key system-wide without consuming it, so Tibia still gets the keystroke
and behaves exactly as before. Combined with a focus filter (only fire when
the foreground window looks like Tibia) and an upstream rate-limit (only
actually refresh at most once every N seconds), the result is: you play
normally, your hunt stats stay fresh, and we never interfere with gameplay.

Backend matrix
--------------

- **X11**: uses :mod:`pynput.keyboard.Listener` which wraps XInput2 passive
  grabs. Observing only; keys are never swallowed.
- **Wayland**: reads from ``/dev/input/event*`` via :mod:`evdev`. Requires the
  user to be in the ``input`` group (``sudo usermod -aG input $USER`` + new
  login). If ``/dev/input/event*`` is unreadable we fail cleanly, set
  ``available = False``, and emit a descriptive ``error`` signal so the
  Settings page can surface the fix.

Both backends are wrapped behind a single :class:`PassiveKeyListener` Qt
object so callers need not know which platform they are on.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QObject, QTimer, Signal

from .logging_config import get_logger

log = get_logger(__name__)

Backend = Literal["x11", "wayland", "unavailable"]


@dataclass
class BackendStatus:
    backend: Backend
    available: bool
    reason: str = ""


def detect_backend() -> BackendStatus:
    """Return the best available passive-listen backend + diagnostic text."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")

    if session_type == "x11" or (not wayland_display and os.environ.get("DISPLAY")):
        try:
            import pynput.keyboard  # noqa: F401
        except ImportError:
            return BackendStatus(
                backend="x11",
                available=False,
                reason="pynput is not installed (pip install pynput)",
            )
        return BackendStatus(backend="x11", available=True)

    if session_type == "wayland" or wayland_display:
        try:
            import evdev  # noqa: F401
        except ImportError:
            return BackendStatus(
                backend="wayland",
                available=False,
                reason="python-evdev is not installed (pip install evdev)",
            )
        # Check readability of at least one input device.
        import glob

        devices = glob.glob("/dev/input/event*")
        if not devices:
            return BackendStatus(
                backend="wayland",
                available=False,
                reason="No /dev/input/event* devices found",
            )
        for dev in devices:
            if os.access(dev, os.R_OK):
                return BackendStatus(backend="wayland", available=True)
        return BackendStatus(
            backend="wayland",
            available=False,
            reason=(
                "/dev/input/event* is not readable. "
                "Fix: sudo usermod -aG input $USER and log out/in."
            ),
        )

    return BackendStatus(
        backend="unavailable",
        available=False,
        reason="No supported display server detected.",
    )


# -- Focus filter ------------------------------------------------------------


def active_window_title() -> str:
    """Return the foreground window's title, best-effort across compositors.

    Order: xdotool (X11), swaymsg (Sway), hyprctl (Hyprland). Returns "" if
    all fail; the focus filter then falls back to "fire regardless" (rate
    limiting still protects against spam).
    """
    if shutil.which("xdotool"):
        try:
            out = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            if out.returncode == 0:
                return out.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
    if shutil.which("swaymsg"):
        try:
            out = subprocess.run(
                ["swaymsg", "-t", "get_tree"],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            if out.returncode == 0:
                import json as _json

                try:
                    tree = _json.loads(out.stdout)
                except _json.JSONDecodeError:
                    tree = None
                if tree is not None:
                    focused = _find_focused_sway(tree)
                    if focused:
                        return focused
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
    if shutil.which("hyprctl"):
        try:
            out = subprocess.run(
                ["hyprctl", "activewindow"],
                capture_output=True,
                text=True,
                timeout=0.5,
                check=False,
            )
            if out.returncode == 0:
                for line in out.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("title:"):
                        return line.split(":", 1)[1].strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
    return ""


def _find_focused_sway(node: dict) -> str | None:
    if node.get("focused") and node.get("name"):
        return str(node["name"])
    for child_list in ("nodes", "floating_nodes"):
        for child in node.get(child_list, []) or []:
            r = _find_focused_sway(child)
            if r:
                return r
    return None


# -- Listener ---------------------------------------------------------------


class PassiveKeyListener(QObject):
    """Observes one trigger key system-wide and emits ``key_pressed``.

    Design points:

    - **Observer-only**: the backend never consumes keystrokes. Tibia always
      gets the key.
    - **Focus filter**: emissions are gated on the active window title
      containing ``tibia_window_substring`` (case-insensitive). Set the
      substring to ``""`` to disable the filter.
    - **Idempotent**: ``start()`` is safe to call repeatedly; ``stop()`` is
      safe on an already-stopped listener.
    - **Thread-safe**: the backend thread calls into Qt via a queued
      connection (emitting a signal on ``self`` from another thread is safe
      in Qt).
    """

    key_pressed = Signal(str)  # emitted with the pressed key name
    error = Signal(str)  # backend problem; UI can surface this

    def __init__(
        self,
        *,
        trigger_key: str = "space",
        tibia_window_substring: str = "Tibia",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._trigger_key = trigger_key.lower()
        self._tibia_substring = tibia_window_substring
        self._running = False
        self._status = detect_backend()
        self._backend_handle: object | None = None
        self._stop_event = threading.Event()
        self._focus_cache: tuple[float, str] | None = None  # (when, title)

    @property
    def available(self) -> bool:
        return self._status.available

    @property
    def backend(self) -> Backend:
        return self._status.backend

    @property
    def unavailable_reason(self) -> str:
        return self._status.reason

    def set_trigger_key(self, key: str) -> None:
        self._trigger_key = key.lower().strip() or "space"

    def set_tibia_substring(self, substring: str) -> None:
        self._tibia_substring = substring or ""

    # -- Lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        if self._running:
            return True
        if not self._status.available:
            log.info(
                "key_listener.unavailable",
                backend=self._status.backend,
                reason=self._status.reason,
            )
            self.error.emit(self._status.reason)
            return False
        if self._status.backend == "x11":
            return self._start_x11()
        if self._status.backend == "wayland":
            return self._start_wayland()
        return False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        handle = self._backend_handle
        self._backend_handle = None
        if handle is not None:
            try:
                stop_fn = getattr(handle, "stop", None)
                if callable(stop_fn):
                    stop_fn()
            except Exception as e:
                log.warning("key_listener.stop_failed", error=str(e))

    # -- Focus filter ------------------------------------------------------

    def _is_tibia_focused(self) -> bool:
        if not self._tibia_substring:
            return True
        title = active_window_title()
        return self._tibia_substring.lower() in title.lower()

    def _handle_key(self, key_name: str) -> None:
        if key_name.lower() != self._trigger_key:
            return
        if not self._is_tibia_focused():
            return
        self.key_pressed.emit(key_name)

    # -- Backends ----------------------------------------------------------

    def _start_x11(self) -> bool:
        try:
            from pynput import keyboard as _kb
        except ImportError as e:  # pragma: no cover - detection prevents this
            self.error.emit(f"pynput missing: {e}")
            return False

        def on_press(key: object) -> None:
            name = _pynput_key_name(key)
            if name:
                QTimer.singleShot(0, lambda n=name: self._handle_key(n))

        listener = _kb.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        self._backend_handle = listener
        self._running = True
        log.info("key_listener.started", backend="x11", trigger=self._trigger_key)
        return True

    def _start_wayland(self) -> bool:
        try:
            import glob

            import evdev
        except ImportError as e:  # pragma: no cover - detection prevents this
            self.error.emit(f"evdev missing: {e}")
            return False

        devices: list[evdev.InputDevice] = []
        for path in glob.glob("/dev/input/event*"):
            if not os.access(path, os.R_OK):
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            caps = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
            if caps:
                devices.append(dev)

        if not devices:
            self.error.emit("No readable keyboard devices under /dev/input/")
            return False

        target_code = _evdev_code_for(self._trigger_key)

        def loop() -> None:
            from select import select

            fds = {d.fd: d for d in devices}
            while not self._stop_event.is_set():
                try:
                    r, _w, _x = select(list(fds), [], [], 0.2)
                except (OSError, ValueError):
                    break
                for fd in r:
                    dev = fds[fd]
                    try:
                        for event in dev.read():
                            if event.type != 1:  # EV_KEY
                                continue
                            if event.value != 1:  # only key-down
                                continue
                            if target_code is not None and event.code != target_code:
                                continue
                            name = _evdev_event_key_name(event)
                            if name is None:
                                continue
                            QTimer.singleShot(0, lambda n=name: self._handle_key(n))
                    except OSError:
                        continue

        self._stop_event.clear()
        thread = threading.Thread(target=loop, name="tvlinux-keylistener", daemon=True)
        thread.start()
        self._backend_handle = _WaylandHandle(thread=thread, devices=devices)
        self._running = True
        log.info("key_listener.started", backend="wayland", trigger=self._trigger_key)
        return True


# -- Helpers ----------------------------------------------------------------


@dataclass
class _WaylandHandle:
    thread: threading.Thread
    devices: Sequence[object]

    def stop(self) -> None:
        for dev in self.devices:
            try:
                close = getattr(dev, "close", None)
                if callable(close):
                    close()
            except OSError:
                pass


def _pynput_key_name(key: object) -> str | None:
    """Normalize a pynput key event to a lowercase name (e.g. 'space', 'a')."""
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return char.lower()
    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        return name.lower()
    return None


def _evdev_code_for(key_name: str) -> int | None:
    """Resolve a human key name to the evdev key code (e.g. 'space' -> KEY_SPACE)."""
    try:
        import evdev.ecodes as e  # type: ignore[import-not-found]
    except ImportError:
        return None
    key = key_name.upper().strip()
    if not key:
        return None
    candidates = [f"KEY_{key}", key]
    for candidate in candidates:
        code = getattr(e, candidate, None)
        if isinstance(code, int):
            return code
    return None


def _evdev_event_key_name(event: object) -> str | None:
    try:
        import evdev.ecodes as e  # type: ignore[import-not-found]
    except ImportError:
        return None
    code = getattr(event, "code", None)
    if code is None:
        return None
    name = e.KEY.get(code) if hasattr(e, "KEY") else None
    if isinstance(name, list):
        name = name[0] if name else None
    if isinstance(name, str) and name.startswith("KEY_"):
        return name[4:].lower()
    if isinstance(name, str):
        return name.lower()
    return None


__all__ = [
    "BackendStatus",
    "PassiveKeyListener",
    "active_window_title",
    "detect_backend",
]
