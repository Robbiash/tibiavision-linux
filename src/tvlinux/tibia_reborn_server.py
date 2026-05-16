"""Manages the tibia-reborn Next.js dev server as a background subprocess.

TibiaVision starts this server when the Quest Browser page is first shown
and kills it cleanly when the Qt application exits.

Configure the tibia-reborn directory via:
  TIBIA_REBORN_DIR=/path/to/tibia-reborn   (env var)
  Default: sibling directory next to tibiavision-linux/
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

__all__ = ["TibiaRebornServer"]

_DEFAULT_PORT = 3000
_POLL_INTERVAL_MS = 1000
_MAX_WAIT_MS = 60_000

_SIBLING_REBORN = Path(__file__).resolve().parents[3] / "tibia-reborn"


def _find_reborn_dir() -> Path:
    raw = os.environ.get("TIBIA_REBORN_DIR", "").strip()
    return Path(raw).expanduser() if raw else _SIBLING_REBORN


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


class TibiaRebornServer(QObject):
    """Start / stop the tibia-reborn Next.js server and signal readiness."""

    ready = Signal()  # emitted once port 3000 answers
    failed = Signal(str)  # emitted if startup times out or npm is missing
    stopped = Signal()  # emitted after the process is killed

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._elapsed_ms = 0
        self._is_ready = False
        self._reborn_dir = _find_reborn_dir()

    # -- Public API -----------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://localhost:{_DEFAULT_PORT}"

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    @property
    def reborn_dir(self) -> Path:
        return self._reborn_dir

    def start(self) -> None:
        """Launch the Next.js server if it isn't already running."""
        if self._is_ready:
            self.ready.emit()
            return
        if self._proc is not None:
            return  # already starting

        # If something else is already on port 3000, use it directly.
        if _port_open(_DEFAULT_PORT):
            self._is_ready = True
            self.ready.emit()
            return

        if not self._reborn_dir.exists():
            self.failed.emit(
                f"tibia-reborn directory not found at {self._reborn_dir}.\n"
                "Set TIBIA_REBORN_DIR to its location."
            )
            return

        npm = self._find_npm()
        if npm is None:
            self.failed.emit(
                "npm not found. Install Node.js to use the Quest Browser.\n"
                f"Expected tibia-reborn at: {self._reborn_dir}"
            )
            return

        try:
            self._proc = subprocess.Popen(
                [npm, "run", "dev"],
                cwd=self._reborn_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Keep the process alive even if Python's signal mask changes.
                start_new_session=True,
            )
        except OSError as exc:
            self.failed.emit(f"Failed to start tibia-reborn: {exc}")
            return

        self._elapsed_ms = 0
        self._poll_timer.start()

    def stop(self) -> None:
        """Kill the server process if we own it."""
        self._poll_timer.stop()
        self._is_ready = False
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.stopped.emit()

    # -- Internals ------------------------------------------------------------

    def _poll(self) -> None:
        self._elapsed_ms += _POLL_INTERVAL_MS
        if _port_open(_DEFAULT_PORT):
            self._poll_timer.stop()
            self._is_ready = True
            self.ready.emit()
            return
        if self._elapsed_ms >= _MAX_WAIT_MS:
            self._poll_timer.stop()
            self.failed.emit(
                "tibia-reborn server did not start within 60 seconds.\n"
                "Check that npm dependencies are installed (npm install)."
            )

    @staticmethod
    def _find_npm() -> str | None:
        candidates = ["npm"]
        if sys.platform == "win32":
            candidates = ["npm.cmd", "npm"]
        for candidate in candidates:
            try:
                subprocess.run(
                    [candidate, "--version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
                return candidate
            except (OSError, subprocess.CalledProcessError):
                continue
        return None
