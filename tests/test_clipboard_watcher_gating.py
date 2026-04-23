"""ClipboardWatcher: ensure Hunt Mode gating silences events while off."""

from __future__ import annotations

from pathlib import Path

from tvlinux.analyzers import AnalyzerHub
from tvlinux.clipboard_watcher import ClipboardWatcher
from tvlinux.hunt_mode import HuntModeManager

SAMPLE = """Session: 01:30h
Raw XP Gain: 1,100,000
XP Gain: 1,200,000
Raw XP/h: 740,000
XP/h: 800,000
Loot: 900,000
Supplies: 300,000
Balance: 600,000
Damage: 500,000
Damage/h: 333,333
Healing: 120,000
Healing/h: 80,000
"""


def test_clipboard_silent_when_hunt_mode_off(tmp_path: Path, qapp) -> None:
    hub = AnalyzerHub()
    mode = HuntModeManager(path=tmp_path / "mode.json")
    assert mode.active is False
    events: list[str] = []
    hub.event_emitted.connect(lambda e: events.append(str(e.kind)))
    watcher = ClipboardWatcher(hub, hunt_mode=mode)
    watcher.process_text(SAMPLE)
    assert events == []


def test_clipboard_emits_when_hunt_mode_on(tmp_path: Path, qapp) -> None:
    hub = AnalyzerHub()
    mode = HuntModeManager(path=tmp_path / "mode.json")
    mode.set_active(True)
    events: list[str] = []
    hub.event_emitted.connect(lambda e: events.append(str(e.kind)))
    watcher = ClipboardWatcher(hub, hunt_mode=mode)
    captured: list[tuple] = []
    watcher.hunt_captured.connect(lambda s, t: captured.append((s, t)))
    watcher.process_text(SAMPLE)
    assert events, "expected at least one bus event"
    assert captured, "expected hunt_captured to fire"


def test_clipboard_without_gate_still_works(qapp) -> None:
    hub = AnalyzerHub()
    events: list[str] = []
    hub.event_emitted.connect(lambda e: events.append(str(e.kind)))
    watcher = ClipboardWatcher(hub)  # no hunt_mode
    watcher.process_text(SAMPLE)
    assert events
