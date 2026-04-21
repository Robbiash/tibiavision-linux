"""Unit tests for profiles.ProfileManager."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect

from tvlinux.profiles import DEFAULT_PROFILE, ProfileManager
from tvlinux.regions import Region, RegionManager


def _mk(tmp_path: Path) -> tuple[RegionManager, ProfileManager]:
    regions = RegionManager()
    pm = ProfileManager(regions, path=tmp_path / "profiles.json")
    return regions, pm


def test_first_load_creates_default(qapp, tmp_path):
    regions, pm = _mk(tmp_path)
    assert pm.active == DEFAULT_PROFILE
    assert DEFAULT_PROFILE in pm.names()
    assert len(regions) == 0


def test_save_and_load_profile(qapp, tmp_path):
    regions, pm = _mk(tmp_path)
    regions.add(Region(name="A", rect=QRect(0, 0, 10, 10)))
    pm.save_profile_as("MyProfile")

    # A fresh manager on the same file must see the saved profile.
    regions2 = RegionManager()
    pm2 = ProfileManager(regions2, path=tmp_path / "profiles.json")
    assert "MyProfile" in pm2.names()
    assert pm2.active == "MyProfile"
    assert [r.name for r in regions2.all()] == ["A"]


def test_cycle_profile_alphabetical(qapp, tmp_path):
    regions, pm = _mk(tmp_path)
    regions.add(Region(name="A", rect=QRect(0, 0, 10, 10)))
    pm.save_profile_as("Alpha")
    regions.add(Region(name="B", rect=QRect(0, 0, 10, 10)))
    pm.save_profile_as("Beta")
    # Active is now Beta; cycle should go Beta -> Default -> Alpha -> Beta.
    assert pm.active == "Beta"
    assert pm.next_profile() == "Default"
    assert pm.next_profile() == "Alpha"
    assert pm.next_profile() == "Beta"


def test_cannot_delete_default(qapp, tmp_path):
    _regions, pm = _mk(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        pm.delete_profile(DEFAULT_PROFILE)


def test_import_does_not_overwrite(qapp, tmp_path):
    regions, pm = _mk(tmp_path)
    regions.add(Region(name="A", rect=QRect(0, 0, 10, 10)))
    pm.save_profile_as("Shared")

    # Write a profile file that clashes with "Shared".
    export_path = tmp_path / "shared_other.json"
    pm.export_current_to(export_path)

    new_name = pm.import_from(export_path)
    assert new_name == "Shared (2)"
    assert "Shared" in pm.names()
    assert "Shared (2)" in pm.names()
