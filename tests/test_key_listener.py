"""Tests for the PassiveKeyListener scaffolding (no real backend grabs)."""

from __future__ import annotations

from tvlinux.key_listener import (
    BackendStatus,
    PassiveKeyListener,
    _pynput_key_name,
    detect_backend,
)


def test_detect_backend_returns_status() -> None:
    status = detect_backend()
    assert isinstance(status, BackendStatus)
    assert status.backend in ("x11", "wayland", "unavailable")


def test_listener_focus_filter_blocks_wrong_window(qapp, monkeypatch) -> None:
    listener = PassiveKeyListener(trigger_key="space", tibia_window_substring="Tibia")
    monkeypatch.setattr("tvlinux.key_listener.active_window_title", lambda: "Firefox")
    fired: list[str] = []
    listener.key_pressed.connect(fired.append)
    listener._handle_key("space")
    assert fired == []


def test_listener_focus_filter_allows_match(qapp, monkeypatch) -> None:
    listener = PassiveKeyListener(trigger_key="space", tibia_window_substring="Tibia")
    monkeypatch.setattr(
        "tvlinux.key_listener.active_window_title",
        lambda: "Tibia - World: Antica",
    )
    fired: list[str] = []
    listener.key_pressed.connect(fired.append)
    listener._handle_key("space")
    assert fired == ["space"]


def test_listener_empty_substring_disables_focus_filter(qapp, monkeypatch) -> None:
    listener = PassiveKeyListener(trigger_key="space", tibia_window_substring="")
    monkeypatch.setattr("tvlinux.key_listener.active_window_title", lambda: "")
    fired: list[str] = []
    listener.key_pressed.connect(fired.append)
    listener._handle_key("space")
    assert fired == ["space"]


def test_listener_ignores_wrong_key(qapp, monkeypatch) -> None:
    listener = PassiveKeyListener(trigger_key="space", tibia_window_substring="")
    fired: list[str] = []
    listener.key_pressed.connect(fired.append)
    listener._handle_key("q")
    listener._handle_key("SPACE")
    assert fired == ["SPACE"]


def test_set_trigger_key_normalises(qapp) -> None:
    listener = PassiveKeyListener(trigger_key="space")
    listener.set_trigger_key("  F1  ")
    assert listener._trigger_key == "f1"
    listener.set_trigger_key("")
    assert listener._trigger_key == "space"


def test_pynput_key_name_char_and_name() -> None:
    class _C:
        char = "A"

    class _N:
        char = None
        name = "space"

    class _X:
        char = None
        name = None

    assert _pynput_key_name(_C()) == "a"
    assert _pynput_key_name(_N()) == "space"
    assert _pynput_key_name(_X()) is None


def test_stop_is_idempotent(qapp) -> None:
    listener = PassiveKeyListener()
    listener.stop()
    listener.stop()
