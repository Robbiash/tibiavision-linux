"""Stub: battle-list / chat intelligence.

v2 will OCR the battle list region every ~500 ms and match names against a user-owned
PK list, emitting ``pk_detected`` events that the app can surface as a loud audio alert.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class BattleListAnalyzer(Analyzer):
    id = "battle_list"

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
