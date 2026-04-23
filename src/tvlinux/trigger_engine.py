"""Rule-based trigger engine on top of the EventBus.

Subscribes to events the analyzers publish (see :mod:`tvlinux.analyzers`) and
runs user-declared rules of the shape::

    IF <event kind> AND <conditions on event.data> THEN <actions>

The point of this module is to keep **policy** (what to do when something
happens) out of **analyzers** (detect that something happened) and out of
**UI** (render the result). Both producers and consumers stay small and
testable; the engine is the one place you look when you want to know "why
did the profile switch just now?".

Schema (both dict and JSON)
===========================

::

    {
      "version": 1,
      "rules": [
        {
          "id": "unique-string",
          "when": "LOGIN_DETECTED",
          "if": [
            {"field": "hp", "op": "<", "value": 40}
          ],
          "then": [
            {"action": "switch_profile", "args": {"name_from": "name"}}
          ],
          "cooldown_ms": 2000
        }
      ]
    }

Conditions are ANDed; an empty ``if`` list matches any event. ``cooldown_ms``
is optional; when set, the rule will not fire more often than every N ms.

Actions (v1 vocabulary)
=======================

- ``switch_profile`` -- args ``{"name_from": "<field>"}``. Reads that field
  from ``event.data`` and calls :meth:`tvlinux.profiles.ProfileManager.load_profile`.
  If the profile does not exist we log a warning and stay on the current
  profile (no auto-create, no fallback to Default -- less disruptive).
- ``publish_event`` -- args ``{"kind": str, "data": dict}``. Emits a
  synthetic event onto the bus. Lets rules chain without hard-coding.
- ``log`` -- args ``{"level": "info"|"warning"|"error", "message": str}``.
  Useful when authoring rules interactively.

Threading
=========

The engine runs on the Qt main thread (same as ``EventBus`` subscribers).
All actions are expected to be fast; no action in v1 blocks.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .analyzers import Event, EventBus
from .logging_config import get_logger
from .profiles import ProfileManager

log = get_logger(__name__)

SCHEMA_VERSION = 1

# -- Operators used in rule conditions. Kept explicit + minimal so we can
#    refuse anything we don't know how to evaluate (rather than silently
#    evaluating a mystery op to False).
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
}


@dataclass
class Condition:
    """A single ``event.data[field] <op> value`` check."""

    field: str
    op: str
    value: Any

    def matches(self, event: Event) -> bool:
        if self.op not in _OPS:
            log.warning("trigger.unknown_op", op=self.op)
            return False
        if self.field not in event.data:
            return False
        return _OPS[self.op](event.data[self.field], self.value)


@dataclass
class Action:
    """A single action to run when a rule fires."""

    action: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Rule:
    """A declarative pub/sub rule. See the module docstring for the schema."""

    id: str
    when: str
    conditions: list[Condition] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    cooldown_ms: int = 0
    # Monotonic timestamp of the last time this rule's actions ran. Starts at
    # -inf so the first matching event always fires, regardless of cooldown.
    _last_fired_ts: float = field(default=float("-inf"), repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        return cls(
            id=str(data["id"]),
            when=str(data["when"]),
            conditions=[
                Condition(
                    field=str(c["field"]),
                    op=str(c["op"]),
                    value=c.get("value"),
                )
                for c in data.get("if", [])
            ],
            actions=[
                Action(
                    action=str(a["action"]),
                    args=dict(a.get("args") or {}),
                )
                for a in data.get("then", [])
            ],
            cooldown_ms=int(data.get("cooldown_ms", 0)),
        )

    def matches(self, event: Event) -> bool:
        return all(c.matches(event) for c in self.conditions)


def default_rules() -> list[Rule]:
    """Rules shipped with the app when no ``triggers.json`` exists.

    The one non-negotiable default is the LOGIN_DETECTED -> switch_profile
    rule that implements dynamic vocation switching. Everything else we
    leave for the user to author.
    """
    return [
        Rule.from_dict(
            {
                "id": "auto-switch-profile-on-login",
                "when": "LOGIN_DETECTED",
                "if": [],
                "then": [
                    {
                        "action": "switch_profile",
                        "args": {"name_from": "name"},
                    }
                ],
                "cooldown_ms": 1000,
            }
        ),
        # Visibility rule for the general-purpose pixel watchdog. On its
        # own this does nothing you can't do by tailing the log -- but it
        # gives users a concrete "rule fires when my watched region
        # changes" breadcrumb to copy + customise in ``triggers.json``.
        Rule.from_dict(
            {
                "id": "log-pixel-watch-changed",
                "when": "PIXEL_WATCH_CHANGED",
                "if": [],
                "then": [
                    {
                        "action": "log",
                        "args": {
                            "level": "info",
                            "message": "Pixel-watch region changed",
                        },
                    }
                ],
                # Tight but non-zero: a single real change can take a few
                # frames to settle, and we don't want one alert per frame.
                "cooldown_ms": 250,
            }
        ),
    ]


class TriggerEngine(QObject):
    """Evaluates rules against events emitted on an :class:`EventBus`.

    Owns one subscription per distinct ``when`` kind. Adding / removing rules
    at runtime is supported -- the engine tears down and re-subscribes as
    needed.

    :param bus: the application's :class:`EventBus` (alias of
        ``AnalyzerHub``).
    :param profiles: used by the ``switch_profile`` action.
    """

    # Emitted after a rule's actions finish running. UI can connect to this
    # to show a subtle "profile switched" toast or to log in a debug panel.
    rule_fired = Signal(str)

    def __init__(
        self,
        *,
        bus: EventBus,
        profiles: ProfileManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus
        self._profiles = profiles
        self._rules: list[Rule] = []
        # Map event-kind -> unsubscribe closure so replacing rules is cheap.
        self._subs: dict[str, Callable[[], None]] = {}

    # -- Rule management -------------------------------------------------------

    def set_rules(self, rules: list[Rule]) -> None:
        """Install ``rules``, replacing any previously-installed ones."""
        # Disconnect old subscriptions first.
        for unsubscribe in self._subs.values():
            unsubscribe()
        self._subs.clear()

        self._rules = list(rules)

        # Subscribe once per distinct event kind; the handler iterates any
        # rules gated on that kind. One subscription per kind keeps the bus
        # iteration cheap even if the user authors a hundred rules.
        for kind in {r.when for r in self._rules}:
            self._subs[kind] = self._bus.subscribe(kind, self._on_event)

    def rules(self) -> list[Rule]:
        return list(self._rules)

    # -- Persistence -----------------------------------------------------------

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Parse a rules blob and install it."""
        rules = [Rule.from_dict(r) for r in data.get("rules", [])]
        self.set_rules(rules)

    def load_from_file(self, path: Path) -> bool:
        """Load rules from a JSON file. Returns True on success.

        Missing file -> returns False and leaves the engine untouched (the
        caller typically follows up with :func:`default_rules`).
        """
        if not path.exists():
            log.info("triggers.no_file", path=str(path))
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("triggers.load_failed", error=str(e), path=str(path))
            return False
        self.load_from_dict(data)
        return True

    # -- Event handling --------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        now = time.monotonic()
        for rule in self._rules:
            if rule.when != event.kind:
                continue
            if not rule.matches(event):
                continue
            if rule.cooldown_ms > 0:
                elapsed_ms = (now - rule._last_fired_ts) * 1000.0
                if elapsed_ms < rule.cooldown_ms:
                    continue
            rule._last_fired_ts = now
            self._run_actions(rule, event)
            self.rule_fired.emit(rule.id)

    def _run_actions(self, rule: Rule, event: Event) -> None:
        for action in rule.actions:
            try:
                self._dispatch(action, event)
            except Exception:  # pragma: no cover - never let one bad action
                log.exception(
                    "trigger.action_failed",
                    rule=rule.id,
                    action=action.action,
                )

    def _dispatch(self, action: Action, event: Event) -> None:
        if action.action == "switch_profile":
            self._action_switch_profile(action.args, event)
        elif action.action == "publish_event":
            self._action_publish_event(action.args, event)
        elif action.action == "log":
            self._action_log(action.args, event)
        else:
            log.warning("trigger.unknown_action", action=action.action)

    # -- Action implementations -----------------------------------------------

    def _action_switch_profile(self, args: dict[str, Any], event: Event) -> None:
        name_from = args.get("name_from")
        name = event.data.get(name_from) if name_from else args.get("name")
        if not isinstance(name, str) or not name:
            log.warning("trigger.switch_profile.missing_name", args=args)
            return
        if name == self._profiles.active:
            return  # already on this profile; no-op
        if name not in self._profiles.names():
            log.warning("trigger.switch_profile.unknown", name=name)
            return
        try:
            self._profiles.load_profile(name)
            log.info("trigger.switch_profile.ok", name=name)
        except KeyError:
            # Race condition: profile vanished between names() and load(). Just log.
            log.warning("trigger.switch_profile.race", name=name)

    def _action_publish_event(self, args: dict[str, Any], event: Event) -> None:
        kind = args.get("kind")
        if not isinstance(kind, str) or not kind:
            log.warning("trigger.publish_event.missing_kind", args=args)
            return
        data = dict(args.get("data") or {})
        self._bus.publish(
            Event(analyzer_id=f"trigger:{kind}", kind=kind, data=data),
        )

    def _action_log(self, args: dict[str, Any], event: Event) -> None:
        level = str(args.get("level", "info")).lower()
        message = str(args.get("message", ""))
        fn = {
            "debug": log.debug,
            "info": log.info,
            "warning": log.warning,
            "error": log.error,
        }.get(level, log.info)
        fn("trigger.log", message=message, event_kind=event.kind)
