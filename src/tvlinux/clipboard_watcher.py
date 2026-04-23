"""Bridge between the OS clipboard and the EventBus.

When the player right-clicks Tibia's Hunt Analyser or Party Hunt widget
and picks "Copy to clipboard", the result lands in the OS clipboard as
a chunk of plain text. This module listens for clipboard changes, tries
each of the :mod:`tvlinux.hunt_parser` parsers in turn, and publishes
typed events on the bus so downstream consumers (HUD panels, trigger
rules) can react without knowing anything about the clipboard itself.

The clipboard is a shared resource across the whole desktop, so we are
careful to:

* Ignore empty strings / obvious non-Tibia text (the parsers return
  ``None`` cheaply on non-matches).
* Deduplicate repeats of the same payload so mashing "Copy to clipboard"
  doesn't storm the bus.
* Fail silently if we run under an environment that has no clipboard
  (CI, headless tests without ``qapp`` fixture) -- the watcher becomes
  a no-op rather than raising at import.

**Hunt Mode gating**: when a :class:`HuntModeManager` is passed, the
watcher is completely silent while Hunt Mode is off -- no parsing, no
events, no history logging. This is what lets the app stay quiet outside
of active play.
"""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QClipboard, QGuiApplication

from .analyzers import AnalyzerHub, Event, EventKind
from .hunt_mode import HuntModeManager
from .hunt_parser import parse_hunt_analyser, parse_party_hunt
from .logging_config import get_logger

log = get_logger(__name__)


class ClipboardWatcher(QObject):
    """Watch the OS clipboard and publish Tibia hunt-stats events.

    :param bus: the application event bus.
    :param hunt_mode: optional gate. When provided, clipboard events are
        ignored while Hunt Mode is off.
    :param clipboard: optional clipboard override (tests). If ``None`` we
        use :meth:`QGuiApplication.clipboard`.
    """

    id = "clipboard_watcher"

    hunt_captured = Signal(object, str)  # (HuntSession, raw_text)
    party_captured = Signal(object, str)  # (PartyHuntSession, raw_text)

    def __init__(
        self,
        bus: AnalyzerHub,
        *,
        hunt_mode: HuntModeManager | None = None,
        clipboard: QClipboard | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus
        self._mode = hunt_mode
        self._last_payload: str | None = None

        if clipboard is None:
            app = QGuiApplication.instance()
            # ``QGuiApplication.instance()`` returns the narrower
            # ``QCoreApplication`` in stubs; at runtime it's always the
            # ``QGuiApplication`` we need for ``clipboard()``.
            if isinstance(app, QGuiApplication):
                clipboard = app.clipboard()

        self._clipboard = clipboard
        if self._clipboard is not None:
            self._clipboard.dataChanged.connect(self._on_clipboard_changed)
        else:
            log.info("clipboard_watcher.no_clipboard")

    # -- Public surface --------------------------------------------------------

    def process_text(self, text: str) -> None:
        """Process a clipboard string. Exposed for tests.

        Publishes at most one event per distinct payload: repeated copies
        of the same string are swallowed by the dedupe check. When a
        :class:`HuntModeManager` is configured and currently inactive,
        the method is a silent no-op.
        """
        if self._mode is not None and not self._mode.active:
            return
        if not isinstance(text, str) or not text.strip():
            return
        if text == self._last_payload:
            return

        hunt = parse_hunt_analyser(text)
        party = parse_party_hunt(text)

        if hunt is None and party is None:
            # Not a Tibia payload. Don't clobber ``_last_payload`` here --
            # we still want to react if the user re-copies the same
            # non-matching text and then copies a Tibia payload.
            return

        self._last_payload = text

        if party is not None:
            self._bus.publish(
                Event(
                    analyzer_id=self.id,
                    kind=EventKind.PARTY_HUNT_UPDATE,
                    data=asdict(party),
                )
            )
            self.party_captured.emit(party, text)

        if hunt is not None:
            self._bus.publish(
                Event(
                    analyzer_id=self.id,
                    kind=EventKind.HUNT_STATS_UPDATE,
                    data=asdict(hunt),
                )
            )
            self.hunt_captured.emit(hunt, text)

    # -- Slots -----------------------------------------------------------------

    def _on_clipboard_changed(self) -> None:
        if self._clipboard is None:
            return
        try:
            text = self._clipboard.text()
        except Exception:  # pragma: no cover - defensive against platform weirdness
            log.exception("clipboard_watcher.read_failed")
            return
        self.process_text(text)


__all__ = ["ClipboardWatcher"]
