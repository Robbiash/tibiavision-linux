"""v2 AI analyzers package.

All modules in this package consume **numpy frame buffers** produced by
``tvlinux.capture.CaptureCore.frame_buffer_ready``. They must remain strictly read-only
on the pixel data - the BattlEye safety story documented in ``docs/safety.md`` is
independent of how we process those pixels.

Contents are *stubs* for v1; each one defines the interface and a no-op implementation
so v2 can flesh them out without refactoring the rest of the codebase.

Architecture:

  CaptureCore.frame_buffer_ready(np.ndarray)
        |
        v
    AnalyzerHub (fans out to registered analyzers)
        |
        +--> OCRAnalyzer        (Tesseract: HP/mana numbers, stamina)
        +--> CooldownAnalyzer   (OpenCV: template match, desaturation -> spell-ready event)
        +--> BattleListAnalyzer (OCR + known-name matcher -> PK alert)
        +--> CopilotAnalyzer    (optional: snapshot -> Claude Opus 4.7 API -> advice)

Each analyzer receives an ``AnalyzerFrame`` (ndarray + source size + timestamp) and emits
typed ``Event`` dataclasses. The ``Application`` object subscribes to events and either
shows them inline on a mirror window, triggers a TibiaAudio timer, or both.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, AnalyzerHub, Event

__all__ = ["Analyzer", "AnalyzerFrame", "AnalyzerHub", "Event"]
