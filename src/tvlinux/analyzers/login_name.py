"""Deprecated OCR-based login detector.

.. deprecated:: Phase 4
    Superseded by :class:`tvlinux.analyzers.preset_watcher.PresetWatcher`,
    which derives the same "character switched" signal from Tibia's own
    ``clientoptions.json`` without OCR.

The class is retained as a thin shim so any ``triggers.json`` written
against the old ``analyzer_id`` keeps loading, and so external docs
pointing at :class:`LoginNameAnalyzer` still import cleanly.
"""

from __future__ import annotations

import warnings

from .base import Analyzer, AnalyzerFrame, Event


class LoginNameAnalyzer(Analyzer):
    """No-op stub kept for backwards compatibility. See module docstring."""

    id = "login_name"
    tick_ms = 2000

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False
        warnings.warn(
            "LoginNameAnalyzer is deprecated; use PresetWatcher "
            "(tvlinux.analyzers.preset_watcher) which reads Tibia's "
            "clientoptions.json instead of OCR.",
            DeprecationWarning,
            stacklevel=2,
        )

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
