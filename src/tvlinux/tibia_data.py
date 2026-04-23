"""Read-only accessors for Tibia's own on-disk state.

The Tibia client persists its per-character preferences in a well-known
location::

    ~/.local/share/CipSoft GmbH/Tibia/packages/Tibia/

Of particular interest for TibiaCompanion is ``conf/clientoptions.json`` --
Tibia rewrites the file whenever a user logs in / out or changes a hotkey,
so the *active hotkey preset* and the *current hotkey bindings* are both
available without any OCR or pixel inspection.

This module is the single read-side for that file. It stays deliberately
free of Qt so it can be unit-tested without a ``QApplication``; the
filesystem watcher lives one directory over, in
:mod:`tvlinux.analyzers.preset_watcher`.

Override via environment
------------------------

``TIBIAVISION_TIBIA_DATA`` overrides the discovered data directory. Tests
use this to point at a temporary directory; power users can use it to
read from a Tibia install mounted somewhere non-standard (e.g. a Steam
Flatpak or a dev build).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .logging_config import get_logger

log = get_logger(__name__)

# Bundled JSON of known Tibia item / spell ids -> human names. Lives under
# the package so it travels with installs. Users can author a richer copy
# at ``<config_dir>/object_names.json`` which wins over this default.
_BUNDLED_OBJECT_NAMES = Path(__file__).with_name("assets") / "object_names.json"

# Friendly labels for the built-in actions Tibia wires into the hotkey
# system (everything not matching ``TriggerActionButton_X.Y`` or a direct
# ``useObject`` goes through here). Extend as we discover more.
_BUILTIN_ACTION_LABELS: dict[str, str] = {
    "AttackFirstTarget": "Attack",
    "AttackNextTarget": "Next target",
    "AttackPreviousTarget": "Prev target",
    "QuickLootAreaAtPlayer": "Quick loot",
    "MinimapFloorUp": "Minimap up",
    "MinimapFloorDown": "Minimap down",
    "MinimapScrollEast": "Minimap east",
    "MinimapScrollWest": "Minimap west",
    "MinimapScrollNorth": "Minimap north",
    "MinimapScrollSouth": "Minimap south",
    "OpenHelpChannel": "Help channel",
    "OpenLootChannel": "Loot channel",
    "OpenChannelList": "Channels",
    "CloseCurrentChannel": "Close channel",
    "NextChannel": "Next channel",
    "PreviousChannel": "Prev channel",
    "ShowDefaultChannel": "Default channel",
    "ShowCyclopediaMap": "Map",
    "ShowIgnorelist": "Ignore list",
    "ShowOptionsHotkeys": "Hotkey options",
    "ShowPrey": "Prey",
    "ShowQuestlog": "Questlog",
    "Bugreport": "Bug report",
    "Copy": "Copy",
    "ToggleShowServermessagesInCurrentChannel": "Toggle server msgs",
    "ChatModeTemporaryOn": "Chat (temp)",
}


@dataclass(frozen=True)
class HotkeyBinding:
    """One row in the hotbar cheat-sheet.

    ``label`` is the best human-readable name we could resolve:

    * a spell's words (e.g. ``"exura vita"``),
    * an item's name (``"mana potion"``) via ``object_names.json``,
    * a built-in action (``"Attack"``),
    * or a fallback like ``"#3725"`` when the object is unknown.
    """

    keysequence: str
    label: str
    kind: str  # "item" | "spell" | "action" | "unknown"
    object_id: int | None = None
    use_type: str | None = None


def find_data_dir() -> Path | None:
    """Locate the Tibia data directory.

    Lookup order:

    1. ``$TIBIAVISION_TIBIA_DATA`` env var (explicit opt-in).
    2. ``$XDG_DATA_HOME/CipSoft GmbH/Tibia/packages/Tibia/``.
    3. ``~/.local/share/CipSoft GmbH/Tibia/packages/Tibia/``.

    Returns ``None`` if none of those exist -- callers must be prepared
    for "Tibia not installed".
    """
    override = os.environ.get("TIBIAVISION_TIBIA_DATA")
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None

    xdg = os.environ.get("XDG_DATA_HOME")
    candidates = []
    if xdg:
        candidates.append(Path(xdg) / "CipSoft GmbH" / "Tibia" / "packages" / "Tibia")
    candidates.append(
        Path.home() / ".local" / "share" / "CipSoft GmbH" / "Tibia" / "packages" / "Tibia",
    )
    for c in candidates:
        if c.exists():
            return c
    return None


def client_options_path() -> Path | None:
    """Absolute path to ``conf/clientoptions.json`` (or ``None``)."""
    root = find_data_dir()
    if root is None:
        return None
    p = root / "conf" / "clientoptions.json"
    return p if p.exists() else None


def read_client_options(path: Path | None = None) -> dict[str, Any] | None:
    """Parse ``clientoptions.json``. Returns ``None`` on failure.

    The file is rewritten atomically by Tibia on change, so a partial read
    is rare; still we swallow ``JSONDecodeError`` to keep the caller loop
    robust when we happen to read mid-write.
    """
    target = path or client_options_path()
    if target is None or not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("tibia_data.read_failed", path=str(target), error=str(e))
        return None


def current_preset_name(options: dict[str, Any]) -> str | None:
    """Return the active hotkey preset name, if set.

    Tibia populates ``hotkeyOptions.currentHotkeySetName`` with the name
    of the preset bound to the last logged-in character, so changes to
    this string are a good proxy for "character switched".
    """
    hk = options.get("hotkeyOptions") or {}
    name = hk.get("currentHotkeySetName")
    return str(name) if isinstance(name, str) and name else None


def preset_names(options: dict[str, Any]) -> list[str]:
    """All preset names in ``hotkeySets``."""
    hk = options.get("hotkeyOptions") or {}
    sets = hk.get("hotkeySets") or {}
    return list(sets.keys()) if isinstance(sets, dict) else []


def _load_object_names() -> dict[str, str]:
    """Return the merged ``object_names`` dict.

    Priority (later keys win):

    1. Bundled ``src/tvlinux/assets/object_names.json``.
    2. User override at ``<config>/object_names.json`` (optional).
    """
    merged: dict[str, str] = {}
    for candidate in (_BUNDLED_OBJECT_NAMES, _user_object_names_path()):
        if candidate is None or not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("tibia_data.object_names.load_failed", path=str(candidate), error=str(e))
            continue
        if isinstance(data, dict):
            # Keys are stringified ints; preserve that shape so callers
            # can do ``names[str(object_id)]`` directly.
            merged.update({str(k): str(v) for k, v in data.items()})
    return merged


def _user_object_names_path() -> Path | None:
    """Optional user override. Imported lazily to avoid a hard cycle with paths."""
    try:
        from .paths import config_dir

        return config_dir() / "object_names.json"
    except Exception:  # pragma: no cover - defensive: paths module always imports
        return None


def iter_hotkey_bindings(
    options: dict[str, Any],
    preset_name: str | None = None,
    *,
    mode: str = "chatOff",
) -> Iterator[HotkeyBinding]:
    """Yield one :class:`HotkeyBinding` per key in ``preset_name``.

    ``mode`` selects between ``"chatOff"`` (typical gameplay) and
    ``"chatOn"`` (which Tibia stores as a sibling list). Defaults to
    ``chatOff`` because that's what the hotbar panel wants to display.
    If ``preset_name`` is ``None`` we use the current one from
    ``currentHotkeySetName``.
    """
    hk = options.get("hotkeyOptions") or {}
    sets = hk.get("hotkeySets") or {}
    if not isinstance(sets, dict):
        return
    name = preset_name or current_preset_name(options)
    if name is None or name not in sets:
        return
    preset = sets[name]
    if not isinstance(preset, dict):
        return

    action_bars = _index_action_bars(preset.get("actionBarOptions") or {})
    object_names = _load_object_names()

    entries = preset.get(mode) or []
    if not isinstance(entries, list):
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        keysequence = entry.get("keysequence")
        actionsetting = entry.get("actionsetting") or {}
        if not isinstance(keysequence, str) or not isinstance(actionsetting, dict):
            continue
        yield _resolve_binding(keysequence, actionsetting, action_bars, object_names)


def _index_action_bars(action_bar_options: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
    """Flatten ``actionBarOptions.mappings`` into a ``(bar, button) -> actionsetting`` map."""
    out: dict[tuple[int, int], dict[str, Any]] = {}
    mappings = action_bar_options.get("mappings") or []
    if not isinstance(mappings, list):
        return out
    for m in mappings:
        if not isinstance(m, dict):
            continue
        try:
            bar = int(m["actionBar"])
            button = int(m["actionButton"])
        except (KeyError, TypeError, ValueError):
            continue
        setting = m.get("actionsetting")
        if isinstance(setting, dict):
            out[(bar, button)] = setting
    return out


def _resolve_binding(
    keysequence: str,
    actionsetting: dict[str, Any],
    action_bars: dict[tuple[int, int], dict[str, Any]],
    object_names: dict[str, str],
) -> HotkeyBinding:
    """Translate one ``actionsetting`` into a :class:`HotkeyBinding`."""
    # Direct item use / equip.
    if "useObject" in actionsetting:
        return _binding_for_object(keysequence, actionsetting, object_names)

    # Spell / chat-text trigger.
    if "words" in actionsetting:
        words = str(actionsetting.get("words") or "").strip()
        if words:
            return HotkeyBinding(
                keysequence=keysequence,
                label=words,
                kind="spell",
            )

    action = actionsetting.get("action")
    if not isinstance(action, str):
        return HotkeyBinding(keysequence=keysequence, label="(unknown)", kind="unknown")

    # Indirection: TriggerActionButton_X.Y -> look up the real action on
    # action bar X, button Y.
    if action.startswith("TriggerActionButton_"):
        coords = _parse_trigger_action(action)
        if coords is not None and coords in action_bars:
            return _resolve_binding(
                keysequence,
                action_bars[coords],
                action_bars,
                object_names,
            )
        # Missing mapping: still useful to say "action bar 1 button 3"
        # rather than an opaque token.
        if coords is not None:
            bar, button = coords
            return HotkeyBinding(
                keysequence=keysequence,
                label=f"Bar {bar} button {button}",
                kind="unknown",
            )

    # Built-in action with a known friendly label.
    if action in _BUILTIN_ACTION_LABELS:
        return HotkeyBinding(
            keysequence=keysequence,
            label=_BUILTIN_ACTION_LABELS[action],
            kind="action",
        )

    return HotkeyBinding(keysequence=keysequence, label=action, kind="action")


def _binding_for_object(
    keysequence: str,
    actionsetting: dict[str, Any],
    object_names: dict[str, str],
) -> HotkeyBinding:
    raw_object_id = actionsetting.get("useObject")
    if raw_object_id is None:
        return HotkeyBinding(keysequence=keysequence, label="(invalid object)", kind="unknown")
    try:
        object_id = int(raw_object_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return HotkeyBinding(keysequence=keysequence, label="(invalid object)", kind="unknown")
    use_type = actionsetting.get("useType")
    name = object_names.get(str(object_id))
    label = name if name else f"#{object_id}"
    return HotkeyBinding(
        keysequence=keysequence,
        label=label,
        kind="item",
        object_id=object_id,
        use_type=str(use_type) if isinstance(use_type, str) else None,
    )


def _parse_trigger_action(action: str) -> tuple[int, int] | None:
    """Turn ``"TriggerActionButton_1.13"`` into ``(1, 13)``."""
    payload = action.removeprefix("TriggerActionButton_")
    if "." not in payload:
        return None
    bar_s, button_s = payload.split(".", 1)
    try:
        return int(bar_s), int(button_s)
    except ValueError:
        return None


__all__ = [
    "HotkeyBinding",
    "client_options_path",
    "current_preset_name",
    "find_data_dir",
    "iter_hotkey_bindings",
    "preset_names",
    "read_client_options",
]
