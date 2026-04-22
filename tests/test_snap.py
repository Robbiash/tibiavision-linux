"""Tests for :mod:`tvlinux.snap`: pure helpers for edge-snap and grouping."""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QRect

from tvlinux.snap import MirrorGroupManager, compute_snap


def test_snap_right_edge_flush():
    src = QRect(100, 50, 40, 40)
    neighbor = QRect(145, 50, 40, 40)
    out = compute_snap(src, neighbor, threshold=10)
    assert out is not None
    assert out.right() + 1 == neighbor.left()
    assert out.top() == src.top()


def test_snap_left_edge_flush():
    neighbor = QRect(50, 50, 40, 40)
    src = QRect(96, 50, 40, 40)
    out = compute_snap(src, neighbor, threshold=10)
    assert out is not None
    assert out.left() == neighbor.right() + 1


def test_snap_top_edge_flush():
    neighbor = QRect(50, 50, 40, 40)
    src = QRect(55, 96, 40, 40)
    out = compute_snap(src, neighbor, threshold=10)
    assert out is not None
    assert out.top() == neighbor.bottom() + 1


def test_snap_bottom_edge_flush():
    src = QRect(50, 50, 40, 40)
    neighbor = QRect(55, 96, 40, 40)
    out = compute_snap(src, neighbor, threshold=10)
    assert out is not None
    assert out.bottom() + 1 == neighbor.top()


def test_snap_returns_none_when_out_of_threshold():
    src = QRect(0, 0, 40, 40)
    neighbor = QRect(200, 200, 40, 40)
    assert compute_snap(src, neighbor, threshold=10) is None


def test_snap_returns_none_when_no_perpendicular_overlap():
    src = QRect(100, 500, 40, 40)
    neighbor = QRect(145, 50, 40, 40)
    assert compute_snap(src, neighbor, threshold=10) is None


def test_snap_noop_when_already_aligned():
    src = QRect(105, 50, 40, 40)
    neighbor = QRect(145, 50, 40, 40)
    out = compute_snap(src, neighbor, threshold=10)
    assert out is not None
    assert out == src


def test_group_manager_join_and_peers():
    gm = MirrorGroupManager()
    a, b, c = uuid4(), uuid4(), uuid4()

    gm.join(a, b)
    assert gm.peers(a) == {b}
    assert gm.peers(b) == {a}
    assert gm.peers(c) == set()

    gm.join(b, c)
    assert gm.peers(a) == {b, c}
    assert gm.peers(b) == {a, c}
    assert gm.peers(c) == {a, b}


def test_group_manager_merges_two_groups():
    gm = MirrorGroupManager()
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    gm.join(a, b)
    gm.join(c, d)
    assert gm.peers(a) == {b}
    assert gm.peers(c) == {d}
    gm.join(b, c)
    assert gm.peers(a) == {b, c, d}


def test_group_manager_unlink_removes_member():
    gm = MirrorGroupManager()
    a, b, c = uuid4(), uuid4(), uuid4()
    gm.join(a, b)
    gm.join(b, c)
    gm.unlink(b)
    assert gm.peers(b) == set()
    assert gm.peers(a) == {c}


def test_group_manager_unlink_dissolves_pair():
    gm = MirrorGroupManager()
    a, b = uuid4(), uuid4()
    gm.join(a, b)
    gm.unlink(a)
    assert gm.peers(a) == set()
    assert gm.peers(b) == set()


def test_group_manager_forget_unknown_id_is_noop():
    gm = MirrorGroupManager()
    gm.forget(uuid4())
