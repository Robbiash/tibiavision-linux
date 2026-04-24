"""Tests for :class:`tvlinux.clipboard_watcher.ClipboardWatcher`.

We avoid poking the real OS clipboard by driving ``process_text`` directly
-- that is the same entrypoint the ``dataChanged`` slot hits after
reading the clipboard text.
"""

from __future__ import annotations

from tvlinux.analyzers import AnalyzerHub, Event, EventKind
from tvlinux.clipboard_watcher import ClipboardWatcher

SOLO = """\
Session: 01:00h
Raw XP Gain: 100,000
XP Gain: 200,000
Raw XP/h: 100,000
XP/h: 200,000
Loot: 1,000,000
Supplies: 500,000
Balance: 500,000
Damage: 1,000,000
Damage/h: 1,000,000
Healing: 100,000
Healing/h: 100,000
"""

PARTY = """\
Session: 02:00h
Loot Type: Market
Loot: 2,000,000
Supplies: 500,000
Balance: 1,500,000
Alice
    Loot: 1,500,000
    Supplies: 250,000
    Balance: 1,250,000
Bob
    Loot: 500,000
    Supplies: 250,000
    Balance: 250,000
"""


def _collect(bus: AnalyzerHub, kinds: list[str]) -> list[Event]:
    received: list[Event] = []

    def _collector(ev: Event) -> None:
        if ev.kind in kinds:
            received.append(ev)

    bus.subscribe_all(_collector)
    return received


def test_clipboard_watcher_publishes_hunt_update(qapp):
    bus = AnalyzerHub()
    received = _collect(bus, [EventKind.HUNT_STATS_UPDATE, EventKind.PARTY_HUNT_UPDATE])
    watcher = ClipboardWatcher(bus, clipboard=None)  # no clipboard == test-safe
    watcher.process_text(SOLO)
    kinds = [e.kind for e in received]
    assert kinds == [EventKind.HUNT_STATS_UPDATE]
    assert received[0].data["balance"] == 500_000


def test_clipboard_watcher_publishes_party_update(qapp):
    bus = AnalyzerHub()
    received = _collect(bus, [EventKind.HUNT_STATS_UPDATE, EventKind.PARTY_HUNT_UPDATE])
    watcher = ClipboardWatcher(bus, clipboard=None)
    watcher.process_text(PARTY)
    kinds = [e.kind for e in received]
    assert kinds == [EventKind.PARTY_HUNT_UPDATE]
    assert [m["name"] for m in received[0].data["members"]] == ["Alice", "Bob"]


def test_clipboard_watcher_dedupes_repeat_copies(qapp):
    bus = AnalyzerHub()
    received = _collect(bus, [EventKind.HUNT_STATS_UPDATE])
    watcher = ClipboardWatcher(bus, clipboard=None)
    watcher.process_text(SOLO)
    watcher.process_text(SOLO)
    watcher.process_text(SOLO)
    assert len(received) == 1


def test_clipboard_watcher_ignores_non_tibia_text(qapp):
    bus = AnalyzerHub()
    received = _collect(bus, [EventKind.HUNT_STATS_UPDATE, EventKind.PARTY_HUNT_UPDATE])
    watcher = ClipboardWatcher(bus, clipboard=None)
    watcher.process_text("hello world")
    watcher.process_text("")
    watcher.process_text("   \n  ")
    assert received == []


def test_clipboard_watcher_resumes_after_non_match(qapp):
    bus = AnalyzerHub()
    received = _collect(bus, [EventKind.HUNT_STATS_UPDATE])
    watcher = ClipboardWatcher(bus, clipboard=None)
    watcher.process_text("not tibia")
    watcher.process_text(SOLO)
    assert len(received) == 1


class _FakeMode:
    """Minimal HuntModeManager stand-in for gating tests."""

    def __init__(self, active: bool = False) -> None:
        self.active = active


def test_clipboard_watcher_emits_ignored_signal_when_hunt_mode_off(qapp):
    bus = AnalyzerHub()
    received = _collect(bus, [EventKind.HUNT_STATS_UPDATE, EventKind.PARTY_HUNT_UPDATE])
    mode = _FakeMode(active=False)
    watcher = ClipboardWatcher(bus, clipboard=None, hunt_mode=mode)

    ignored: list[int] = []
    watcher.hunt_ignored_while_off.connect(lambda: ignored.append(1))

    watcher.process_text(SOLO)

    assert received == []
    assert len(ignored) == 1

    mode.active = True
    watcher.process_text(SOLO)
    assert len(received) == 1


def test_clipboard_watcher_ignores_non_tibia_text_quietly_while_off(qapp):
    bus = AnalyzerHub()
    mode = _FakeMode(active=False)
    watcher = ClipboardWatcher(bus, clipboard=None, hunt_mode=mode)

    ignored: list[int] = []
    watcher.hunt_ignored_while_off.connect(lambda: ignored.append(1))

    watcher.process_text("just some random text")
    watcher.process_text("")

    assert ignored == []
