"""Tests for :mod:`tvlinux.trigger_engine`.

These drive the engine via ``bus.publish`` rather than running analyzers,
because rules are a pure function of events + state, and we want to cover
the whole action vocabulary without needing real pixel data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect

from tvlinux.analyzers import AnalyzerHub, Event
from tvlinux.profiles import DEFAULT_PROFILE, ProfileManager
from tvlinux.regions import Region, RegionManager
from tvlinux.trigger_engine import (
    Action,
    Condition,
    Rule,
    TriggerEngine,
    default_rules,
)


def _make_engine(tmp_path: Path) -> tuple[AnalyzerHub, ProfileManager, TriggerEngine]:
    bus = AnalyzerHub()
    regions = RegionManager()
    profiles = ProfileManager(regions, path=tmp_path / "profiles.json")
    engine = TriggerEngine(bus=bus, profiles=profiles)
    return bus, profiles, engine


def _event(kind: str, **data: Any) -> Event:
    return Event(analyzer_id="test", kind=kind, data=dict(data))


# -- Rule parsing ------------------------------------------------------------


def test_rule_from_dict_parses_all_fields():
    rule = Rule.from_dict(
        {
            "id": "low-hp",
            "when": "STATS_UPDATE",
            "if": [{"field": "hp", "op": "<", "value": 40}],
            "then": [{"action": "log", "args": {"message": "low hp"}}],
            "cooldown_ms": 500,
        }
    )
    assert rule.id == "low-hp"
    assert rule.when == "STATS_UPDATE"
    assert rule.conditions == [Condition(field="hp", op="<", value=40)]
    assert rule.actions == [Action(action="log", args={"message": "low hp"})]
    assert rule.cooldown_ms == 500


def test_default_rules_include_login_auto_switch():
    rules = default_rules()
    assert any(r.when == "LOGIN_DETECTED" for r in rules)
    auto = next(r for r in rules if r.when == "LOGIN_DETECTED")
    assert any(a.action == "switch_profile" for a in auto.actions)


# -- Condition evaluation ----------------------------------------------------


def test_condition_ops_cover_comparisons():
    ev = _event("FOO", hp=30, name="Cleric")
    assert Condition("hp", "<", 40).matches(ev)
    assert not Condition("hp", "<", 30).matches(ev)
    assert Condition("hp", "<=", 30).matches(ev)
    assert Condition("hp", ">", 20).matches(ev)
    assert Condition("name", "==", "Cleric").matches(ev)
    assert Condition("name", "!=", "Druid").matches(ev)
    assert Condition("name", "in", ["Cleric", "Druid"]).matches(ev)
    assert Condition("name", "not in", ["Druid"]).matches(ev)


def test_condition_missing_field_is_false():
    assert not Condition("mana", "<", 10).matches(_event("FOO", hp=30))


def test_unknown_op_is_rejected_silently():
    # Defensive: a mystery op must NOT evaluate True, so we never run actions
    # against a condition we can't evaluate.
    assert not Condition("hp", "=~", 40).matches(_event("FOO", hp=30))


# -- Engine dispatch ---------------------------------------------------------


def test_rule_fires_when_event_kind_and_conditions_match(qapp, tmp_path):
    bus, _profiles, engine = _make_engine(tmp_path)
    engine.set_rules(
        [
            Rule.from_dict(
                {
                    "id": "low-hp",
                    "when": "STATS_UPDATE",
                    "if": [{"field": "hp", "op": "<", "value": 40}],
                    "then": [{"action": "log", "args": {"message": "low hp"}}],
                }
            )
        ]
    )
    fired: list[str] = []
    engine.rule_fired.connect(fired.append)

    bus.publish(_event("STATS_UPDATE", hp=30))
    assert fired == ["low-hp"]


def test_rule_does_not_fire_when_conditions_fail(qapp, tmp_path):
    bus, _profiles, engine = _make_engine(tmp_path)
    engine.set_rules(
        [
            Rule.from_dict(
                {
                    "id": "low-hp",
                    "when": "STATS_UPDATE",
                    "if": [{"field": "hp", "op": "<", "value": 40}],
                    "then": [{"action": "log", "args": {}}],
                }
            )
        ]
    )
    fired: list[str] = []
    engine.rule_fired.connect(fired.append)

    bus.publish(_event("STATS_UPDATE", hp=80))  # above threshold
    bus.publish(_event("OTHER", hp=10))  # wrong kind

    assert fired == []


def test_cooldown_suppresses_repeat_firings(qapp, tmp_path):
    bus, _profiles, engine = _make_engine(tmp_path)
    engine.set_rules(
        [
            Rule.from_dict(
                {
                    "id": "noisy",
                    "when": "TICK",
                    "if": [],
                    "then": [{"action": "log", "args": {}}],
                    "cooldown_ms": 10_000,
                }
            )
        ]
    )
    fired: list[str] = []
    engine.rule_fired.connect(fired.append)

    for _ in range(5):
        bus.publish(_event("TICK"))
    assert fired == ["noisy"]  # 5 publishes, 1 firing


# -- switch_profile action ---------------------------------------------------


def test_switch_profile_loads_matching_profile(qapp, tmp_path):
    bus, profiles, engine = _make_engine(tmp_path)
    # Create two named profiles.
    profiles.save_profile_as("Mage")
    profiles.save_profile_as("Knight")
    assert profiles.active == "Knight"

    engine.set_rules(default_rules())

    bus.publish(_event("LOGIN_DETECTED", name="Mage"))
    assert profiles.active == "Mage"


def test_switch_profile_unknown_name_is_noop(qapp, tmp_path):
    bus, profiles, engine = _make_engine(tmp_path)
    before = profiles.active
    engine.set_rules(default_rules())

    bus.publish(_event("LOGIN_DETECTED", name="SomeoneElse"))

    assert profiles.active == before


def test_switch_profile_same_name_is_noop(qapp, tmp_path):
    bus, profiles, engine = _make_engine(tmp_path)
    engine.set_rules(default_rules())
    fired: list[str] = []
    engine.rule_fired.connect(fired.append)

    # Name equals current active -> rule matches and fires (we still emit so
    # observers can see the event) but profile itself does not change.
    bus.publish(_event("LOGIN_DETECTED", name=DEFAULT_PROFILE))

    assert profiles.active == DEFAULT_PROFILE
    assert fired == ["auto-switch-profile-on-login"]


# -- publish_event action ----------------------------------------------------


def test_publish_event_chains_onto_bus(qapp, tmp_path):
    bus, _profiles, engine = _make_engine(tmp_path)
    engine.set_rules(
        [
            Rule.from_dict(
                {
                    "id": "echo",
                    "when": "PING",
                    "if": [],
                    "then": [
                        {
                            "action": "publish_event",
                            "args": {"kind": "PONG", "data": {"n": 1}},
                        }
                    ],
                }
            )
        ]
    )
    seen: list[Event] = []
    bus.subscribe("PONG", seen.append)

    bus.publish(_event("PING"))

    assert len(seen) == 1
    assert seen[0].kind == "PONG"
    assert seen[0].data == {"n": 1}


# -- Persistence -------------------------------------------------------------


def test_load_from_file_installs_rules(qapp, tmp_path):
    bus, _profiles, engine = _make_engine(tmp_path)

    triggers_file = tmp_path / "triggers.json"
    triggers_file.write_text(
        json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "id": "hello",
                        "when": "PING",
                        "if": [],
                        "then": [{"action": "log", "args": {}}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert engine.load_from_file(triggers_file) is True
    assert [r.id for r in engine.rules()] == ["hello"]

    fired: list[str] = []
    engine.rule_fired.connect(fired.append)
    bus.publish(_event("PING"))
    assert fired == ["hello"]


def test_load_from_missing_file_returns_false(qapp, tmp_path):
    _bus, _profiles, engine = _make_engine(tmp_path)
    assert engine.load_from_file(tmp_path / "does-not-exist.json") is False
    assert engine.rules() == []


# -- Rule replacement --------------------------------------------------------


def test_set_rules_replaces_previous_subscriptions(qapp, tmp_path):
    bus, _profiles, engine = _make_engine(tmp_path)
    engine.set_rules(
        [
            Rule.from_dict(
                {
                    "id": "first",
                    "when": "EVT",
                    "if": [],
                    "then": [{"action": "log", "args": {}}],
                }
            )
        ]
    )
    fired: list[str] = []
    engine.rule_fired.connect(fired.append)

    bus.publish(_event("EVT"))
    assert fired == ["first"]

    # Replace with an empty rule set; the old subscription should be gone.
    engine.set_rules([])
    fired.clear()
    bus.publish(_event("EVT"))
    assert fired == []


def test_region_manager_region_unused(tmp_path):
    """Sanity: our fixture doesn't leak region state across tests."""
    # Kept as a cheap guard against accidentally coupling profile tests to
    # a specific region layout.
    regions = RegionManager()
    regions.add(Region(name="x", rect=QRect(0, 0, 1, 1)))
    assert len(regions) == 1
