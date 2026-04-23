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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QSize, Signal

from ..logging_config import get_logger

log = get_logger(__name__)


class EventKind:
    """Canonical event-kind strings.

    Used both by analyzer publishers and by consumers (trigger engine, HUD,
    widgets) so a typo shows up at import time rather than at runtime as a
    silently-dropped event. Extend as new analyzers land.
    """

    STATS_UPDATE = "STATS_UPDATE"
    SWING_TIMER_RESET = "SWING_TIMER_RESET"
    EQUIP_UPDATE = "EQUIP_UPDATE"
    LOOT_LOGGED = "LOOT_LOGGED"
    LOGIN_DETECTED = "LOGIN_DETECTED"
    # Phase 4 additions.
    PIXEL_WATCH_CHANGED = "PIXEL_WATCH_CHANGED"


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


EventHandler = Callable[[Event], None]


class Analyzer(ABC):
    """Stateless or stateful analyzer.

    Subclasses override ``analyze``. Return an iterable of ``Event``; an empty list is
    fine. If an analyzer is expensive, declare a ``tick_ms`` class attribute (or set it
    in ``__init__``) and the hub will only invoke ``analyze`` once that many ms have
    elapsed since the last run -- no per-analyzer boilerplate needed.
    """

    id: str = "abstract"
    # None = run on every frame. Integer value = minimum ms between two
    # ``analyze()`` calls. Enforced by ``AnalyzerHub.on_frame_buffer`` via
    # :meth:`_should_run`.
    tick_ms: int | None = None

    def __init__(self) -> None:
        self.enabled = True
        # Negative infinity so the first :meth:`_should_run` check always
        # returns True regardless of ``tick_ms`` -- analyzers fire once on
        # registration rather than silently waiting out an initial window.
        self._last_run_ts: float = float("-inf")

    def _should_run(self, frame: AnalyzerFrame) -> bool:
        """Return True if enough time has passed to invoke :meth:`analyze`.

        Uses the frame's ``monotonic_ts`` rather than calling ``time.monotonic()``
        ourselves so unit tests can drive the clock deterministically by
        constructing frames with known timestamps.
        """
        if self.tick_ms is None:
            return True
        elapsed_ms = (frame.monotonic_ts - self._last_run_ts) * 1000.0
        if elapsed_ms < self.tick_ms:
            return False
        self._last_run_ts = frame.monotonic_ts
        return True

    @abstractmethod
    def analyze(self, frame: AnalyzerFrame) -> list[Event]: ...


class AnalyzerHub(QObject):
    """Dispatches incoming frame buffers to registered analyzers and acts as
    the application-wide event broker.

    Producers
        - Registered :class:`Analyzer` instances whose :meth:`Analyzer.analyze`
          returns events on each frame (gated by ``tick_ms``).
        - Any non-analyzer caller can synthesize events via :meth:`publish` --
          useful for widgets, the trigger engine, or tests.

    Consumers
        Call :meth:`subscribe` with an event-kind string (see :class:`EventKind`)
        and a handler to receive only matching events, or :meth:`subscribe_all`
        to receive everything. Both return an unsubscribe closure; invoke it to
        disconnect cleanly without needing a reference to the internal slot.

    Thread safety
        We piggy-back on Qt's signal/slot machinery: subscribers connected from
        the main thread receive events on the main thread regardless of where
        :meth:`publish` is called from (Qt uses ``AutoConnection``). No extra
        locking needed in user code.
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

    # -- Pub/sub facade -------------------------------------------------------

    def subscribe(self, kind: str, handler: EventHandler) -> Callable[[], None]:
        """Subscribe ``handler`` to events whose ``kind`` matches.

        Returns a zero-arg closure that disconnects this subscription. Stash
        it on the caller if it needs to unsubscribe later (e.g. widget close).
        """

        def _slot(ev: Event) -> None:
            if ev.kind == kind:
                handler(ev)

        self.event_emitted.connect(_slot)

        def _unsubscribe() -> None:
            # PySide6's ``disconnect`` returns bool; wrap it so the public
            # type stays ``Callable[[], None]``.
            self.event_emitted.disconnect(_slot)

        return _unsubscribe

    def subscribe_all(self, handler: EventHandler) -> Callable[[], None]:
        """Subscribe ``handler`` to every event, regardless of kind."""
        self.event_emitted.connect(handler)

        def _unsubscribe() -> None:
            self.event_emitted.disconnect(handler)

        return _unsubscribe

    def publish(self, event: Event) -> None:
        """Emit an event onto the bus from a non-analyzer producer.

        Internally just forwards to ``event_emitted``; exposed as a named
        method so call-sites read as pub/sub rather than Qt-internal.
        """
        self.event_emitted.emit(event)

    # -- Frame dispatch -------------------------------------------------------

    def on_frame_buffer(self, buf: np.ndarray, size: QSize) -> None:  # slot
        if not self._analyzers:
            return
        frame = AnalyzerFrame(buffer=buf, size=size)
        for a in self._analyzers:
            if not a.enabled or not a._should_run(frame):
                continue
            try:
                for event in a.analyze(frame) or []:
                    self.event_emitted.emit(event)
            except Exception:  # pragma: no cover - never let one analyzer kill the hub
                log.exception("analyzer.failed", id=a.id)
