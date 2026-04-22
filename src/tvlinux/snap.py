"""Edge-snap and group-drag support for mirror windows.

Two small, pure-Python concepts:

- :func:`compute_snap` takes a moved rectangle and a neighbor and returns a
  copy of the moved rect aligned flush to the neighbor on the closest edge,
  provided the gap is within ``threshold`` pixels. Returns ``None`` when no
  snap should occur.
- :class:`MirrorGroupManager` keeps track of which mirror regions have been
  joined into the same movable group. Groups are ephemeral UI state and are
  never persisted to the profile JSON.

Both pieces are intentionally free of Qt signals / widgets so they can be
unit-tested without spinning up a real ``QApplication``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from PySide6.QtCore import QRect

SNAP_THRESHOLD = 10  # pixels


def compute_snap(src: QRect, neighbor: QRect, threshold: int = SNAP_THRESHOLD) -> QRect | None:
    """Return ``src`` translated to sit flush against ``neighbor``, or ``None``.

    We only ever snap on the edge that is *closest* to the neighbor, and only
    when the source and neighbor already overlap on the perpendicular axis
    (otherwise snapping two far-apart windows would teleport one of them into
    the other's row/column, which feels wrong).
    """
    if threshold <= 0:
        return None

    dx_right = neighbor.left() - src.right() - 1
    dx_left = src.left() - neighbor.right() - 1
    dy_bottom = neighbor.top() - src.bottom() - 1
    dy_top = src.top() - neighbor.bottom() - 1

    vertical_overlap = src.bottom() >= neighbor.top() and src.top() <= neighbor.bottom()
    horizontal_overlap = src.right() >= neighbor.left() and src.left() <= neighbor.right()

    candidates: list[tuple[int, str]] = []
    if vertical_overlap:
        if 0 <= dx_right <= threshold:
            candidates.append((dx_right, "right"))
        if 0 <= dx_left <= threshold:
            candidates.append((dx_left, "left"))
    if horizontal_overlap:
        if 0 <= dy_bottom <= threshold:
            candidates.append((dy_bottom, "bottom"))
        if 0 <= dy_top <= threshold:
            candidates.append((dy_top, "top"))

    if not candidates:
        return None

    _, edge = min(candidates, key=lambda c: c[0])
    out = QRect(src)
    if edge == "right":
        out.moveLeft(neighbor.left() - out.width())
    elif edge == "left":
        out.moveLeft(neighbor.right() + 1)
    elif edge == "bottom":
        out.moveTop(neighbor.top() - out.height())
    elif edge == "top":
        out.moveTop(neighbor.bottom() + 1)
    return out


@dataclass
class MirrorGroupManager:
    """Tracks which mirror regions share a movable group.

    Groups are purely UI state and are not persisted. IDs are allocated as a
    monotonically increasing integer so two independent groups never collide.
    """

    _next_group_id: int = 1
    _groups: dict[int, set[UUID]] = field(default_factory=dict)
    _membership: dict[UUID, int] = field(default_factory=dict)

    def group_of(self, rid: UUID) -> int | None:
        return self._membership.get(rid)

    def peers(self, rid: UUID) -> set[UUID]:
        gid = self._membership.get(rid)
        if gid is None:
            return set()
        return {member for member in self._groups[gid] if member != rid}

    def join(self, a: UUID, b: UUID) -> None:
        """Place ``a`` and ``b`` in the same group, merging groups if needed."""
        if a == b:
            return
        ga = self._membership.get(a)
        gb = self._membership.get(b)
        if ga is not None and gb is not None:
            if ga == gb:
                return
            for member in self._groups[gb]:
                self._membership[member] = ga
            self._groups[ga] |= self._groups[gb]
            del self._groups[gb]
            return
        if ga is not None:
            self._groups[ga].add(b)
            self._membership[b] = ga
            return
        if gb is not None:
            self._groups[gb].add(a)
            self._membership[a] = gb
            return
        gid = self._next_group_id
        self._next_group_id += 1
        self._groups[gid] = {a, b}
        self._membership[a] = gid
        self._membership[b] = gid

    def unlink(self, rid: UUID) -> None:
        """Remove ``rid`` from its group. Destroys groups of size <= 1."""
        gid = self._membership.pop(rid, None)
        if gid is None:
            return
        members = self._groups.get(gid, set())
        members.discard(rid)
        if len(members) <= 1:
            for leftover in members:
                self._membership.pop(leftover, None)
            self._groups.pop(gid, None)
        else:
            self._groups[gid] = members

    def forget(self, rid: UUID) -> None:
        """Called when a region is deleted - identical to unlink."""
        self.unlink(rid)
