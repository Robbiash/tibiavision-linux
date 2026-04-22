"""Stub: character-name detector for dynamic vocation auto-switching.

Planned v2 implementation:

1. User designates a ROI over the top-left character name (the same place
   Tibia prints it above the HP bar).
2. Every ~2 s, OCR the region. The text is short and stable so a single
   high-confidence read is usually enough.
3. Maintain a ``_last_name`` field; only emit
   :data:`EventKind.LOGIN_DETECTED` when the detected name *changes* from
   the previous value. Payload: ``{"name": str, "previous": str | None}``.
4. Phase 2's trigger engine subscribes to this event and asks
   :class:`~tvlinux.profiles.ProfileManager` to hot-swap the active profile
   to one named after the detected character.

The 2 s cadence is plenty -- character switches happen at human speed -- and
cheap enough that we could ratchet it down later if auto-switch latency
becomes user-visible.

Kept as a pure stub in v1.
"""

from __future__ import annotations

from .base import Analyzer, AnalyzerFrame, Event


class LoginNameAnalyzer(Analyzer):
    id = "login_name"
    tick_ms = 2000

    def __init__(self) -> None:
        super().__init__()
        self.enabled = False
        # v2 will populate this from the first successful OCR and compare
        # against subsequent reads to decide when to emit LOGIN_DETECTED.
        self._last_name: str | None = None

    def analyze(self, frame: AnalyzerFrame) -> list[Event]:
        return []
