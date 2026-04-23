"""Tests for :class:`tvlinux.analyzers.preset_watcher.PresetWatcher`.

We avoid depending on ``QFileSystemWatcher`` timings by driving
:meth:`PresetWatcher.check_now` explicitly, which performs the same
logical re-read the fs event would trigger.
"""

from __future__ import annotations

import json
from pathlib import Path

from tvlinux.analyzers import AnalyzerHub, Event, EventKind
from tvlinux.analyzers.preset_watcher import PresetWatcher


def _write_options(path: Path, preset: str | None) -> None:
    payload = {"hotkeyOptions": {"currentHotkeySetName": preset} if preset else {}}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _collect(bus: AnalyzerHub) -> list[Event]:
    out: list[Event] = []
    bus.subscribe(EventKind.LOGIN_DETECTED, out.append)
    return out


def test_preset_watcher_seeds_from_initial_file_without_emitting(qapp, tmp_path):
    target = tmp_path / "clientoptions.json"
    _write_options(target, "Sydnee")

    bus = AnalyzerHub()
    captured = _collect(bus)

    watcher = PresetWatcher(bus, path=target)
    assert watcher.last_preset == "Sydnee"
    assert captured == []


def test_preset_watcher_emits_on_change(qapp, tmp_path):
    target = tmp_path / "clientoptions.json"
    _write_options(target, "Sydnee")

    bus = AnalyzerHub()
    captured = _collect(bus)
    watcher = PresetWatcher(bus, path=target)

    _write_options(target, "Abbadinos")
    watcher.check_now()

    assert len(captured) == 1
    event = captured[0]
    assert event.kind == EventKind.LOGIN_DETECTED
    assert event.data["name"] == "Abbadinos"
    assert event.data["previous"] == "Sydnee"
    assert event.data["source"] == "clientoptions"


def test_preset_watcher_ignores_identical_rereads(qapp, tmp_path):
    target = tmp_path / "clientoptions.json"
    _write_options(target, "Sydnee")

    bus = AnalyzerHub()
    captured = _collect(bus)
    watcher = PresetWatcher(bus, path=target)

    # File rewritten with identical contents -- no LOGIN_DETECTED.
    _write_options(target, "Sydnee")
    watcher.check_now()
    assert captured == []


def test_preset_watcher_noop_when_path_missing(qapp, tmp_path):
    # File doesn't exist; watcher still constructs and simply reports no
    # active preset. check_now() is a safe no-op.
    bus = AnalyzerHub()
    captured = _collect(bus)
    watcher = PresetWatcher(bus, path=tmp_path / "missing.json")
    watcher.check_now()
    assert watcher.last_preset is None
    assert captured == []
