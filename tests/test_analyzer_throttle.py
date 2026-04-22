"""Tests for the per-analyzer ``tick_ms`` throttle primitive on :class:`Analyzer`.

We drive the clock by constructing :class:`AnalyzerFrame` instances with
explicit ``monotonic_ts`` values so the test is deterministic and does not
sleep.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize

from tvlinux.analyzers import Analyzer, AnalyzerFrame, AnalyzerHub, Event


class _Dummy(Analyzer):
    id = "dummy"

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return [Event(analyzer_id=self.id, kind="TICK", data={"ts": frame.monotonic_ts})]


def _frame(ts: float) -> AnalyzerFrame:
    return AnalyzerFrame(
        buffer=np.zeros((2, 2, 4), dtype=np.uint8),
        size=QSize(2, 2),
        monotonic_ts=ts,
    )


def test_no_tick_ms_always_runs():
    a = _Dummy()
    assert a.tick_ms is None
    for ts in (0.0, 0.001, 0.002, 1.0):
        assert a._should_run(_frame(ts)) is True


def test_tick_ms_skips_calls_within_window():
    a = _Dummy()
    a.tick_ms = 100

    assert a._should_run(_frame(0.000)) is True  # first call always runs
    assert a._should_run(_frame(0.050)) is False  # 50 ms later - skipped
    assert a._should_run(_frame(0.099)) is False  # 99 ms later - still skipped
    assert a._should_run(_frame(0.110)) is True  # 110 ms since last True -> runs
    assert a._should_run(_frame(0.150)) is False  # 40 ms since last run -> skipped
    assert a._should_run(_frame(0.220)) is True  # 110 ms -> runs


def test_tick_ms_uses_frame_timestamp_not_wall_clock():
    """Rewinding the frame timestamp does not accidentally trigger a run."""
    a = _Dummy()
    a.tick_ms = 50
    assert a._should_run(_frame(10.0)) is True
    # Ten seconds of wall time could pass here; what matters is the frame ts.
    assert a._should_run(_frame(10.02)) is False


def test_hub_gates_analyze_by_tick_ms(qapp):
    """Back-to-back on_frame_buffer calls must be throttled when tick_ms is set.

    The hub stamps each frame with ``time.monotonic()`` itself, so if we fire
    six frames in quick succession at a 10-second throttle, only the first
    should reach :meth:`Analyzer.analyze`.
    """
    a = _Dummy()
    a.tick_ms = 10_000  # 10 s window -- no way all six fit inside one test run
    hub = AnalyzerHub()
    hub.register(a)

    seen: list[Event] = []
    hub.subscribe("TICK", seen.append)

    buf = np.zeros((2, 2, 4), dtype=np.uint8)
    for _ in range(6):
        hub.on_frame_buffer(buf, QSize(2, 2))

    assert len(seen) == 1
