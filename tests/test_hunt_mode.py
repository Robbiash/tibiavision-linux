"""HuntModeManager: persistence, toggle, and config updates."""

from __future__ import annotations

from pathlib import Path

from tvlinux.hunt_mode import CopyAnchor, HuntModeConfig, HuntModeManager


def test_default_config_is_off(tmp_path: Path, qapp) -> None:
    mgr = HuntModeManager(path=tmp_path / "mode.json")
    assert mgr.active is False
    assert mgr.config.trigger_key == "space"
    assert mgr.config.min_refresh_interval_sec == 60


def test_set_active_emits_toggled_and_persists(tmp_path: Path, qapp) -> None:
    p = tmp_path / "mode.json"
    mgr = HuntModeManager(path=p)
    seen: list[bool] = []
    mgr.toggled.connect(seen.append)
    mgr.set_active(True)
    assert seen == [True]
    mgr.set_active(True)  # idempotent
    assert seen == [True]
    # Reloaded state matches persisted state.
    mgr2 = HuntModeManager(path=p)
    assert mgr2.active is True


def test_update_config_merges(tmp_path: Path, qapp) -> None:
    mgr = HuntModeManager(path=tmp_path / "mode.json")
    mgr.update_config(trigger_key="f11", min_refresh_interval_sec=30)
    assert mgr.config.trigger_key == "f11"
    assert mgr.config.min_refresh_interval_sec == 30
    assert mgr.config.active is False  # unchanged


def test_copy_anchor_roundtrip() -> None:
    a = CopyAnchor(10, 20, 30, 40)
    d = a.to_dict()
    b = CopyAnchor.from_dict(d)
    assert b == a
    assert CopyAnchor.from_dict(None) is None
    assert CopyAnchor.from_dict({"bad": "payload"}) is None


def test_config_roundtrip() -> None:
    cfg = HuntModeConfig(
        active=True,
        trigger_key="f12",
        min_refresh_interval_sec=45,
        copy_anchor_hunt=CopyAnchor(1, 2, 3, 4),
    )
    cfg2 = HuntModeConfig.from_dict(cfg.to_dict())
    assert cfg2 == cfg
