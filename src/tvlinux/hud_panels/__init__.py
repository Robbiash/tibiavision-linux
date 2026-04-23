"""Concrete HUD panels for :class:`tvlinux.smart_hud.SmartHud`.

Each panel is a single file implementing :class:`~tvlinux.smart_hud.HudPanel`.
Adding a new one requires no changes to :mod:`tvlinux.smart_hud` -- only a
new file in this package and a single ``register_panel(...)`` call in
:mod:`tvlinux.app`.
"""

from __future__ import annotations

from ..smart_hud import HudPanel
from .audio_timer_panel import AudioTimerPanel
from .hotbar_panel import HotbarPanel
from .hunt_stats_panel import HuntStatsPanel
from .metronome_panel import MetronomePanel
from .party_panel import PartyPanel

__all__ = [
    "AudioTimerPanel",
    "HotbarPanel",
    "HudPanel",
    "HuntStatsPanel",
    "MetronomePanel",
    "PartyPanel",
]
