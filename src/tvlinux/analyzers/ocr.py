"""Stub: OCR analyzer (HP/mana/stamina numbers).

v1 placeholder. In v2 this will wire up ``pytesseract`` against user-configured sub-regions
and emit ``vitals`` events with integers parsed from the Tibia HUD text.

Intentionally left as a no-op so importing the module on v1 doesn't pull in tesseract.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class OCRAnalyzer(Analyzer):
    id = "ocr"

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False  # off by default; opt-in in v2 settings UI

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
