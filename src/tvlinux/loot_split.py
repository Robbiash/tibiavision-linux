"""Standard Tibia loot-split math.

Given a set of party members with their individual balances (loot minus
supplies), this module computes who pays whom, and how much, so every member
ends the hunt with an identical share of the total balance. This is the same
math every online Tibia loot-split calculator uses; we keep it in-process so
the Hunt History page can show it without a round-trip to a web form.

Formula::

    total_balance = sum(member.balance for member in members)
    fair_share    = total_balance / n
    transfer(m)   = fair_share - m.balance
        > 0  => m receives `transfer(m)` gp
        < 0  => m pays abs(transfer(m)) gp

Sum of transfers is always 0 modulo integer rounding; we distribute the
rounding residue onto the top earner so the result still sums to zero
exactly (this is what online calculators do and what real parties expect).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .hunt_parser import PartyHuntSession

Direction = Literal["receive", "pay", "even"]


@dataclass(frozen=True)
class LootSplitTransfer:
    name: str
    balance: int  # member's gross balance (loot - supplies)
    transfer: int  # signed: positive => this member RECEIVES, negative => PAYS
    direction: Direction

    @property
    def fair_share(self) -> int:
        return self.balance + self.transfer


def _direction(transfer: int) -> Direction:
    if transfer > 0:
        return "receive"
    if transfer < 0:
        return "pay"
    return "even"


def split_from_party(session: PartyHuntSession) -> list[LootSplitTransfer]:
    """Compute transfers for every member of a Party Hunt session."""
    pairs = [(m.name, int(m.balance)) for m in session.members]
    return split(pairs)


def split(pairs: list[tuple[str, int]]) -> list[LootSplitTransfer]:
    """Compute transfers for an arbitrary ``(name, balance)`` list.

    Used both by ``split_from_party`` and by the manual-entry loot split
    dialog. Preserves member order in the output.
    """
    n = len(pairs)
    if n == 0:
        return []

    total = sum(b for _, b in pairs)
    fair = total // n
    # Integer division loses up to ``n-1`` gp; we give the top earner that
    # extra gp (their "target" becomes ``fair + residue``) so every transfer
    # is a whole number and the sum is exactly zero.
    residue = total - fair * n

    # Each member's target share (fair share, plus any residue for top earner).
    targets = [fair] * n
    if residue != 0:
        top_idx = max(range(n), key=lambda i: pairs[i][1])
        targets[top_idx] += residue

    return [
        LootSplitTransfer(
            name=pairs[i][0],
            balance=pairs[i][1],
            transfer=targets[i] - pairs[i][1],
            direction=_direction(targets[i] - pairs[i][1]),
        )
        for i in range(n)
    ]


def format_transfer_line(t: LootSplitTransfer) -> str:
    """Chat-friendly single line for copy-pasting into Tibia party chat."""
    if t.direction == "even":
        return f"{t.name}: even (no transfer)"
    if t.direction == "receive":
        return f"Transfer {t.transfer} to {t.name}"
    return f"{t.name} transfers {abs(t.transfer)}"


def format_transfers_block(transfers: list[LootSplitTransfer]) -> str:
    """Multi-line block for copy-pasting into party chat."""
    return "\n".join(format_transfer_line(t) for t in transfers)


__all__ = [
    "LootSplitTransfer",
    "format_transfer_line",
    "format_transfers_block",
    "split",
    "split_from_party",
]
