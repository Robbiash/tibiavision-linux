"""Tests for :class:`tvlinux.analyzers.pixel_watch.PixelWatchAnalyzer`."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect, QSize

from tvlinux.analyzers import EventKind
from tvlinux.analyzers.base import AnalyzerFrame
from tvlinux.analyzers.pixel_watch import PixelWatchAnalyzer
from tvlinux.regions import Region, RegionManager


def _frame(buf: np.ndarray, ts: float) -> AnalyzerFrame:
    h, w = buf.shape[0], buf.shape[1]
    return AnalyzerFrame(buffer=buf, size=QSize(w, h), monotonic_ts=ts)


def _solid(shape=(32, 32, 4), value: int = 0) -> np.ndarray:
    arr = np.full(shape, value, dtype=np.uint8)
    arr[..., 3] = 255
    return arr


def _stripes(shape=(32, 32, 4)) -> np.ndarray:
    arr = np.zeros(shape, dtype=np.uint8)
    # Alternating columns so the dhash has plenty of edges.
    arr[:, ::2, :3] = 255
    arr[..., 3] = 255
    return arr


def _watched_region() -> Region:
    return Region(
        name="watch",
        rect=QRect(0, 0, 32, 32),
        watch_mode="change",
    )


def test_first_frame_does_not_emit(qapp):
    regions = RegionManager()
    regions.add(_watched_region())
    analyzer = PixelWatchAnalyzer(regions)
    events = analyzer.analyze(_frame(_solid(value=10), ts=0.0))
    assert events == []


def test_identical_frames_do_not_emit(qapp):
    regions = RegionManager()
    regions.add(_watched_region())
    analyzer = PixelWatchAnalyzer(regions)
    buf = _solid(value=10)
    assert analyzer.analyze(_frame(buf, ts=0.0)) == []
    assert analyzer.analyze(_frame(buf, ts=1.0)) == []


def test_changed_frame_emits_with_region_metadata(qapp):
    regions = RegionManager()
    region = _watched_region()
    regions.add(region)
    analyzer = PixelWatchAnalyzer(regions)
    analyzer.analyze(_frame(_solid(value=10), ts=0.0))
    events = analyzer.analyze(_frame(_stripes(), ts=1.0))
    assert len(events) == 1
    event = events[0]
    assert event.kind == EventKind.PIXEL_WATCH_CHANGED
    assert event.data["region_id"] == str(region.id)
    assert event.data["name"] == "watch"


def test_cooldown_gates_rapid_reemission(qapp):
    regions = RegionManager()
    regions.add(_watched_region())
    analyzer = PixelWatchAnalyzer(regions)
    analyzer.analyze(_frame(_solid(value=10), ts=0.0))
    # First real change: fires.
    first = analyzer.analyze(_frame(_stripes(), ts=1.0))
    assert len(first) == 1
    # Different content again within 500 ms -> suppressed by cooldown.
    second = analyzer.analyze(_frame(_solid(value=128), ts=1.2))
    assert second == []
    # After the cooldown has elapsed we can fire again on new content.
    third = analyzer.analyze(_frame(_stripes(), ts=2.0))
    assert len(third) == 1


def test_watch_mode_off_clears_state(qapp):
    regions = RegionManager()
    region = _watched_region()
    regions.add(region)
    analyzer = PixelWatchAnalyzer(regions)
    analyzer.analyze(_frame(_solid(value=10), ts=0.0))

    region.watch_mode = "off"
    regions.update(region)
    analyzer.analyze(_frame(_stripes(), ts=1.0))  # should clear state
    assert analyzer.state_for(str(region.id)) is None

    # Re-enabling should require a new seed frame before emitting.
    region.watch_mode = "change"
    regions.update(region)
    assert analyzer.analyze(_frame(_solid(value=200), ts=2.0)) == []
