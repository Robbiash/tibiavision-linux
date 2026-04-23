"""Hunt Mode -- global on/off gate for the clipboard -> HUD pipeline.

Hunt Mode is the single switch that decides whether the clipboard watcher
and history auto-logging actually do anything. Flipping it off makes the
app completely quiet on those channels so the user pays no ambient cost
when they are not actively playing.

The app never interacts with Tibia directly. Fresh hunt stats arrive only
when the user themselves presses Tibia's built-in "Copy to clipboard"
menu item; the OS clipboard change is what this module gates.

Config shape (persisted to ``config_dir()/hunt_mode.json``)::

    {
        "active": false,              # master toggle
        "auto_log_to_history": true   # Hunt Analyser pastes feed history
    }

This module is intentionally tiny -- all the actual work (clipboard
parsing, history persistence) lives in dedicated modules that consume a
``HuntModeManager``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .logging_config import get_logger
from .paths import config_dir

log = get_logger(__name__)


@dataclass
class HuntModeConfig:
    active: bool = False
    auto_log_to_history: bool = True

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "auto_log_to_history": self.auto_log_to_history,
        }

    @classmethod
    def from_dict(cls, data: dict) -> HuntModeConfig:
        return cls(
            active=bool(data.get("active", False)),
            auto_log_to_history=bool(data.get("auto_log_to_history", True)),
        )


def hunt_mode_path() -> Path:
    return config_dir() / "hunt_mode.json"


class HuntModeManager(QObject):
    """Holds the `HuntModeConfig`, persists it, and broadcasts toggles.

    Design goals:

    - Single source of truth. Everywhere else reads from ``self.config``.
    - Cheap to import and construct; no I/O unless callers ask for it.
    - Signal-driven: consumers (ClipboardWatcher, StatusFooter) just wire
      to ``toggled`` / ``config_changed`` and never have to poll.
    """

    toggled = Signal(bool)  # new active state
    config_changed = Signal(object)  # new HuntModeConfig

    def __init__(
        self,
        *,
        path: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path or hunt_mode_path()
        self._config = HuntModeConfig()
        self.load()

    @property
    def config(self) -> HuntModeConfig:
        return self._config

    @property
    def active(self) -> bool:
        return self._config.active

    def set_active(self, active: bool) -> None:
        if active == self._config.active:
            return
        self._config = replace(self._config, active=active)
        self.save()
        log.info("hunt_mode.toggled", active=active)
        self.toggled.emit(active)
        self.config_changed.emit(self._config)

    def toggle(self) -> bool:
        self.set_active(not self._config.active)
        return self._config.active

    def update_config(self, **kwargs: object) -> None:
        """Mutate one or more config fields and persist.

        ``active`` changes still fire ``toggled``; all changes fire
        ``config_changed``. Unknown kwargs are ignored so callers can pass
        through form state without filtering (and so legacy keys from
        older versions quietly drop on load).
        """
        valid = {f for f in HuntModeConfig.__dataclass_fields__}
        patch = {k: v for k, v in kwargs.items() if k in valid}
        if not patch:
            return
        was_active = self._config.active
        self._config = replace(self._config, **patch)  # type: ignore[arg-type]
        self.save()
        self.config_changed.emit(self._config)
        if "active" in patch and bool(patch["active"]) != was_active:
            self.toggled.emit(self._config.active)

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("hunt_mode.load_failed", error=str(e))
            return
        self._config = HuntModeConfig.from_dict(data)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._config.to_dict()
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


__all__ = [
    "HuntModeConfig",
    "HuntModeManager",
    "hunt_mode_path",
]
