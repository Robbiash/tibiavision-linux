"""Tests for the shared animation primitives.

Under the default offscreen Qt platform :func:`reduce_motion` returns
True so fades and pulses collapse to duration 0. That is the right
behaviour for the rest of the suite -- deterministic snapshots of final
widget state. These tests explicitly set ``TVLINUX_REDUCE_MOTION=0`` on
the animations that need to verify real timing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget

from tvlinux import motion


@pytest.fixture
def full_motion(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Force real timing for the few tests that need to see a live
    # animation. Everything else inherits offscreen reduce-motion.
    monkeypatch.setenv("TVLINUX_REDUCE_MOTION", "0")
    monkeypatch.setenv("QT_QPA_PLATFORM", "minimal")
    yield


def test_motion_tokens_exposed():
    assert motion.MOTION.d.fast < motion.MOTION.d.normal < motion.MOTION.d.slow
    assert motion.MOTION.d.fast >= 60  # sanity: fast is still visible


def test_reduce_motion_true_under_offscreen():
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    assert motion.reduce_motion() is True


def test_fade_snaps_to_end_under_reduce_motion(qapp):
    w = QLabel("x")
    anim = motion.fade(w, start=0.0, end=1.0)
    # duration collapses to 0 and Qt fires finished synchronously.
    assert anim.duration() == 0
    effect = w.graphicsEffect()
    assert isinstance(effect, QGraphicsOpacityEffect)
    # With duration=0 Qt still pumps the animation once; give the event
    # loop a tick so the end value lands.
    qapp.processEvents()
    assert effect.opacity() == pytest.approx(1.0)


def test_fade_reuses_opacity_effect(qapp):
    w = QLabel("x")
    motion.fade(w, start=1.0, end=0.0)
    effect_a = w.graphicsEffect()
    motion.fade(w, start=0.0, end=1.0)
    effect_b = w.graphicsEffect()
    # Reusing the effect keeps any in-flight target stable; creating a
    # fresh effect every time would lose the previous animation target.
    assert effect_a is effect_b


def test_fade_parents_animation_to_widget(full_motion, qapp):
    w = QLabel("x")
    anim = motion.fade(w, start=0.0, end=1.0, duration=80)
    # Animation's parent is the widget, so destroying the widget
    # collects the animation automatically.
    assert anim.parent() is w


def test_cross_fade_swaps_index(qapp):
    stack = QStackedWidget()
    page_a = QLabel("A")
    page_b = QLabel("B")
    stack.addWidget(page_a)
    stack.addWidget(page_b)
    assert stack.currentIndex() == 0
    motion.cross_fade(stack, 1)
    qapp.processEvents()
    assert stack.currentIndex() == 1


def test_cross_fade_noop_on_same_index(qapp):
    stack = QStackedWidget()
    stack.addWidget(QLabel("A"))
    stack.addWidget(QLabel("B"))
    motion.cross_fade(stack, 0)
    assert stack.currentIndex() == 0


def test_cross_fade_ignores_out_of_range(qapp):
    stack = QStackedWidget()
    stack.addWidget(QLabel("A"))
    motion.cross_fade(stack, 5)
    assert stack.currentIndex() == 0


def test_pulse_is_looping_animation(full_motion, qapp):
    w = QLabel("x")
    anim = motion.pulse(w, period_ms=400)
    try:
        assert anim.loopCount() == -1
        assert anim.duration() == 400
        assert anim.state() == QPropertyAnimation.State.Running
    finally:
        motion.stop_pulse(w)


def test_pulse_stop_cancels_animation(qapp):
    w = QLabel("x")
    motion.pulse(w)
    motion.stop_pulse(w)
    assert w.property("_tvlinux_pulse_anim") is None


def test_pulse_snaps_under_reduce_motion(qapp):
    w = QLabel("x")
    # Under offscreen (the default fixture env) pulse must NOT loop --
    # it should just pin the property at max_value so the "active" cue
    # still reads in static renders / screenshots.
    anim = motion.pulse(w, min_value=0.4, max_value=0.9)
    try:
        assert anim.duration() == 0
        assert w.property("pulseT") == pytest.approx(0.9)
    finally:
        motion.stop_pulse(w)


def test_hover_ease_installs_event_filter(qapp):
    w = QWidget()
    filt = motion.hover_ease(w)
    # Widget owns the filter so lifetime matches the widget exactly.
    assert filt.parent() is w


def test_lerp_clamped():
    assert motion.lerp(0.0, 10.0, -1.0) == 0.0
    assert motion.lerp(0.0, 10.0, 0.5) == 5.0
    assert motion.lerp(0.0, 10.0, 2.0) == 10.0
