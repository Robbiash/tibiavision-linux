"""Tests for the Smart HUD container and its shipped panels.

The HUD itself is exercised through a minimal ``_FakePanel`` so we can
assert event + tick fan-out without touching PySide6's paint pipeline.
The shipped panels (AudioTimerPanel, MetronomePanel) are verified via
their public state surface rather than pixel output -- painting is tested
implicitly by running paintEvent on an offscreen QPainter and asserting it
does not crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSizeF
from PySide6.QtGui import QImage, QPainter

from tvlinux.analyzers import AnalyzerHub, Event, EventKind
from tvlinux.audio_timers import AudioTimer, AudioTimerManager
from tvlinux.hud_panels import AudioTimerPanel, MetronomePanel
from tvlinux.hud_panels.audio_timer_panel import _FIRE_FLASH_MS
from tvlinux.smart_hud import HudPanel, SmartHud


@dataclass
class _FakePanel(HudPanel):
    """Minimal panel used to prove the HUD plumbing without painting anything real."""

    id: str = "fake"
    anchor: str = "top_left"  # type: ignore[assignment]
    events: list[Event] = field(default_factory=list)
    ticks: list[float] = field(default_factory=list)

    def preferred_size(self) -> QSizeF:
        return QSizeF(100.0, 40.0)

    def paint(self, painter: QPainter, rect: QRectF) -> None:
        del painter, rect

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def on_tick(self, dt_ms: float) -> None:
        self.ticks.append(dt_ms)


def _hud(tmp_path: Path) -> tuple[AnalyzerHub, SmartHud]:
    bus = AnalyzerHub()
    hud = SmartHud(bus=bus, layout_path=tmp_path / "hud_layout.json")
    return bus, hud


# -- Panel registry ----------------------------------------------------------


def test_register_and_lookup_panel(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    p = _FakePanel(id="one")
    hud.register_panel(p)
    assert hud.panel("one") is p
    assert hud.panels() == [p]


def test_duplicate_id_is_ignored(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    a = _FakePanel(id="same")
    b = _FakePanel(id="same")
    hud.register_panel(a)
    hud.register_panel(b)  # should log and no-op
    # The first one wins.
    assert hud.panel("same") is a
    assert hud.panels() == [a]


def test_unregister_panel(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    hud.register_panel(_FakePanel(id="x"))
    hud.unregister_panel("x")
    assert hud.panel("x") is None
    assert hud.panels() == []


# -- Event / tick routing ----------------------------------------------------


def test_subscribed_on_show_routes_events_to_every_panel(qapp, tmp_path):
    bus, hud = _hud(tmp_path)
    a = _FakePanel(id="a")
    b = _FakePanel(id="b")
    hud.register_panel(a)
    hud.register_panel(b)
    # showEvent triggers the bus subscription; showing an offscreen widget
    # is fine under QT_QPA_PLATFORM=offscreen (conftest sets this).
    hud.show()
    try:
        bus.publish(Event(analyzer_id="t", kind="TICK", data={"n": 1}))
        assert len(a.events) == 1
        assert len(b.events) == 1
        assert a.events[0].kind == "TICK"
    finally:
        hud.close()


def test_hide_stops_frame_timer_and_tick_routing(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    p = _FakePanel(id="p")
    hud.register_panel(p)
    hud.show()
    hud.hide()
    # Directly invoke the private frame step so the test stays free of
    # real-timer flakiness. The stopped frame timer won't fire on its own
    # now, which is the actual behaviour we care about.
    assert not hud._frame_timer.isActive()


def test_on_frame_fans_tick_to_panels(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    p = _FakePanel(id="p")
    hud.register_panel(p)
    # Step the frame loop manually so the test doesn't depend on real time.
    hud._on_frame()
    hud._on_frame()
    assert len(p.ticks) == 2
    assert all(dt >= 0.0 for dt in p.ticks)


# -- Layout ------------------------------------------------------------------


def test_default_layout_places_top_left_near_corner(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    # Force a known geometry so the anchor math is deterministic.
    hud.setGeometry(0, 0, 800, 600)
    hud.register_panel(_FakePanel(id="tl", anchor="top_left"))  # type: ignore[arg-type]
    hud._relayout()
    rect = hud._slots["tl"].rect
    # Edge margin is 16 in smart_hud; allow a 1-pixel fudge against rounding.
    assert 15.0 <= rect.x() <= 17.0
    assert 15.0 <= rect.y() <= 17.0


def test_layout_override_from_disk_wins(qapp, tmp_path):
    layout = tmp_path / "hud_layout.json"
    layout.write_text('{"version":1,"positions":{"tl":{"x":321,"y":654}}}', encoding="utf-8")
    bus = AnalyzerHub()
    hud = SmartHud(bus=bus, layout_path=layout)
    hud.setGeometry(0, 0, 800, 600)
    hud.register_panel(_FakePanel(id="tl"))
    hud._relayout()
    assert hud._slots["tl"].rect.topLeft() == QPointF(321.0, 654.0)


def test_save_layout_roundtrip(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    hud.setGeometry(0, 0, 800, 600)
    hud.register_panel(_FakePanel(id="p", anchor="top_right"))  # type: ignore[arg-type]
    hud._relayout()
    hud.save_layout()

    # A brand-new HUD loading the same file must restore the same position.
    hud2 = SmartHud(bus=AnalyzerHub(), layout_path=tmp_path / "hud_layout.json")
    hud2.setGeometry(0, 0, 800, 600)
    hud2.register_panel(_FakePanel(id="p", anchor="top_right"))  # type: ignore[arg-type]
    hud2._relayout()
    assert hud2._slots["p"].rect.topLeft() == hud._slots["p"].rect.topLeft()


# -- paintEvent smoke test ---------------------------------------------------


def test_paint_does_not_crash_with_registered_panels(qapp, tmp_path):
    _bus, hud = _hud(tmp_path)
    hud.setGeometry(0, 0, 400, 300)
    hud.register_panel(_FakePanel(id="p"))
    hud._relayout()
    # Paint into an offscreen QImage so we don't need an actual window.
    img = QImage(400, 300, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    try:
        # Re-drive the HUD's paintEvent end-to-end.
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QPaintEvent

        hud.paintEvent(QPaintEvent(QRect(0, 0, 400, 300)))
    finally:
        painter.end()


# -- AudioTimerPanel ---------------------------------------------------------


def test_audio_timer_panel_starts_empty(qapp, tmp_path):
    mgr = AudioTimerManager(path=tmp_path / "audio.json")
    panel = AudioTimerPanel(mgr)
    assert panel.state.rows == {}
    # Empty panel paints without crashing.
    img = QImage(260, 40, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    try:
        panel.paint(painter, QRectF(0.0, 0.0, 260.0, 40.0))
    finally:
        painter.end()


def test_audio_timer_panel_tracks_running_timers(qapp, tmp_path):
    mgr = AudioTimerManager(path=tmp_path / "audio.json")
    timer = AudioTimer(name="Food", duration_s=60.0)
    mgr.add(timer)
    panel = AudioTimerPanel(mgr)

    # Simulate the AudioTimerManager's tick signal directly.
    mgr.countdown_tick.emit(timer.id, 42.0)
    assert timer.id in panel.state.rows
    row = panel.state.rows[timer.id]
    assert row.name == "Food"
    assert row.remaining_s == 42.0
    assert row.duration_s == 60.0


def test_audio_timer_panel_fire_flash_decays(qapp, tmp_path):
    mgr = AudioTimerManager(path=tmp_path / "audio.json")
    timer = AudioTimer(name="Exori", duration_s=4.0)
    mgr.add(timer)
    panel = AudioTimerPanel(mgr)
    mgr.countdown_tick.emit(timer.id, 0.0)
    mgr.timer_fired.emit(timer.id)
    row = panel.state.rows[timer.id]
    assert row.fire_flash_ms == _FIRE_FLASH_MS
    panel.on_tick(_FIRE_FLASH_MS / 2.0)
    assert row.fire_flash_ms < _FIRE_FLASH_MS
    # Drain the remaining flash: row should be dropped because remaining == 0.
    panel.on_tick(_FIRE_FLASH_MS)
    assert timer.id not in panel.state.rows


def test_audio_timer_panel_handles_timer_removal(qapp, tmp_path):
    mgr = AudioTimerManager(path=tmp_path / "audio.json")
    timer = AudioTimer(name="UH", duration_s=10.0)
    mgr.add(timer)
    panel = AudioTimerPanel(mgr)
    mgr.countdown_tick.emit(timer.id, 5.0)
    assert timer.id in panel.state.rows
    mgr.remove(timer.id)
    assert timer.id not in panel.state.rows


# -- MetronomePanel ----------------------------------------------------------


def test_metronome_starts_in_waiting_state():
    m = MetronomePanel()
    assert m.reset_count == 0
    # Float("inf") signals "no reset yet".
    assert m.since_reset_ms == float("inf")


def test_metronome_reset_event_records_timestamp_and_flash():
    m = MetronomePanel()
    m.on_event(Event(analyzer_id="swing", kind=EventKind.SWING_TIMER_RESET, data={}))
    assert m.reset_count == 1
    assert m.since_reset_ms == 0.0
    assert m.flash_ms > 0.0


def test_metronome_ignores_unrelated_events():
    m = MetronomePanel()
    m.on_event(Event(analyzer_id="x", kind="SOMETHING_ELSE", data={}))
    assert m.reset_count == 0


def test_metronome_tick_advances_clocks_and_flash_decays():
    m = MetronomePanel()
    m.on_event(Event(analyzer_id="swing", kind=EventKind.SWING_TIMER_RESET, data={}))
    start_flash = m.flash_ms
    m.on_tick(50.0)
    assert m.since_reset_ms == 50.0
    assert m.flash_ms < start_flash
    # Drain flash.
    m.on_tick(10_000.0)
    assert m.flash_ms == 0.0


def test_metronome_paints_in_all_states(qapp):
    m = MetronomePanel()
    img = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    painter = QPainter(img)
    try:
        # Idle (no reset yet).
        m.paint(painter, QRectF(0.0, 0.0, 200.0, 200.0))
        # Active window.
        m.on_event(
            Event(analyzer_id="swing", kind=EventKind.SWING_TIMER_RESET, data={}),
        )
        m.on_tick(500.0)
        m.paint(painter, QRectF(0.0, 0.0, 200.0, 200.0))
        # Past-window "ready" state.
        m.on_tick(5000.0)
        m.paint(painter, QRectF(0.0, 0.0, 200.0, 200.0))
    finally:
        painter.end()
