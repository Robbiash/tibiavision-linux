"""Smoke tests for the new ShellWindow."""

from __future__ import annotations

from pathlib import Path

from tvlinux.audio_timers import AudioTimerManager
from tvlinux.hunt_history import HuntHistoryStore
from tvlinux.hunt_mode import HuntModeManager
from tvlinux.regions import RegionManager
from tvlinux.shell import PAGES, ShellWindow


def _make_shell(tmp_path: Path) -> ShellWindow:
    regions = RegionManager()
    mode = HuntModeManager(path=tmp_path / "mode.json")
    audio = AudioTimerManager(path=tmp_path / "audio.json")
    hist = HuntHistoryStore(path=tmp_path / "hist.json")
    return ShellWindow(
        regions=regions,
        hunt_mode=mode,
        audio_timers=audio,
        hunt_history=hist,
    )


def test_shell_builds_and_has_all_pages(tmp_path: Path, qapp) -> None:
    shell = _make_shell(tmp_path)
    try:
        assert shell.stack.count() == len(PAGES)
        for key, _label, _subtitle in PAGES:
            assert key in shell.pages_by_key
    finally:
        shell.close()
        shell.deleteLater()
        qapp.processEvents()


def test_shell_nav_switches_pages(tmp_path: Path, qapp) -> None:
    shell = _make_shell(tmp_path)
    try:
        shell.navigate_to("hunt_history")
        assert shell.stack.currentWidget() is shell.hunt_history_page
        shell.navigate_to("settings")
        assert shell.stack.currentWidget() is shell.settings_page
    finally:
        shell.close()
        shell.deleteLater()
        qapp.processEvents()


def test_shell_status_footer_updates_on_hunt_mode_toggle(tmp_path: Path, qapp) -> None:
    shell = _make_shell(tmp_path)
    try:
        assert "off" in shell.footer._hunt_label.text().lower()
        shell._mode.set_active(True)
        qapp.processEvents()
        assert "on" in shell.footer._hunt_label.text().lower()
    finally:
        shell.close()
        shell.deleteLater()
        qapp.processEvents()
