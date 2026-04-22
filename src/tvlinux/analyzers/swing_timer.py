"""Stub: auto-attack swing-timer detector ("The Metronome").

Planned v2 implementation:

1. User designates a tiny ROI over the fist / sword weapon icon (drawn in the
   region picker alongside mirrors).
2. On every frame, compute a cheap statistic over the ROI -- mean HSV
   saturation is usually enough to catch Tibia's desaturate-on-swing flash.
3. When the statistic crosses the "reset" edge (desaturated -> saturated),
   emit a :data:`EventKind.SWING_TIMER_RESET` event whose ``data`` carries the
   monotonic timestamp so the HUD can schedule the next expected swing.

Every-frame cadence is intentional: the swing window is short (~2 s) and the
HUD's Metronome needs sub-100 ms resolution to feel tight. The ROI is a few
dozen pixels, so a per-frame mean is negligible on the main thread.

Kept as a pure stub in v1.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class SwingTimerAnalyzer(Analyzer):
    id = "swing_timer"
    # None = run on every frame. See module docstring for the rationale.
    tick_ms = None

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False  # opt-in: requires the user to pick a weapon ROI

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
