"""Profile persistence.

A *profile* is a named snapshot of the region manager's state (plus a couple of global
preferences like which profile is active). Profiles are stored in a single JSON file at
``$XDG_CONFIG_HOME/tibiavision-linux/profiles.json`` (inside the Flatpak sandbox this
resolves to ``~/.var/app/gg.tibiavision.Linux/config/tibiavision-linux/profiles.json``).

Schema::

  {
    "version": 1,
    "active": "Default",
    "profiles": {
      "Default": {"regions": [...]},
      "PvP":     {"regions": [...]},
      ...
    }
  }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .logging_config import get_logger
from .paths import profiles_path
from .regions import Region, RegionManager

log = get_logger(__name__)

SCHEMA_VERSION = 1
DEFAULT_PROFILE = "Default"


@dataclass
class ProfileStore:
    """In-memory representation of the profiles file."""

    active: str = DEFAULT_PROFILE
    profiles: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "active": self.active,
            "profiles": self.profiles,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ProfileStore:
        return cls(
            active=str(data.get("active") or DEFAULT_PROFILE),
            profiles={
                str(k): list(v.get("regions", [])) if isinstance(v, dict) else list(v)
                for k, v in (data.get("profiles") or {}).items()
            },
        )


class ProfileManager:
    """Handles load/save/swap of profiles against a single ``RegionManager``."""

    def __init__(
        self,
        regions: RegionManager,
        path: Path | None = None,
    ) -> None:
        self._regions = regions
        self._path = path or profiles_path()
        self._store = ProfileStore()
        self.load_from_disk()

    # -- Disk I/O ---------------------------------------------------------------------

    def load_from_disk(self) -> None:
        if not self._path.exists():
            log.info("profiles.no_file", path=str(self._path))
            self._store = ProfileStore(active=DEFAULT_PROFILE, profiles={DEFAULT_PROFILE: []})
            self._apply_active_to_regions()
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._store = ProfileStore.from_json(data)
        except (OSError, json.JSONDecodeError) as e:
            log.error("profiles.load_failed", error=str(e), path=str(self._path))
            self._store = ProfileStore(active=DEFAULT_PROFILE, profiles={DEFAULT_PROFILE: []})
        if DEFAULT_PROFILE not in self._store.profiles:
            self._store.profiles[DEFAULT_PROFILE] = []
        if self._store.active not in self._store.profiles:
            self._store.active = DEFAULT_PROFILE
        self._apply_active_to_regions()

    def save_to_disk(self) -> None:
        # Snapshot current regions into the active profile before writing.
        self._store.profiles[self._store.active] = self._regions.to_list()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._store.to_json(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self._path)
        log.info("profiles.saved", path=str(self._path))

    # -- Profile operations -----------------------------------------------------------

    @property
    def active(self) -> str:
        return self._store.active

    def names(self) -> list[str]:
        return sorted(self._store.profiles.keys())

    def load_profile(self, name: str) -> None:
        if name not in self._store.profiles:
            raise KeyError(name)
        self._store.profiles[self._store.active] = self._regions.to_list()
        self._store.active = name
        self._apply_active_to_regions()
        self.save_to_disk()

    def save_profile_as(self, name: str) -> None:
        self._store.profiles[name] = self._regions.to_list()
        self._store.active = name
        self.save_to_disk()

    def delete_profile(self, name: str) -> None:
        if name == DEFAULT_PROFILE:
            raise ValueError("cannot delete Default profile")
        self._store.profiles.pop(name, None)
        if self._store.active == name:
            self._store.active = DEFAULT_PROFILE
            self._apply_active_to_regions()
        self.save_to_disk()

    def next_profile(self) -> str:
        """Cycle to the next profile in alphabetical order and load it."""
        names = self.names()
        if not names:
            return self._store.active
        try:
            idx = names.index(self._store.active)
        except ValueError:
            idx = -1
        target = names[(idx + 1) % len(names)]
        self.load_profile(target)
        return target

    # -- Import / Export --------------------------------------------------------------

    def export_current_to(self, path: Path) -> None:
        data = {
            "version": SCHEMA_VERSION,
            "profile": self._store.active,
            "regions": self._regions.to_list(),
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def import_from(self, path: Path, new_name: str | None = None) -> str:
        data = json.loads(path.read_text(encoding="utf-8"))
        regions = data.get("regions") or []
        name = new_name or data.get("profile") or path.stem
        # Avoid overwriting an existing profile without explicit user intent.
        base = name
        i = 2
        while name in self._store.profiles:
            name = f"{base} ({i})"
            i += 1
        self._store.profiles[name] = list(regions)
        self.save_to_disk()
        return name

    # -- Internals --------------------------------------------------------------------

    def _apply_active_to_regions(self) -> None:
        data = self._store.profiles.get(self._store.active, [])
        regions = [Region.from_dict(d) for d in data]
        self._regions.reset(regions)
