"""TibiaVision-Linux: a Wayland-native, BattlEye-safe screen-mirroring overlay."""

from __future__ import annotations

from importlib.resources import files as _pkg_files

__version__ = "0.1.0"
__app_id__ = "gg.tibiavision.Linux"
__app_name__ = "TibiaVision-Linux"


def app_icon_path() -> str:
    """Absolute filesystem path to the bundled SVG app icon."""
    return str(_pkg_files("tvlinux").joinpath("resources", "app_icon.svg"))
