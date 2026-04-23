"""Unit tests for :mod:`tvlinux.tibia_data`."""

from __future__ import annotations

import json
from pathlib import Path

from tvlinux.tibia_data import (
    client_options_path,
    current_preset_name,
    find_data_dir,
    iter_hotkey_bindings,
    preset_names,
    read_client_options,
)


def _write_fixture(root: Path, options: dict) -> Path:
    conf = root / "conf"
    conf.mkdir(parents=True, exist_ok=True)
    target = conf / "clientoptions.json"
    target.write_text(json.dumps(options), encoding="utf-8")
    return target


CANONICAL_OPTIONS: dict = {
    "hotkeyOptions": {
        "currentHotkeySetName": "Sydnee",
        "hotkeySets": {
            "Sydnee": {
                "chatOff": [
                    {
                        "keysequence": "F1",
                        "actionsetting": {"words": "exura vita"},
                    },
                    {
                        "keysequence": "F2",
                        "actionsetting": {"useObject": 237, "useType": "useOnYourself"},
                    },
                    {
                        "keysequence": "F3",
                        "actionsetting": {"useObject": 9999999},
                    },
                    {
                        "keysequence": "F4",
                        "actionsetting": {"action": "AttackFirstTarget"},
                    },
                    {
                        "keysequence": "F5",
                        "actionsetting": {
                            "action": "TriggerActionButton_1.13",
                        },
                    },
                ],
                "actionBarOptions": {
                    "mappings": [
                        {
                            "actionBar": 1,
                            "actionButton": 13,
                            "actionsetting": {"useObject": 3031},
                        },
                    ],
                },
            },
            "Alt": {
                "chatOff": [],
            },
        },
    }
}


def test_find_data_dir_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TIBIAVISION_TIBIA_DATA", str(tmp_path))
    assert find_data_dir() == tmp_path


def test_find_data_dir_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("TIBIAVISION_TIBIA_DATA", str(tmp_path / "does-not-exist"))
    assert find_data_dir() is None


def test_client_options_path_resolves_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TIBIAVISION_TIBIA_DATA", str(tmp_path))
    target = _write_fixture(tmp_path, CANONICAL_OPTIONS)
    assert client_options_path() == target


def test_read_client_options_parses_json(tmp_path):
    target = _write_fixture(tmp_path, CANONICAL_OPTIONS)
    loaded = read_client_options(target)
    assert loaded is not None
    assert loaded["hotkeyOptions"]["currentHotkeySetName"] == "Sydnee"


def test_read_client_options_handles_missing_and_invalid(tmp_path):
    assert read_client_options(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    assert read_client_options(bad) is None


def test_current_preset_name_reads_active_preset():
    assert current_preset_name(CANONICAL_OPTIONS) == "Sydnee"
    assert current_preset_name({"hotkeyOptions": {}}) is None
    assert current_preset_name({}) is None


def test_preset_names_lists_all():
    assert sorted(preset_names(CANONICAL_OPTIONS)) == ["Alt", "Sydnee"]


def test_iter_hotkey_bindings_resolves_spells_items_actions_and_indirection():
    bindings = list(iter_hotkey_bindings(CANONICAL_OPTIONS))
    by_key = {b.keysequence: b for b in bindings}

    # Spell: direct ``words`` value becomes the label, kind=spell.
    assert by_key["F1"].kind == "spell"
    assert by_key["F1"].label == "exura vita"

    # Known item id: resolved via the bundled object_names.json (237 ==
    # great mana potion).
    assert by_key["F2"].kind == "item"
    assert by_key["F2"].object_id == 237
    assert "potion" in by_key["F2"].label.lower()

    # Unknown item id falls back to ``#<id>``.
    assert by_key["F3"].kind == "item"
    assert by_key["F3"].label == "#9999999"

    # Built-in action gets a friendly label.
    assert by_key["F4"].kind == "action"
    assert by_key["F4"].label == "Attack"

    # TriggerActionButton_X.Y indirection hops through action bar mappings
    # and resolves to the eventual useObject. 3031 == gold coin in the
    # bundled object_names.json.
    assert by_key["F5"].kind == "item"
    assert by_key["F5"].object_id == 3031


def test_iter_hotkey_bindings_respects_explicit_preset():
    # "Alt" has no bindings -> empty.
    assert list(iter_hotkey_bindings(CANONICAL_OPTIONS, "Alt")) == []


def test_iter_hotkey_bindings_missing_preset_is_empty():
    assert list(iter_hotkey_bindings(CANONICAL_OPTIONS, "Ghost")) == []
