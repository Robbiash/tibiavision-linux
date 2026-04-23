"""Click-synthesizer that replays the in-game 'right-click + Copy' dance.

This is the module that actually causes the clipboard to refresh. It is
explicitly a no-op unless Hunt Mode is active and a :class:`CopyAnchor` has
been calibrated; this is what makes the feature safe-by-default.

Tools
-----

- **xdotool** on X11. Has first-class per-window click routing, never moves
  the real cursor pointer.
- **ydotool** on Wayland (requires ``ydotoold`` running). Works with
  absolute coordinates.

If neither tool is present, the refresher degrades to a logged no-op -- the
user's manual in-game Copy still works because ``ClipboardWatcher`` is
listening on its own channel.

Rate-limiting
-------------

:meth:`HuntRefresher.fire_once` computes ``now - _last_fire_ts`` against
``HuntModeConfig.min_refresh_interval_sec``. If we're still inside the
window, the call is a silent no-op. This is the key ergonomic property: the
user can mash the trigger key thousands of times and we still only refresh
at the configured cadence (default: once per 60 s).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import QObject, QTimer, Signal

from .hunt_mode import CopyAnchor, HuntModeManager
from .logging_config import get_logger

log = get_logger(__name__)

ToolKind = Literal["xdotool", "ydotool", "none"]
AnchorKind = Literal["hunt", "party"]


@dataclass
class ToolStatus:
    tool: ToolKind
    available: bool
    reason: str = ""


def detect_tool() -> ToolStatus:
    """Return the best available click-synth tool."""
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland = session_type == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))

    if not wayland and shutil.which("xdotool"):
        return ToolStatus(tool="xdotool", available=True)
    if shutil.which("ydotool"):
        return ToolStatus(tool="ydotool", available=True)
    if shutil.which("xdotool"):
        return ToolStatus(tool="xdotool", available=True)
    return ToolStatus(
        tool="none",
        available=False,
        reason="Neither xdotool (X11) nor ydotool (Wayland) is installed.",
    )


class HuntRefresher(QObject):
    """Fires 'right-click + Copy' at calibrated coords with rate limiting.

    Consumers call :meth:`fire_once` with a short human reason ("space",
    "auto-timer", "manual"); the refresher does the gating, logging, and
    platform-specific invocation.
    """

    fired = Signal(str, float)  # (reason, monotonic_ts)
    skipped = Signal(str, str)  # (reason, why_skipped)

    def __init__(
        self,
        hunt_mode: HuntModeManager,
        *,
        parent: QObject | None = None,
        _subprocess_run=None,  # test seam
    ) -> None:
        super().__init__(parent)
        self._mode = hunt_mode
        self._status = detect_tool()
        self._last_fire_ts = float("-inf")
        self._subprocess_run = _subprocess_run or subprocess.run

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(False)
        self._auto_timer.timeout.connect(self._on_auto_tick)
        self._reconfigure()
        self._mode.config_changed.connect(lambda _cfg: self._reconfigure())
        self._mode.toggled.connect(lambda _a: self._reconfigure())

    # -- Public API --------------------------------------------------------

    @property
    def tool(self) -> ToolKind:
        return self._status.tool

    @property
    def available(self) -> bool:
        return self._status.available

    @property
    def last_fire_ts(self) -> float:
        return self._last_fire_ts

    def seconds_until_next_fire(self) -> float:
        interval = max(0, int(self._mode.config.min_refresh_interval_sec))
        if interval == 0:
            return 0.0
        delta = (self._last_fire_ts + interval) - time.monotonic()
        return max(0.0, delta)

    def fire_once(self, reason: str = "manual") -> bool:
        """Attempt a refresh. Returns True if clicks actually fired."""
        if not self._mode.active:
            self.skipped.emit(reason, "hunt_mode_off")
            return False
        cfg = self._mode.config
        interval = max(0, int(cfg.min_refresh_interval_sec))
        now = time.monotonic()
        if interval > 0 and (now - self._last_fire_ts) < interval:
            self.skipped.emit(reason, "rate_limited")
            return False
        if not self._status.available:
            self.skipped.emit(reason, "no_tool")
            return False
        any_fired = False
        if cfg.copy_anchor_hunt is not None:
            any_fired |= self._replay(cfg.copy_anchor_hunt, "hunt")
        if cfg.copy_anchor_party is not None:
            any_fired |= self._replay(cfg.copy_anchor_party, "party")
        if not any_fired:
            self.skipped.emit(reason, "no_anchor")
            return False
        self._last_fire_ts = now
        self.fired.emit(reason, now)
        log.info("hunt_refresh.fired", reason=reason, tool=self._status.tool)
        return True

    # -- Auto-timer --------------------------------------------------------

    def _reconfigure(self) -> None:
        cfg = self._mode.config
        interval_sec = int(cfg.auto_fire_fallback_sec)
        should_run = self._mode.active and interval_sec > 0
        if should_run:
            self._auto_timer.start(max(1000, interval_sec * 1000))
        else:
            self._auto_timer.stop()

    def _on_auto_tick(self) -> None:
        self.fire_once("auto-timer")

    # -- Click synthesis ---------------------------------------------------

    def _replay(self, anchor: CopyAnchor, which: AnchorKind) -> bool:
        try:
            if self._status.tool == "xdotool":
                self._xdotool_sequence(anchor)
                return True
            if self._status.tool == "ydotool":
                self._ydotool_sequence(anchor)
                return True
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            log.warning("hunt_refresh.tool_failed", which=which, error=str(e))
        return False

    def _xdotool_sequence(self, anchor: CopyAnchor) -> None:
        # Right-click at (right_click_x, right_click_y), wait, click the menu entry.
        self._subprocess_run(
            [
                "xdotool",
                "mousemove",
                "--sync",
                str(anchor.right_click_x),
                str(anchor.right_click_y),
                "click",
                "3",
            ],
            check=False,
            timeout=1.0,
        )
        time.sleep(0.15)
        self._subprocess_run(
            [
                "xdotool",
                "mousemove",
                "--sync",
                str(anchor.menu_x),
                str(anchor.menu_y),
                "click",
                "1",
            ],
            check=False,
            timeout=1.0,
        )

    def _ydotool_sequence(self, anchor: CopyAnchor) -> None:
        # ydotool uses absolute mouse moves when --absolute is passed to
        # `mousemove`. Button codes: 0xC0 = right, 0xC1 = left.
        self._subprocess_run(
            [
                "ydotool",
                "mousemove",
                "--absolute",
                "-x",
                str(anchor.right_click_x),
                "-y",
                str(anchor.right_click_y),
            ],
            check=False,
            timeout=1.0,
        )
        self._subprocess_run(["ydotool", "click", "0xC0"], check=False, timeout=1.0)
        time.sleep(0.15)
        self._subprocess_run(
            [
                "ydotool",
                "mousemove",
                "--absolute",
                "-x",
                str(anchor.menu_x),
                "-y",
                str(anchor.menu_y),
            ],
            check=False,
            timeout=1.0,
        )
        self._subprocess_run(["ydotool", "click", "0xC1"], check=False, timeout=1.0)


__all__ = [
    "HuntRefresher",
    "ToolStatus",
    "detect_tool",
]
