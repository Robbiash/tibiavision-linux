"""Tests for the EventBus (formerly AnalyzerHub) pub/sub API.

We exercise the broker directly rather than plumbing a fake ``Analyzer``
through ``on_frame_buffer``, because the subscribe/publish facade is the
contract that widgets, the trigger engine, and the HUD will use.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize

from tvlinux.analyzers import (
    Analyzer,
    AnalyzerFrame,
    AnalyzerHub,
    Event,
    EventBus,
)


def _make_event(kind: str, **data: object) -> Event:
    return Event(analyzer_id="test", kind=kind, data=dict(data))


def test_event_bus_is_analyzer_hub(qapp):
    assert EventBus is AnalyzerHub


def test_subscribe_filters_by_kind(qapp):
    hub = AnalyzerHub()
    seen: list[Event] = []
    hub.subscribe("FOO", seen.append)

    hub.publish(_make_event("BAR", x=1))
    hub.publish(_make_event("FOO", x=2))
    hub.publish(_make_event("BAZ", x=3))

    assert len(seen) == 1
    assert seen[0].kind == "FOO"
    assert seen[0].data == {"x": 2}


def test_subscribe_all_receives_every_event(qapp):
    hub = AnalyzerHub()
    seen: list[str] = []
    hub.subscribe_all(lambda ev: seen.append(ev.kind))

    hub.publish(_make_event("FOO"))
    hub.publish(_make_event("BAR"))
    hub.publish(_make_event("BAZ"))

    assert seen == ["FOO", "BAR", "BAZ"]


def test_unsubscribe_closure_disconnects(qapp):
    hub = AnalyzerHub()
    seen: list[Event] = []
    unsubscribe = hub.subscribe("FOO", seen.append)

    hub.publish(_make_event("FOO", n=1))
    unsubscribe()
    hub.publish(_make_event("FOO", n=2))

    assert len(seen) == 1
    assert seen[0].data == {"n": 1}


def test_unsubscribe_all_closure_disconnects(qapp):
    hub = AnalyzerHub()
    seen: list[Event] = []
    unsubscribe = hub.subscribe_all(seen.append)

    hub.publish(_make_event("FOO"))
    unsubscribe()
    hub.publish(_make_event("BAR"))

    assert len(seen) == 1


def test_multiple_subscribers_same_kind_all_called(qapp):
    hub = AnalyzerHub()
    a: list[int] = []
    b: list[int] = []
    hub.subscribe("TICK", lambda ev: a.append(ev.data["n"]))
    hub.subscribe("TICK", lambda ev: b.append(ev.data["n"]))

    hub.publish(_make_event("TICK", n=7))

    assert a == [7]
    assert b == [7]


def test_register_and_unregister_toggle_active_changed(qapp):
    class _Noop(Analyzer):
        id = "noop"

        def analyze(self, frame: AnalyzerFrame) -> list[Event]:
            return []

    hub = AnalyzerHub()
    transitions: list[bool] = []
    hub.active_changed.connect(transitions.append)

    noop = _Noop()
    hub.register(noop)
    hub.unregister(noop)

    assert transitions == [True, False]


def test_on_frame_buffer_routes_events_to_subscribers(qapp):
    class _Loud(Analyzer):
        id = "loud"

        def analyze(self, frame: AnalyzerFrame) -> list[Event]:
            return [Event(analyzer_id=self.id, kind="LOUD", data={"ts": frame.monotonic_ts})]

    hub = AnalyzerHub()
    hub.register(_Loud())

    seen: list[Event] = []
    hub.subscribe("LOUD", seen.append)

    buf = np.zeros((2, 2, 4), dtype=np.uint8)
    hub.on_frame_buffer(buf, QSize(2, 2))

    assert len(seen) == 1
    assert seen[0].analyzer_id == "loud"
