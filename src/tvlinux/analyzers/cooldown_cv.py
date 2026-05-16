"""Special-spell cooldown detector for region mirrors.

User goal: select a tight region over a spell cooldown timer (e.g. Exori Gran),
and blink the region border when remaining time reaches a threshold (default 1.9s).

Design constraints:
- No hard dependency on external OCR engines (tesseract/opencv are optional extras).
- Must run from the same read-only capture stream as the rest of the app.
- Needs to handle occasional cooldown procs (e.g. -2s jumps) without requiring
  keyboard hooks or game memory reads.

Implementation strategy:
- For tracked regions, extract a small high-pass binary mask of the *top* area
  where Tibia cooldown text usually appears.
- Detect cooldown start by a strong transition from the baseline "idle" signal.
- Drive remaining-time from the spell preset's base cooldown.
- Detect probable proc jumps as unusually large per-frame mask deltas and subtract
  the preset proc delta (2s for Exori Gran).
- Emit ``EventKind.SPELL_COOLDOWN_SOON`` once per cooldown when the predicted
  remaining time drops below the alert threshold.

This is intentionally lightweight and heuristic-based. Users should capture
regions tightly around the cooldown number for best results.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..regions import RegionManager
from .base import Analyzer, AnalyzerFrame, Event, EventKind


@dataclass(frozen=True)
class _SpellPreset:
    cooldown_s: float
    alert_s: float
    proc_delta_s: float


_SPELL_PRESETS: dict[str, _SpellPreset] = {
    "exori_gran": _SpellPreset(cooldown_s=6.0, alert_s=1.9, proc_delta_s=2.0),
    "executors_throw": _SpellPreset(cooldown_s=10.0, alert_s=1.9, proc_delta_s=0.0),
}


@dataclass
class _CooldownState:
    prev_mask: np.ndarray | None = None
    baseline_signal: float = 0.0
    running: bool = False
    cooldown_started_ts: float = float("-inf")
    cooldown_end_ts: float = float("-inf")
    alert_sent: bool = False
    low_signal_streak: int = 0
    diff_ema: float = 0.0
    last_proc_ts: float = float("-inf")


class CooldownAnalyzer(Analyzer):
    id = "cooldown_cv"
    # ~10 Hz is enough for a 1.9s threshold alert and keeps CPU use tiny.
    tick_ms = 100

    def __init__(self, regions: RegionManager) -> None:
        super().__init__()
        self._regions = regions
        self._state: dict[str, _CooldownState] = {}
        self.enabled = True

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        events: list[Event] = []
        buf = frame.buffer
        if buf is None or buf.ndim < 2:
            return events

        h, w = buf.shape[0], buf.shape[1]
        now = frame.monotonic_ts
        seen_ids: set[str] = set()

        for region in list(self._regions):
            spell = getattr(region, "cooldown_spell", "off")
            if not region.track_cooldown or spell not in _SPELL_PRESETS:
                continue
            rid = str(region.id)
            seen_ids.add(rid)
            preset = _SPELL_PRESETS[spell]

            x1 = max(0, region.rect.x())
            y1 = max(0, region.rect.y())
            x2 = min(w, region.rect.x() + region.rect.width())
            y2 = min(h, region.rect.y() + region.rect.height())
            if x2 <= x1 or y2 <= y1:
                continue

            mask = _cooldown_text_mask(buf[y1:y2, x1:x2])
            if mask is None:
                continue
            signal = float(mask.sum())
            st = self._state.setdefault(rid, _CooldownState())

            diff = 0.0
            if st.prev_mask is not None and st.prev_mask.shape == mask.shape:
                diff = float(np.mean(mask != st.prev_mask))
            st.prev_mask = mask

            if st.diff_ema <= 0.0:
                st.diff_ema = diff
            else:
                st.diff_ema = st.diff_ema * 0.85 + diff * 0.15

            if not st.running:
                # Baseline while idle. This stabilizes quickly on "no number shown".
                if st.baseline_signal <= 0.0:
                    if signal <= 4.0:
                        st.baseline_signal = signal
                else:
                    idle_update_cap = max(st.baseline_signal * 1.4 + 3.0, 6.0)
                    if signal <= idle_update_cap:
                        st.baseline_signal = st.baseline_signal * 0.90 + signal * 0.10
                start_signal = max(st.baseline_signal * 1.6, st.baseline_signal + 4.0, 6.0)
                start_diff = max(0.015, st.diff_ema * 0.8)
                if signal >= start_signal and diff >= start_diff:
                    st.running = True
                    st.cooldown_started_ts = now
                    st.cooldown_end_ts = now + preset.cooldown_s
                    st.alert_sent = False
                    st.low_signal_streak = 0
                    st.last_proc_ts = float("-inf")
                continue

            # Running cooldown ---------------------------------------------------------
            low_signal_cutoff = max(st.baseline_signal + 4.0, st.baseline_signal * 1.15)
            if signal <= low_signal_cutoff:
                st.low_signal_streak += 1
            else:
                st.low_signal_streak = 0
            if st.low_signal_streak >= 3:
                st.running = False
                st.alert_sent = False
                continue

            remaining = st.cooldown_end_ts - now
            if remaining <= 0.0:
                st.running = False
                st.alert_sent = False
                continue

            # Proc compensation: detect unusually large visual jumps and pull
            # the predicted end time forward by the preset delta.
            proc_jump_threshold = max(0.04, st.diff_ema * 1.5)
            can_proc = (
                preset.proc_delta_s > 0.0
                and (now - st.cooldown_started_ts) > 0.8
                and (now - st.last_proc_ts) > 0.9
                and remaining > (preset.alert_s + 0.4)
            )
            if can_proc and diff >= proc_jump_threshold:
                st.cooldown_end_ts = max(now, st.cooldown_end_ts - preset.proc_delta_s)
                st.last_proc_ts = now
                remaining = st.cooldown_end_ts - now

            if not st.alert_sent and remaining <= preset.alert_s:
                st.alert_sent = True
                events.append(
                    Event(
                        analyzer_id=self.id,
                        kind=EventKind.SPELL_COOLDOWN_SOON,
                        data={
                            "region_id": rid,
                            "name": region.name,
                            "spell": spell,
                            "remaining_s": max(0.0, remaining),
                            "alert_s": preset.alert_s,
                        },
                    )
                )

        # Drop stale state for regions that were deleted or untracked.
        for rid in list(self._state.keys()):
            if rid not in seen_ids:
                self._state.pop(rid, None)

        return events


def _cooldown_text_mask(crop: np.ndarray) -> np.ndarray | None:
    """Extract a tiny binary mask for the cooldown-number area.

    Returns a fixed-shape boolean array (16x24) or ``None`` if the crop is too
    small / empty.
    """
    if crop.size == 0:
        return None
    if crop.ndim == 3:
        gray = crop[..., :3].mean(axis=2).astype(np.float32)
    else:
        gray = crop.astype(np.float32)

    h, w = gray.shape
    if h < 8 or w < 8:
        return None

    # Trim borders (mirror frame / icon edge noise), then focus on the upper
    # area where Tibia cooldown text is typically painted.
    margin_x = max(1, int(w * 0.12))
    margin_y = max(1, int(h * 0.10))
    if (w - 2 * margin_x) < 6 or (h - 2 * margin_y) < 6:
        return None
    inner = gray[margin_y : h - margin_y, margin_x : w - margin_x]
    upper_h = max(4, int(inner.shape[0] * 0.65))
    upper = inner[:upper_h, :]

    # Cheap 3x3 blur via direct neighbourhood average (numpy-only).
    pad = np.pad(upper, 1, mode="edge")
    blur = (
        pad[:-2, :-2]
        + pad[:-2, 1:-1]
        + pad[:-2, 2:]
        + pad[1:-1, :-2]
        + pad[1:-1, 1:-1]
        + pad[1:-1, 2:]
        + pad[2:, :-2]
        + pad[2:, 1:-1]
        + pad[2:, 2:]
    ) / 9.0

    high = np.clip(upper - blur + 128.0, 0.0, 255.0)
    if high.size == 0:
        return None
    threshold = max(135.0, float(np.percentile(high, 98.2)))
    mask = high >= threshold

    return _pool_mask(mask, target_h=16, target_w=24)


def _pool_mask(mask: np.ndarray, *, target_h: int, target_w: int) -> np.ndarray:
    """Resize ``mask`` by block pooling (keeps us numpy-only)."""
    src_h, src_w = mask.shape
    h_edges = np.linspace(0, src_h, target_h + 1).astype(int)
    w_edges = np.linspace(0, src_w, target_w + 1).astype(int)
    out = np.zeros((target_h, target_w), dtype=bool)
    for i in range(target_h):
        y0, y1 = h_edges[i], max(h_edges[i] + 1, h_edges[i + 1])
        for j in range(target_w):
            x0, x1 = w_edges[j], max(w_edges[j] + 1, w_edges[j + 1])
            block = mask[y0:y1, x0:x1]
            if block.size == 0:
                continue
            out[i, j] = float(block.mean()) >= 0.2
    return out
