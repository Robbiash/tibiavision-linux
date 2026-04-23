"""Unit tests for :mod:`tvlinux.stats_math`."""

from __future__ import annotations

from datetime import timedelta

from tvlinux.hunt_parser import HuntSession, PartyHuntSession, PartyMember
from tvlinux.stats_math import humanize_gp, live_extrapolate, party_count, per_hour


def _session(
    *,
    duration: timedelta = timedelta(hours=1),
    xp_gain: int = 1_000_000,
    balance: int = 600_000,
    captured_at: float = 1_000.0,
) -> HuntSession:
    return HuntSession(
        session=duration,
        raw_xp_gain=xp_gain,
        xp_gain=xp_gain,
        raw_xp_per_h=xp_gain,
        xp_per_h=xp_gain,
        loot=balance + 100_000,
        supplies=100_000,
        balance=balance,
        damage=500_000,
        damage_per_h=500_000,
        healing=200_000,
        healing_per_h=200_000,
        captured_at=captured_at,
    )


# -- per_hour -------------------------------------------------------------


def test_per_hour_scales_to_hourly_rate():
    assert per_hour(1000, timedelta(hours=1)) == 1000
    assert per_hour(500, timedelta(minutes=30)) == 1000
    assert per_hour(250, timedelta(minutes=15)) == 1000


def test_per_hour_returns_zero_on_zero_duration():
    assert per_hour(1_234_567, timedelta(0)) == 0
    assert per_hour(1_234_567, timedelta(seconds=-5)) == 0


# -- humanize_gp ----------------------------------------------------------


def test_humanize_gp_under_1000():
    assert humanize_gp(0) == "0"
    assert humanize_gp(1) == "1"
    assert humanize_gp(999) == "999"


def test_humanize_gp_thousands():
    assert humanize_gp(1_000) == "1K"
    assert humanize_gp(12_500) == "12K"
    assert humanize_gp(876_421) == "876K"


def test_humanize_gp_millions_have_one_decimal_unless_integer():
    assert humanize_gp(1_000_000) == "1M"
    assert humanize_gp(1_200_000) == "1.2M"
    assert humanize_gp(12_300_000) == "12.3M"


def test_humanize_gp_large_millions_drop_decimal():
    assert humanize_gp(123_456_789) == "123M"


def test_humanize_gp_preserves_sign():
    assert humanize_gp(-876_421) == "-876K"
    assert humanize_gp(-1_200_000) == "-1.2M"
    assert humanize_gp(-999) == "-999"


# -- party_count ----------------------------------------------------------


def test_party_count_counts_members():
    session = PartyHuntSession(
        session=timedelta(hours=1),
        loot_type="Market",
        loot=0,
        supplies=0,
        balance=0,
        members=[
            PartyMember(name="A", loot=0, supplies=0, balance=0),
            PartyMember(name="B", loot=0, supplies=0, balance=0),
        ],
    )
    assert party_count(session) == 2


def test_party_count_empty_session():
    session = PartyHuntSession(
        session=timedelta(), loot_type="", loot=0, supplies=0, balance=0, members=[]
    )
    assert party_count(session) == 0


# -- live_extrapolate -----------------------------------------------------


def test_live_extrapolate_advances_from_capture_time():
    s = _session(duration=timedelta(hours=1), xp_gain=1_000_000, balance=600_000, captured_at=1_000.0)
    # 30 minutes after capture: total session time should be 1h30m.
    ms_live, xp_live, profit_live = live_extrapolate(s, now_monotonic=1_000.0 + 1_800.0)
    assert ms_live == 5_400_000.0  # 90 minutes in ms
    # Hourly rates were 1M/h and 600K/h at capture; after 50% more time they
    # should deflate to ~667K xp/h and ~400K profit/h.
    assert 660_000 <= xp_live <= 670_000
    assert 395_000 <= profit_live <= 405_000


def test_live_extrapolate_zero_duration_does_not_divide_by_zero():
    s = _session(duration=timedelta(0), xp_gain=0, balance=0, captured_at=0.0)
    ms_live, xp_live, profit_live = live_extrapolate(s, now_monotonic=0.0)
    assert ms_live == 0.0
    assert xp_live == 0
    assert profit_live == 0


def test_live_extrapolate_never_returns_negative_timedelta():
    s = _session(captured_at=1_000.0)
    ms_live, _, _ = live_extrapolate(s, now_monotonic=900.0)  # clock went backwards
    # dt clamps at 0 so ms_live == original session length.
    assert ms_live == s.session.total_seconds() * 1000.0
