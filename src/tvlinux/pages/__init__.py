"""Sidebar-navigated pages for the ShellWindow.

Each page is a standalone ``QWidget`` that the :class:`~tvlinux.shell.ShellWindow`
swaps into its central stack. Pages own their own widgets and signals; the
shell owns the navigation, page header, and status footer.
"""

from .about_page import AboutPage
from .audio_timers_page import AudioTimersPage
from .hunt_history_page import HuntHistoryPage
from .quest_browser_page import QuestBrowserPage
from .regions_page import RegionsPage
from .settings_page import SettingsPage

__all__ = [
    "AboutPage",
    "AudioTimersPage",
    "HuntHistoryPage",
    "QuestBrowserPage",
    "RegionsPage",
    "SettingsPage",
]
