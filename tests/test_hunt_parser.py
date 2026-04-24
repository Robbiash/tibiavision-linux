"""Unit tests for :mod:`tvlinux.hunt_parser`."""

from __future__ import annotations

from datetime import timedelta

from tvlinux.hunt_parser import (
    HuntSession,
    PartyHuntSession,
    parse_hunt_analyser,
    parse_party_hunt,
)

SOLO_PAYLOAD = """\
Session data: From 2025-04-01, 20:30:00 to 2025-04-02, 00:18:00
Session: 03:48h
Raw XP Gain: 33,673,350
XP Gain: 71,462,604
Raw XP/h: 521,857
XP/h: 782,770
Loot: 1,385,541
Supplies: 509,120
Balance: 876,421
Damage: 19,844,957
Damage/h: 5,381,491
Healing: 6,415,088
Healing/h: 1,744,415
"""

PARTY_PAYLOAD = """\
Session data: From 2025-04-01, 20:30:00 to 2025-04-01, 23:40:00
Session: 03:10h
Loot Type: Market
Loot: 28,028,157
Supplies: 6,748,447
Balance: 21,279,710
Abbadinos
    Loot: 18,908,088
    Supplies: 6,876,058
    Balance: 12,032,030
    Damage: 9,000,000
    Healing: 500,000
Sydnee Sweeney
    Loot: 11,000,000
    Supplies: 1,100,000
    Balance: 9,900,000
La Kogzita
    Loot: 100,000
    Supplies: 526,000
    Balance: -426,000
Mooony Gaz
    Loot: 20,000
    Supplies: 155,000
    Balance: -135,000
The Kogzy
    Loot: 0
    Supplies: 88,000
    Balance: -88,000
"""


# -- Solo hunt -------------------------------------------------------------


def test_parse_hunt_analyser_roundtrips_canonical_payload():
    session = parse_hunt_analyser(SOLO_PAYLOAD)
    assert isinstance(session, HuntSession)
    assert session.session == timedelta(hours=3, minutes=48)
    assert session.raw_xp_gain == 33_673_350
    assert session.xp_gain == 71_462_604
    assert session.xp_per_h == 782_770
    assert session.loot == 1_385_541
    assert session.supplies == 509_120
    assert session.balance == 876_421
    assert session.damage == 19_844_957
    assert session.damage_per_h == 5_381_491
    assert session.healing == 6_415_088
    assert session.healing_per_h == 1_744_415


def test_parse_hunt_analyser_handles_negative_balance():
    # Replace balance with a negative value; verify sign is preserved.
    text = SOLO_PAYLOAD.replace("Balance: 876,421", "Balance: -112,400")
    session = parse_hunt_analyser(text)
    assert session is not None
    assert session.balance == -112_400


def test_parse_hunt_analyser_accepts_period_separators():
    text = SOLO_PAYLOAD.replace(",", ".")
    session = parse_hunt_analyser(text)
    assert session is not None
    # 782.770 with period as thousands separator is still 782770.
    assert session.xp_per_h == 782_770


def test_parse_hunt_analyser_returns_none_on_garbage():
    assert parse_hunt_analyser("") is None
    assert parse_hunt_analyser("hello world") is None
    # Close but missing required fields -> None, not partial.
    assert parse_hunt_analyser("Session: 01:00h") is None


def test_parse_hunt_analyser_does_not_misparse_party_payload():
    # Party payload is missing the xp / damage / healing totals, so it
    # must fail the Hunt Analyser schema.
    assert parse_hunt_analyser(PARTY_PAYLOAD) is None


# -- Party hunt ------------------------------------------------------------


def test_parse_party_hunt_roundtrips_canonical_payload():
    session = parse_party_hunt(PARTY_PAYLOAD)
    assert isinstance(session, PartyHuntSession)
    assert session.session == timedelta(hours=3, minutes=10)
    assert session.loot_type == "Market"
    assert session.loot == 28_028_157
    assert session.supplies == 6_748_447
    assert session.balance == 21_279_710
    assert [m.name for m in session.members] == [
        "Abbadinos",
        "Sydnee Sweeney",
        "La Kogzita",
        "Mooony Gaz",
        "The Kogzy",
    ]
    abba = session.members[0]
    assert abba.loot == 18_908_088
    assert abba.supplies == 6_876_058
    assert abba.balance == 12_032_030
    assert abba.damage == 9_000_000
    assert abba.healing == 500_000

    # Member without Damage / Healing rows degrades gracefully.
    sydnee = session.members[1]
    assert sydnee.balance == 9_900_000
    assert sydnee.damage is None
    assert sydnee.healing is None


def test_parse_party_hunt_preserves_negative_balances():
    session = parse_party_hunt(PARTY_PAYLOAD)
    assert session is not None
    la_kogzita = session.members[2]
    assert la_kogzita.balance == -426_000


def test_parse_party_hunt_rejects_solo_payload():
    # The solo payload has no ``Loot Type:`` line, so the sniff check must
    # fall through rather than producing a zero-member PartyHuntSession.
    assert parse_party_hunt(SOLO_PAYLOAD) is None


def test_parse_party_hunt_returns_none_on_garbage():
    assert parse_party_hunt("") is None
    assert parse_party_hunt("not tibia") is None


def test_parse_party_hunt_accepts_decorated_number_fields():
    text = PARTY_PAYLOAD.replace(
        "Balance: 21,279,710",
        "Balance: 21,279,710 (5,319,927 each)",
    ).replace("Balance: -426,000", "Balance: -426,000 gp")
    session = parse_party_hunt(text)
    assert session is not None
    assert session.balance == 21_279_710
    assert session.members[2].balance == -426_000
