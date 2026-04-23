"""Loot-split math: transfers always sum to zero."""

from __future__ import annotations

from tvlinux.loot_split import format_transfers_block, split


def test_even_split_all_equal_returns_zero_transfers() -> None:
    ts = split([("A", 100), ("B", 100), ("C", 100)])
    assert all(t.transfer == 0 for t in ts)


def test_two_members_rich_pays_poor() -> None:
    ts = split([("Rich", 1000), ("Poor", 0)])
    # Fair share 500; Rich pays 500, Poor receives 500.
    by_name = {t.name: t for t in ts}
    assert by_name["Rich"].transfer == -500
    assert by_name["Poor"].transfer == 500
    assert sum(t.transfer for t in ts) == 0


def test_rounding_residue_absorbed_by_top_earner() -> None:
    # 3 members, totals don't divide evenly.
    ts = split([("A", 100), ("B", 100), ("C", 101)])
    # fair = 100 residue=1 top=C -> C transfer adjusted so sum is zero.
    assert sum(t.transfer for t in ts) == 0


def test_transfers_sum_to_zero_for_arbitrary_case() -> None:
    pairs = [("W", 12345), ("X", -4567), ("Y", 0), ("Z", 987654)]
    ts = split(pairs)
    assert sum(t.transfer for t in ts) == 0


def test_empty_input_returns_empty() -> None:
    assert split([]) == []


def test_format_block_has_one_line_per_member() -> None:
    ts = split([("A", 10), ("B", 0)])
    lines = format_transfers_block(ts).splitlines()
    assert len(lines) == len(ts)
