"""Analyzer base classes.

Everything here is intentionally v1-stable: subclasses in v2 will fill in the
``analyze()`` body without changing the hub, signal shapes, or integration points in
``app.py``.

Threading model:
    The hub runs on the Qt main thread. Each analyzer is expected to be fast (< 10 ms)
    or to kick its own worker. We give it a ``QThreadPool`` handle in v2 if needed.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QSize, Signal

from ..logging_config import get_logger

log = get_logger(__name__)


@dataclass
class AnalyzerFrame:
    """One frame routed to analyzers. The ndarray is (H, W, 4) uint8 BGRA."""

    buffer: np.ndarray
    size: QSize
    monotonic_ts: float = field(default_factory=time.monotonic)


@dataclass
class Event:
    """Base class for analyzer-emitted events. Keep subclasses small and JSON-safe."""

    analyzer_id: str
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


class Analyzer(ABC):
    """Stateless or stateful analyzer.

    Subclasses override ``analyze``. Return an iterable of ``Event``; an empty list is
    fine. If an analyzer is expensive, it should internally sub-sample frames.
    """

    id: str = "abstract"

    def __init__(self) -> None:
        self.enabled = True

    @abstractmethod
    def analyze(self, frame: AnalyzerFrame) -> list[Event]: ...


class AnalyzerHub(QObject):
    """Dispatches incoming frame buffers to registered analyzers.

    In v1 we simply expose the slot and signal; no analyzers are registered by default.
    """

    event_emitted = Signal(object)  # Event
    # True whenever at least one analyzer is registered; False otherwise. The application
    # wires this to ``CaptureCore.buffer_output_enabled`` so the per-frame numpy
    # conversion only runs when something actually consumes it.
    active_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._analyzers: list[Analyzer] = []

    def _refresh_active(self, prev_active: bool) -> None:
        now_active = bool(self._analyzers)
        if now_active != prev_active:
            self.active_changed.emit(now_active)

    def register(self, analyzer: Analyzer) -> None:
        prev = bool(self._analyzers)
        self._analyzers.append(analyzer)
        log.info("analyzer.registered", id=analyzer.id)
        self._refresh_active(prev)

    def unregister(self, analyzer: Analyzer) -> None:
        prev = bool(self._analyzers)
        self._analyzers = [a for a in self._analyzers if a is not analyzer]
        self._refresh_active(prev)

    def on_frame_buffer(self, buf: np.ndarray, size: QSize) -> None:  # slot
        if not self._analyzers:
            return
        frame = AnalyzerFrame(buffer=buf, size=size)
        for a in self._analyzers:
            if not a.enabled:
                continue
            try:
                for event in a.analyze(frame) or []:
                    self.event_emitted.emit(event)
            except Exception:  # pragma: no cover - never let one analyzer kill the hub
                log.exception("analyzer.failed", id=a.id)
