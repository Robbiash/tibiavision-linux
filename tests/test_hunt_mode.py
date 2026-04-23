"""HuntModeManager: persistence, toggle, and config updates."""

from __future__ import annotations

from pathlib import Path

from tvlinux.hunt_mode import HuntModeConfig, HuntModeManager


def test_default_config_is_off(tmp_path: Path, qapp) -> None:
    mgr = HuntModeManager(path=tmp_path / "mode.json")
    assert mgr.active is False
    assert mgr.config.auto_log_to_history is True


def test_set_active_emits_toggled_and_persists(tmp_path: Path, qapp) -> None:
    p = tmp_path / "mode.json"
    mgr = HuntModeManager(path=p)
    seen: list[bool] = []
    mgr.toggled.connect(seen.append)
    mgr.set_active(True)
    assert seen == [True]
    mgr.set_active(True)  # idempotent
    assert seen == [True]
    mgr2 = HuntModeManager(path=p)
    assert mgr2.active is True


def test_update_config_merges(tmp_path: Path, qapp) -> None:
    mgr = HuntModeManager(path=tmp_path / "mode.json")
    mgr.update_config(auto_log_to_history=False)
    assert mgr.config.auto_log_to_history is False
    assert mgr.config.active is False  # unchanged


def test_update_config_ignores_legacy_keys(tmp_path: Path, qapp) -> None:
    """Old configs carried trigger_key / calibration fields; these must
    be silently dropped so upgrading users don't crash on load."""
    mgr = HuntModeManager(path=tmp_path / "mode.json")
    mgr.update_config(trigger_key="f11", min_refresh_interval_sec=30)  # type: ignore[call-arg]
    assert not hasattr(mgr.config, "trigger_key")
    assert mgr.config.auto_log_to_history is True


def test_config_roundtrip() -> None:
    cfg = HuntModeConfig(active=True, auto_log_to_history=False)
    cfg2 = HuntModeConfig.from_dict(cfg.to_dict())
    assert cfg2 == cfg


def test_from_dict_drops_legacy_fields() -> None:
    """Legacy JSON on disk (trigger_key, copy anchors, ...) must load."""
    legacy = {
        "active": True,
        "auto_log_to_history": False,
        "trigger_key": "f11",
        "trigger_key_enabled": True,
        "min_refresh_interval_sec": 30,
        "auto_fire_fallback_sec": 0,
        "tibia_window_substring": "Tibia",
        "copy_anchor_hunt": {"right_click_x": 1, "right_click_y": 2, "menu_x": 3, "menu_y": 4},
        "copy_anchor_party": None,
    }
    cfg = HuntModeConfig.from_dict(legacy)
    assert cfg.active is True
    assert cfg.auto_log_to_history is False
