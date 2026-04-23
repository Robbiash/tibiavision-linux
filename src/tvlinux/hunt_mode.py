"""Hunt Mode -- global on/off gate for the hunt clipboard pipeline.

Hunt Mode is the single switch that decides whether the clipboard watcher,
passive key listener, auto-refresh timer, and history auto-logging actually do
anything. Flipping it off makes the app completely quiet on those channels so
the user pays no ambient cost when they are not actively playing.

Config shape (persisted to ``config_dir()/hunt_mode.json``)::

    {
        "active": false,                    # master toggle
        "trigger_key": "space",             # passive key that triggers a refresh
        "trigger_key_enabled": true,        # listen to the key at all
        "min_refresh_interval_sec": 60,     # rate-limit between refreshes
        "auto_fire_fallback_sec": 0,        # 0 = off; otherwise fire every N s
        "tibia_window_substring": "Tibia",  # focus-filter match
        "copy_anchor_hunt": {"right_click_x": 0, "right_click_y": 0,
                             "menu_x": 0, "menu_y": 0} | None,
        "copy_anchor_party": {...} | None,
        "auto_log_to_history": true         # Hunt Analyser pastes feed history
    }

This module is intentionally tiny -- all the actual work (listening, clicking,
logging) lives in dedicated modules that consume a ``HuntModeManager``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .logging_config import get_logger
from .paths import config_dir

log = get_logger(__name__)


@dataclass(frozen=True)
class CopyAnchor:
    """Screen-coordinate pair for replaying a 'right-click + Copy' dance."""

    right_click_x: int
    right_click_y: int
    menu_x: int
    menu_y: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, int] | None) -> CopyAnchor | None:
        if not data:
            return None
        try:
            return cls(
                right_click_x=int(data["right_click_x"]),
                right_click_y=int(data["right_click_y"]),
                menu_x=int(data["menu_x"]),
                menu_y=int(data["menu_y"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


@dataclass
class HuntModeConfig:
    active: bool = False
    trigger_key: str = "space"
    trigger_key_enabled: bool = True
    min_refresh_interval_sec: int = 60
    auto_fire_fallback_sec: int = 0
    tibia_window_substring: str = "Tibia"
    copy_anchor_hunt: CopyAnchor | None = None
    copy_anchor_party: CopyAnchor | None = None
    auto_log_to_history: bool = True

    def to_dict(self) -> dict:
        return {
            "active": self.active,
            "trigger_key": self.trigger_key,
            "trigger_key_enabled": self.trigger_key_enabled,
            "min_refresh_interval_sec": int(self.min_refresh_interval_sec),
            "auto_fire_fallback_sec": int(self.auto_fire_fallback_sec),
            "tibia_window_substring": self.tibia_window_substring,
            "copy_anchor_hunt": (
                self.copy_anchor_hunt.to_dict() if self.copy_anchor_hunt else None
            ),
            "copy_anchor_party": (
                self.copy_anchor_party.to_dict() if self.copy_anchor_party else None
            ),
            "auto_log_to_history": self.auto_log_to_history,
        }

    @classmethod
    def from_dict(cls, data: dict) -> HuntModeConfig:
        return cls(
            active=bool(data.get("active", False)),
            trigger_key=str(data.get("trigger_key", "space")),
            trigger_key_enabled=bool(data.get("trigger_key_enabled", True)),
            min_refresh_interval_sec=int(data.get("min_refresh_interval_sec", 60)),
            auto_fire_fallback_sec=int(data.get("auto_fire_fallback_sec", 0)),
            tibia_window_substring=str(data.get("tibia_window_substring", "Tibia")),
            copy_anchor_hunt=CopyAnchor.from_dict(data.get("copy_anchor_hunt")),
            copy_anchor_party=CopyAnchor.from_dict(data.get("copy_anchor_party")),
            auto_log_to_history=bool(data.get("auto_log_to_history", True)),
        )


def hunt_mode_path() -> Path:
    return config_dir() / "hunt_mode.json"


class HuntModeManager(QObject):
    """Holds the `HuntModeConfig`, persists it, and broadcasts toggles.

    Design goals:

    - Single source of truth. Everywhere else reads from ``self.config``.
    - Cheap to import and construct; no I/O unless callers ask for it.
    - Signal-driven: consumers (ClipboardWatcher, PassiveKeyListener,
      HuntRefresher, StatusFooter) just wire to ``toggled`` / ``config_changed``
      and never have to poll.
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
        through form state without filtering.
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
    "CopyAnchor",
    "HuntModeConfig",
    "HuntModeManager",
    "hunt_mode_path",
]
