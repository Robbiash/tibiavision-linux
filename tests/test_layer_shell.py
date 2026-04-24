"""Tests for the wlr-layer-shell Wayland protocol integration.

These tests deliberately focus on the parts of :mod:`tvlinux.layer_shell`
we can exercise deterministically without an actual Wayland compositor
that supports ``wlr-layer-shell-v1`` (headless CI has neither a
compositor nor the library on the default ``QT_QPA_PLATFORM=offscreen``
backend).

The integration smoke-test -- "mirror actually sits above a fullscreen
KWin surface" -- lives in :file:`docs/qa-checklist.md` as a manual
verification step, because it's only meaningful on a real Plasma
desktop where we can see the mirror stay visible while Tibia is in
F11 fullscreen.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tvlinux import layer_shell


@pytest.fixture(autouse=True)
def _reset_layer_shell_state(monkeypatch):
    """Every test starts with a clean bootstrap state.

    ``prepare_qt_integration`` is intentionally idempotent via module
    globals, which would otherwise leak state between tests -- a test
    that successfully prepares would make subsequent "I'm not on
    Wayland" tests spuriously see ``is_available() is True`` because
    the cached ``_prepare_result`` is still set.
    """
    monkeypatch.setattr(layer_shell, "_prepared", False)
    monkeypatch.setattr(layer_shell, "_prepare_result", False)
    yield


def test_prepare_is_noop_on_non_wayland_session(monkeypatch):
    """On X11 / tty the layer-shell protocol doesn't exist.
    ``prepare_qt_integration`` must report ``False`` and must NOT
    mutate ``QT_WAYLAND_SHELL_INTEGRATION`` -- we don't want to
    accidentally force Qt's Wayland plugin to pick layer-shell on
    a platform where that would break surface creation."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("QT_WAYLAND_SHELL_INTEGRATION", raising=False)

    assert layer_shell.prepare_qt_integration() is False
    assert "QT_WAYLAND_SHELL_INTEGRATION" not in os.environ


def test_prepare_sets_qt_env_var_on_wayland(monkeypatch):
    """On a Wayland session, bootstrap must set
    ``QT_WAYLAND_SHELL_INTEGRATION=layer-shell`` so the Qt Wayland
    platform plugin picks the layer-shell integration at its next
    ``QApplication`` construction. Without this env var (or the
    equivalent ``qputenv`` from ``Shell::useLayerShell``), every
    QWindow ends up as an ``xdg-toplevel`` and ``setLayer`` becomes
    a silent no-op."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("QT_WAYLAND_SHELL_INTEGRATION", raising=False)

    assert layer_shell.prepare_qt_integration() is True
    assert os.environ["QT_WAYLAND_SHELL_INTEGRATION"] == "layer-shell"


def test_prepare_is_idempotent(monkeypatch):
    """Re-preparing must not re-run the expensive library dlopen,
    and must return the first result forever."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    first = layer_shell.prepare_qt_integration()
    # Flip the "session" to X11 after the first call; cached result
    # should persist.
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")

    second = layer_shell.prepare_qt_integration()
    assert first == second


def test_is_available_requires_prepare_to_run_first():
    """Merely being on a Wayland session is not enough; if the
    caller forgot to bootstrap before ``QApplication``, the layer-shell
    path is a trap (windows will silently be xdg-toplevels) so we
    must report unavailable."""
    # _reset_layer_shell_state has set _prepared=False above.
    assert layer_shell.is_available() is False


def test_is_available_true_after_prepare_on_wayland_with_lib(monkeypatch):
    """Happy path: bootstrap ran, lib loads, Wayland session.
    This is the only state in which promote_to_overlay will attempt
    real work."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert layer_shell.prepare_qt_integration() is True
    # is_available also requires the library to be loadable. On hosts
    # where it isn't (headless CI minus the kf6-layer-shell-qt package),
    # skip this assertion -- we've already proved the env-var path.
    if layer_shell._LayerShellLib.load() is None:
        pytest.skip("libLayerShellQtInterface.so.6 not loadable here")
    assert layer_shell.is_available() is True


def test_promote_to_overlay_returns_false_when_unavailable(qapp):
    """When :func:`is_available` is ``False``, promotion must return
    ``False`` without touching the widget. The caller (Application)
    relies on this to decide whether to run the reactive re-raise
    fallback."""
    from PySide6.QtCore import QRect

    from tvlinux.mirror_window import MirrorWindow
    from tvlinux.regions import Region

    region = Region(name="w", rect=QRect(0, 0, 100, 100))
    mirror = MirrorWindow(region)
    try:
        with patch.object(layer_shell, "is_available", return_value=False):
            assert layer_shell.promote_to_overlay(mirror) is False
    finally:
        mirror.close()
        mirror.deleteLater()
        qapp.processEvents()
