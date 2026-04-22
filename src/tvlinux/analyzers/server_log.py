"""Stub: server-log loot parser (Profit / Waste widget feed).

Planned v2 implementation:

1. User designates a ROI over Tibia's server-log panel.
2. Every ~1 s, OCR the region, isolate the green "Loot of..." lines by
   filtering on green pixel dominance *before* OCR so we skip the noisier
   yellow/white chat.
3. Parse item names and counts; de-duplicate against an in-memory ring so
   the same log line isn't re-emitted when it stays on screen.
4. Emit :data:`EventKind.LOOT_LOGGED` with
   ``{"items": [{"name": str, "count": int}], "raw": str}``.

The live profit widget then looks each item up in a small value dictionary
and maintains a running gp/hour.

Kept as a pure stub in v1.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class ServerLogAnalyzer(Analyzer):
    id = "server_log"
    tick_ms = 1000

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
