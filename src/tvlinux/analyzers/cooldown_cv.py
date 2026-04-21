"""Stub: computer-vision cooldown detector.

Planned v2 implementation:
  1. Accept a list of "spell slot" sub-rects from the user (drawn in the region picker
     alongside the mirror itself).
  2. On each frame sub-sample every ~100 ms, compute desaturation ratio per slot.
  3. When a slot crosses the "desaturated -> saturated" threshold, emit a
     ``cooldown_ready`` event for that slot, which the app can route to a TibiaAudio
     timer or a visual flash.

Kept as a pure stub in v1.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class CooldownAnalyzer(Analyzer):
    id = "cooldown_cv"

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
