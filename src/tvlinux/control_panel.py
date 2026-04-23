"""Deprecated back-compat shim.

The old :class:`ControlPanel` main window was split into:

* :class:`tvlinux.shell.ShellWindow` -- the new top-level shell (nav rail,
  pages, status footer).
* :class:`tvlinux.pages.regions_page.RegionsPage` -- the old body (region
  list, detail editors).

This module re-exports the region list UI under the ``ControlPanel`` name so
existing tests that only exercised the regions / detail / profile surface
keep working without modification. New code should import from
:mod:`tvlinux.shell` and :mod:`tvlinux.pages` directly.
"""

from __future__ import annotations

from .pages.regions_page import PRESET_BORDER_COLORS, ROLE_REGION_ID, RegionsPage

ControlPanel = RegionsPage

__all__ = ["PRESET_BORDER_COLORS", "ROLE_REGION_ID", "ControlPanel", "RegionsPage"]
