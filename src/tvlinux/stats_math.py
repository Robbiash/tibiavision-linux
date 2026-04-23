"""Pure math helpers for the hunt-stats HUD panels.

Kept separate from :mod:`tvlinux.hud_panels.hunt_stats_panel` and
:mod:`tvlinux.hud_panels.party_panel` so the maths can be unit-tested
without instantiating any Qt widgets. None of the helpers allocate; all
run in microseconds.
"""

from __future__ import annotations

import time
from datetime import timedelta

from .hunt_parser import HuntSession, PartyHuntSession


def per_hour(value: int, duration: timedelta) -> int:
    """Return ``value`` scaled to an hourly rate, safe on zero duration.

    If ``duration`` is zero or negative we return zero rather than
    raising -- a fresh session that hasn't ticked yet should render as
    ``0/h`` instead of crashing the panel.
    """
    seconds = duration.total_seconds()
    if seconds <= 0:
        return 0
    return int(value / (seconds / 3600.0))


def humanize_duration(seconds: int) -> str:
    """Render a duration in seconds as a compact ``1h23m`` style string.

    Used by history rows where the column is narrow and the "01:23:45"
    format wastes space. Rounds to whole minutes above one hour.
    """
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    h, rem = divmod(s, 3600)
    m = rem // 60
    if m == 0:
        return f"{h}h"
    return f"{h}h{m:02d}m"


def humanize_gp(value: int) -> str:
    """Render a (possibly negative) gold amount as a short display string.

    Examples:

    * ``1_200_000 -> "1.2M"``
    * ``-876_421 -> "-876K"``
    * ``950 -> "950"``
    * ``0 -> "0"``

    The compacted form keeps HUD rows single-line on narrow party panels.
    We deliberately favour readable "1.2M" over numerically tight
    "1.23M" here -- glanceability beats precision.
    """
    sign = "-" if value < 0 else ""
    n = abs(int(value))
    if n < 1_000:
        return f"{sign}{n}"
    if n < 1_000_000:
        # Round towards zero so "999" shows as "999" and "1,000" as "1K".
        return f"{sign}{n // 1_000}K"
    # Millions: one decimal unless the fraction is exactly zero.
    millions = n / 1_000_000.0
    if millions >= 100:  # 100M+, drop decimal for width
        return f"{sign}{int(millions)}M"
    # 1.0 -> "1M", 12.3 -> "12.3M"
    rounded = round(millions, 1)
    if rounded == int(rounded):
        return f"{sign}{int(rounded)}M"
    return f"{sign}{rounded}M"


def party_count(session: PartyHuntSession) -> int:
    """Number of members in a party hunt snapshot."""
    return len(session.members)


def live_extrapolate(
    session: HuntSession,
    now_monotonic: float | None = None,
) -> tuple[float, int, int]:
    """Return ``(session_ms_live, xp_per_h_live, profit_per_h_live)``.

    ``session_ms_live`` ticks up from the captured session duration by
    the wall-clock delta since the clipboard payload was captured. This
    is what drives the "live" session timer on the HUD between manual
    refreshes.

    ``xp_per_h_live`` and ``profit_per_h_live`` recompute rates against
    the extrapolated session length, so the HUD's per-hour numbers ease
    gently downward while the player isn't copying new data. That is
    slightly pessimistic (they're actively hunting!) but it's a useful
    cue that the numbers are "live, not final".
    """
    now = time.monotonic() if now_monotonic is None else now_monotonic
    dt_s = max(0.0, now - session.captured_at)
    session_ms_live = session.session.total_seconds() * 1000.0 + dt_s * 1000.0

    # Shared divisor. ``max`` prevents a zero-division on a brand-new
    # session captured at duration zero.
    total_hours = max(session_ms_live / 1000.0 / 3600.0, 1e-6)
    xp_per_h_live = int(session.xp_gain / total_hours)
    profit_per_h_live = int(session.balance / total_hours)
    return session_ms_live, xp_per_h_live, profit_per_h_live


__all__ = [
    "humanize_duration",
    "humanize_gp",
    "live_extrapolate",
    "party_count",
    "per_hour",
]
