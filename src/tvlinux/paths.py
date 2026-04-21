"""Centralized XDG-compliant paths for the app (respects the Flatpak sandbox)."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

APP_NAME = "tibiavision-linux"
APP_AUTHOR = "tibiavision-linux"


def config_dir() -> Path:
    p = Path(user_config_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    p = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = Path(user_log_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def profiles_path() -> Path:
    return config_dir() / "profiles.json"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def audio_timers_path() -> Path:
    return config_dir() / "audio_timers.json"
