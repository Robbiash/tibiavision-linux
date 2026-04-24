"""Unified animation primitives for the UI.

Motivation: Qt Style Sheets can't ease ``:hover`` transitions and each
feature that wants an animation otherwise re-implements a one-off
``QPropertyAnimation`` with a hand-picked duration. This module gives
us a single source of truth for timing (:data:`MOTION`) and a tiny set
of helpers that the rest of the UI can call without thinking about
animation lifetime management.

Key concerns handled here once:

- **Parenting.** Every animation is parented to the widget it targets
  so it is collected with the widget. No dangling ``QPropertyAnimation``
  leaks on page reloads or modal close.
- **Re-entrance.** Helpers that own animations (fade, cross-fade, pulse)
  stop any previously-started animation on the same widget before
  starting a new one. Without this, rapid hover/leave events stack up
  competing animations and the easing looks stuttery.
- **Zero-motion fallback.** If the host has ``QT_QPA_PLATFORM=offscreen``
  (our test runner) or the user opts out via ``TVLINUX_REDUCE_MOTION=1``,
  animations run with ``duration=0`` so they snap instantly. Tests stay
  deterministic and accessibility-conscious users get an escape hatch.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QTimer,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget


@dataclass(frozen=True)
class Durations:
    """Canonical animation durations in milliseconds.

    - ``fast``: hover / micro-interaction feedback.
    - ``normal``: page transitions, emphasis changes.
    - ``slow``: opening modals, large fades.
    """

    fast: int = 120
    normal: int = 200
    slow: int = 320


@dataclass(frozen=True)
class Easings:
    """Easing curve presets.

    - ``standard``: default for "symmetric" property changes.
    - ``enter``: content arriving on screen (decelerate).
    - ``exit``: content leaving screen (accelerate).
    """

    standard: QEasingCurve.Type = QEasingCurve.Type.InOutCubic
    enter: QEasingCurve.Type = QEasingCurve.Type.OutCubic
    exit: QEasingCurve.Type = QEasingCurve.Type.InCubic


@dataclass(frozen=True)
class Motion:
    d: Durations = field(default_factory=Durations)
    e: Easings = field(default_factory=Easings)


MOTION = Motion()


def reduce_motion() -> bool:
    """Return True if animations should snap rather than play.

    True under the test runner (offscreen Qt) or when the user exports
    ``TVLINUX_REDUCE_MOTION=1``. Feature code should not branch on this
    itself; the helpers below already honour it.
    """
    if os.environ.get("TVLINUX_REDUCE_MOTION") == "1":
        return True
    return os.environ.get("QT_QPA_PLATFORM", "") == "offscreen"


def _effective_duration(requested: int) -> int:
    return 0 if reduce_motion() else max(0, requested)


def _ensure_opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    """Attach (or reuse) a ``QGraphicsOpacityEffect`` on ``widget``.

    Fading a ``QWidget`` in Qt requires a ``QGraphicsOpacityEffect`` on
    the graphics effect slot. We store the effect so repeated fades
    don't create a fresh effect (which would stomp any pending fade).
    """
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        return effect
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(1.0)
    widget.setGraphicsEffect(effect)
    return effect


def _stop_and_replace(
    owner: QObject, attr: str, animation: QPropertyAnimation
) -> QPropertyAnimation:
    """Stop any prior animation stored at ``owner.attr`` and store ``animation``.

    Lets helpers use a dynamic property as a single slot for "the
    animation currently driving this widget". Replacing cleanly is the
    only reliable way to cancel easing in flight.
    """
    previous = owner.property(attr)
    if isinstance(previous, QPropertyAnimation):
        previous.stop()
        previous.deleteLater()
    owner.setProperty(attr, animation)
    return animation


def fade(
    widget: QWidget,
    *,
    start: float,
    end: float,
    duration: int | None = None,
    easing: QEasingCurve.Type | None = None,
    on_done: Callable[[], None] | None = None,
) -> QPropertyAnimation:
    """Animate ``widget`` opacity from ``start`` to ``end``.

    Attaches (and reuses) a ``QGraphicsOpacityEffect``. Returns the
    running animation so callers can chain behaviour off
    ``finished``; the helper itself fires ``on_done`` on finish for
    the common case of "swap the page after fade-out".
    """
    effect = _ensure_opacity_effect(widget)
    effect.setOpacity(start)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(_effective_duration(MOTION.d.normal if duration is None else duration))
    anim.setEasingCurve(easing if easing is not None else MOTION.e.standard)
    if on_done is not None:
        anim.finished.connect(on_done)
    _stop_and_replace(widget, "_tvlinux_fade_anim", anim)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def cross_fade(
    stack: QStackedWidget,
    new_index: int,
    *,
    duration: int | None = None,
) -> None:
    """Cross-fade between pages in a ``QStackedWidget``.

    Fades the current page out, swaps ``currentIndex``, then fades the
    new page in. No-op if the index is already current or out of
    range.
    """
    if new_index == stack.currentIndex() or not 0 <= new_index < stack.count():
        return
    current = stack.currentWidget()
    incoming = stack.widget(new_index)
    if incoming is None:
        return
    d = _effective_duration(MOTION.d.normal if duration is None else duration) // 2

    def swap() -> None:
        stack.setCurrentIndex(new_index)
        fade(
            incoming,
            start=0.0,
            end=1.0,
            duration=d,
            easing=MOTION.e.enter,
        )

    if current is None or d == 0:
        # Snap: tests and reduce-motion users just want the swap.
        stack.setCurrentIndex(new_index)
        _ensure_opacity_effect(incoming).setOpacity(1.0)
        return

    fade(
        current,
        start=1.0,
        end=0.0,
        duration=d,
        easing=MOTION.e.exit,
        on_done=swap,
    )


def pulse(
    widget: QWidget,
    *,
    prop: bytes = b"pulseT",
    min_value: float = 0.55,
    max_value: float = 1.0,
    period_ms: int = 1600,
) -> QPropertyAnimation:
    """Start a looping ``min_value <-> max_value`` pulse on ``widget``.

    ``prop`` is a Qt property name on the widget (usually a dynamic
    ``float`` property) that ``paintEvent`` reads to interpolate a
    colour / glow. Call :func:`stop_pulse` to cancel.
    """
    anim = QPropertyAnimation(widget, prop, widget)
    anim.setStartValue(min_value)
    anim.setKeyValueAt(0.5, max_value)
    anim.setEndValue(min_value)
    anim.setDuration(_effective_duration(period_ms))
    anim.setEasingCurve(MOTION.e.standard)
    anim.setLoopCount(-1)
    _stop_and_replace(widget, "_tvlinux_pulse_anim", anim)
    if anim.duration() == 0:
        # Reduce-motion: keep the widget pinned at max_value so the
        # visual "active" cue still reads, just without animation.
        widget.setProperty(bytes(prop).decode("ascii"), max_value)
        widget.update()
        return anim
    anim.start()
    return anim


def stop_pulse(widget: QWidget) -> None:
    """Cancel any pulse started by :func:`pulse` on ``widget``."""
    previous = widget.property("_tvlinux_pulse_anim")
    if isinstance(previous, QPropertyAnimation):
        previous.stop()
        previous.deleteLater()
    widget.setProperty("_tvlinux_pulse_anim", None)


class _HoverFilter(QObject):
    """Event filter that maps Qt Enter/Leave events to user callbacks.

    Installed lazily by :func:`hover_bind` and owned by the widget so
    lifetime matches the subject naturally.
    """

    def __init__(
        self,
        widget: QWidget,
        on_enter: Callable[[], None],
        on_leave: Callable[[], None],
    ) -> None:
        super().__init__(widget)
        self._on_enter = on_enter
        self._on_leave = on_leave

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        etype = event.type()
        if etype == QEvent.Type.Enter:
            self._on_enter()
        elif etype == QEvent.Type.Leave:
            self._on_leave()
        return False


def hover_bind(
    widget: QWidget,
    *,
    on_enter: Callable[[], None],
    on_leave: Callable[[], None],
) -> _HoverFilter:
    """Install a hover filter on ``widget`` that calls user hooks.

    Saves feature code from repeating the ``installEventFilter`` /
    ``eventFilter`` boilerplate for the common Enter / Leave pattern.
    The filter is owned by the widget so it lives exactly as long as
    the widget does.
    """
    filt = _HoverFilter(widget, on_enter, on_leave)
    widget.installEventFilter(filt)
    return filt


def hover_ease(
    widget: QWidget,
    *,
    prop: bytes = b"hoverProgress",
    duration: int | None = None,
) -> _HoverFilter:
    """Convenience: animate a hover progress property 0 <-> 1 on enter/leave.

    The widget should define a dynamic float property ``hoverProgress``
    (via ``setProperty`` on construction) and read it in ``paintEvent``
    to interpolate colours. Returns the installed hover filter.
    """
    d_ms = _effective_duration(MOTION.d.fast if duration is None else duration)

    def animate_to(end: float) -> None:
        start = widget.property(bytes(prop).decode("ascii"))
        start_f = float(start) if isinstance(start, int | float) else 0.0
        anim = QPropertyAnimation(widget, prop, widget)
        anim.setStartValue(start_f)
        anim.setEndValue(end)
        anim.setDuration(d_ms)
        anim.setEasingCurve(MOTION.e.standard)
        _stop_and_replace(widget, "_tvlinux_hover_anim", anim)
        if d_ms == 0:
            widget.setProperty(bytes(prop).decode("ascii"), end)
            widget.update()
            return
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    return hover_bind(widget, on_enter=lambda: animate_to(1.0), on_leave=lambda: animate_to(0.0))


def lerp(a: float, b: float, t: float) -> float:
    """Linear interpolation; widget paint code uses this to tint colours."""
    return a + (b - a) * max(0.0, min(1.0, t))


__all__ = [
    "MOTION",
    "Durations",
    "Easings",
    "Motion",
    "cross_fade",
    "fade",
    "hover_bind",
    "hover_ease",
    "lerp",
    "pulse",
    "reduce_motion",
    "stop_pulse",
]


def _defer(callback: Callable[[], None], delay_ms: int = 0) -> None:
    """Run ``callback`` after ``delay_ms`` on the event loop.

    Exposed as an internal helper for callers that need to defer work
    until the next tick (e.g. settle layout after a fade-in).
    """
    QTimer.singleShot(delay_ms, callback)
