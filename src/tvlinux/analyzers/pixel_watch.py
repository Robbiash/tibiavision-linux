"""General-purpose pixel-change detector.

Watches any :class:`~tvlinux.regions.Region` whose ``watch_mode`` is
``"change"`` and publishes :data:`EventKind.PIXEL_WATCH_CHANGED` every
time the pixels inside that region change meaningfully. "Meaningfully"
here means: the region's 8x8 difference hash (dhash) no longer matches
the previously-seen one, and a per-region cooldown has elapsed since the
last event.

Why dhash
---------
dhash is a tiny, cheap, tolerant-to-minor-compression perceptual hash:
eight bytes of state per region, no external deps. It catches the
kind of changes we care about -- icon appearing / disappearing, a
status effect toggling, an alert flashing on -- while ignoring the
sub-pixel flicker you get on every captured frame from the compositor.

Integration
-----------
This analyzer plugs into the existing :class:`AnalyzerHub` frame loop.
It receives the whole captured frame (BGRA ndarray, ``size`` is source
pixel space) and crops per-region. No new plumbing -- just a normal
registered analyzer, gated at ``tick_ms = 150`` to keep the per-frame
cost trivial even if the user watches a dozen regions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..regions import RegionManager
from .base import Analyzer, AnalyzerFrame, Event, EventKind

# Hash grid width/height. 8x8 = 64-bit hash, which fits in a Python int
# and is plenty of resolution for the "is this icon on or off" question
# that almost every user of this analyzer is asking.
_HASH_GRID = 8

# Minimum gap between two PIXEL_WATCH_CHANGED events for the same region.
# 500 ms filters out flicker during capture handoffs without noticeably
# delaying the first real change.
_COOLDOWN_MS = 500


@dataclass
class _RegionState:
    """Last seen hash and last-fired timestamp for one watched region."""

    last_hash: int | None = None
    last_fired_ts: float = float("-inf")


class PixelWatchAnalyzer(Analyzer):
    """Emit ``PIXEL_WATCH_CHANGED`` when a watched region's pixels change."""

    id = "pixel_watch"
    tick_ms = 150

    def __init__(self, regions: RegionManager) -> None:
        super().__init__()
        self._regions = regions
        self._state: dict[str, _RegionState] = {}
        self.enabled = True

    # -- Introspection (tests) ------------------------------------------------

    def state_for(self, region_id: str) -> _RegionState | None:
        return self._state.get(region_id)

    # -- Analyzer contract ----------------------------------------------------

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        events: list[Event] = []
        buf = frame.buffer
        if buf is None or buf.ndim < 2:
            return events
        h, w = buf.shape[0], buf.shape[1]

        # Walk a snapshot of regions so mid-iteration mutations (user
        # adds / removes a region while we analyze) don't blow up.
        for region in list(self._regions):
            if region.watch_mode != "change":
                # Drop stale state so a re-enable starts fresh (no false
                # positive from a stored hash minutes old).
                self._state.pop(str(region.id), None)
                continue

            rid = str(region.id)
            rect = region.rect
            x1 = max(0, rect.x())
            y1 = max(0, rect.y())
            x2 = min(w, rect.x() + rect.width())
            y2 = min(h, rect.y() + rect.height())
            if x2 <= x1 or y2 <= y1:
                # Region is entirely outside the captured frame -- skip
                # rather than hashing a zero-size slice.
                continue

            crop = buf[y1:y2, x1:x2]
            current_hash = _dhash(crop, _HASH_GRID)
            state = self._state.setdefault(rid, _RegionState())

            if state.last_hash is None:
                state.last_hash = current_hash
                continue

            if current_hash == state.last_hash:
                continue

            state.last_hash = current_hash
            now_ms = frame.monotonic_ts * 1000.0
            if (now_ms - state.last_fired_ts * 1000.0) < _COOLDOWN_MS:
                continue
            state.last_fired_ts = frame.monotonic_ts

            events.append(
                Event(
                    analyzer_id=self.id,
                    kind=EventKind.PIXEL_WATCH_CHANGED,
                    data={
                        "region_id": rid,
                        "name": region.name,
                    },
                )
            )

        return events


def _dhash(crop: np.ndarray, grid: int = _HASH_GRID) -> int:
    """Compute a classic difference hash of a BGRA / BGR image crop.

    The algorithm:

    1. Convert to single-channel grayscale (mean over the last axis).
    2. Downsample to ``(grid+1, grid)`` pixels using a nearest-neighbour
       block mean so we stay numpy-only (no Pillow dep).
    3. Compare horizontally adjacent pixels; set a bit wherever the left
       pixel is darker than the right. Pack into a ``grid*grid``-bit int.

    The whole thing is ~0.1 ms on an 80x80 crop; unrolling further would
    be wasted effort.
    """
    if crop.size == 0:
        return 0
    # Grayscale. Works for HxWx4 (BGRA), HxWx3, and HxW. ``[..., :3]``
    # drops the alpha channel if present so we're averaging luminance.
    gray = crop[..., :3].mean(axis=2) if crop.ndim == 3 else crop.astype(np.float32)

    target_h = grid
    target_w = grid + 1

    # Cheap "resize": split each axis into ``target`` blocks and take
    # the mean. Slightly blurry but that's a feature for hashing.
    src_h, src_w = gray.shape
    if src_h < target_h or src_w < target_w:
        # Pad tiny crops so the hash keeps its byte width; nearest-neighbour
        # stretch by repeating rows / cols.
        gray = np.kron(gray, np.ones((max(1, target_h // max(1, src_h) + 1), 1)))
        src_h = gray.shape[0]
        gray = np.kron(gray, np.ones((1, max(1, target_w // max(1, src_w) + 1))))
        src_w = gray.shape[1]

    h_edges = np.linspace(0, src_h, target_h + 1).astype(int)
    w_edges = np.linspace(0, src_w, target_w + 1).astype(int)

    small = np.empty((target_h, target_w), dtype=np.float32)
    for i in range(target_h):
        y0, y1 = h_edges[i], max(h_edges[i] + 1, h_edges[i + 1])
        for j in range(target_w):
            x0, x1 = w_edges[j], max(w_edges[j] + 1, w_edges[j + 1])
            small[i, j] = gray[y0:y1, x0:x1].mean()

    diff = small[:, 1:] > small[:, :-1]

    # Pack a bool array into an int64 (fits 64 bits = 8x8 grid).
    bits = 0
    flat = diff.flatten()
    for bit in flat:
        bits = (bits << 1) | (1 if bit else 0)
    return int(bits)


__all__ = ["PixelWatchAnalyzer"]
