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

from .base import Analyzer, AnalyzerFrame, AnalyzerHub, Event, EventHandler, EventKind
from .battle_list import BattleListAnalyzer
from .cooldown_cv import CooldownAnalyzer
from .copilot import CopilotAnalyzer
from .equipment import EquipmentAnalyzer
from .login_name import LoginNameAnalyzer  # deprecated; superseded by PresetWatcher
from .ocr import OCRAnalyzer
from .pixel_watch import PixelWatchAnalyzer
from .preset_watcher import PresetWatcher
from .server_log import ServerLogAnalyzer
from .swing_timer import SwingTimerAnalyzer

# ``EventBus`` is the public name for non-analyzer consumers/producers. It is
# the same object as :class:`AnalyzerHub` -- the hub was always a pub/sub, we
# just give the broker role a name that doesn't scream "analyzers" at the
# trigger engine, HUD, and widget modules that will publish/subscribe too.
EventBus = AnalyzerHub

__all__ = [
    "Analyzer",
    "AnalyzerFrame",
    "AnalyzerHub",
    "BattleListAnalyzer",
    "CooldownAnalyzer",
    "CopilotAnalyzer",
    "EquipmentAnalyzer",
    "Event",
    "EventBus",
    "EventHandler",
    "EventKind",
    "LoginNameAnalyzer",
    "OCRAnalyzer",
    "PixelWatchAnalyzer",
    "PresetWatcher",
    "ServerLogAnalyzer",
    "SwingTimerAnalyzer",
]
