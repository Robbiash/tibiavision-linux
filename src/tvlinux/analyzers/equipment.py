"""Stub: equipment-slot charge reader (ring + amulet expiry flashers).

Planned v2 implementation:

1. User designates ring and amulet slot ROIs.
2. Every ~500 ms, OCR the small charge-count number overlaid on each slot.
3. Track the last seen value per slot; emit :data:`EventKind.EQUIP_UPDATE`
   whenever the number changes, with payload
   ``{"slot": "ring"|"amulet", "charges": int, "previous": int | None}``.
4. The HUD subscribes and flashes a low-charge warning at <= 1 charge.

Kept as a pure stub in v1. The 500 ms cadence balances latency (a ring that
expires mid-fight matters within a second) against OCR cost (tesseract is
not free).
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class EquipmentAnalyzer(Analyzer):
    id = "equipment"
    tick_ms = 500

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
