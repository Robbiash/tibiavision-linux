"""Unit tests for regions.RegionManager (pure data, no Qt widgets needed)."""

from __future__ import annotations

from PySide6.QtCore import QRect

from tvlinux.regions import Region, RegionManager


def test_add_emits_signal_and_gives_unique_name(qapp):
    mgr = RegionManager()
    emitted = []
    mgr.region_added.connect(lambda r: emitted.append(r))

    mgr.add(Region(name="A", rect=QRect(0, 0, 10, 10)))
    mgr.add(Region(name="A", rect=QRect(0, 0, 10, 10)))
    mgr.add(Region(name="A", rect=QRect(0, 0, 10, 10)))

    names = [r.name for r in mgr.all()]
    assert names == ["A", "A (2)", "A (3)"]
    assert len(emitted) == 3


def test_remove_emits_signal(qapp):
    mgr = RegionManager()
    r = Region(name="X", rect=QRect(0, 0, 5, 5))
    mgr.add(r)

    removed = []
    mgr.region_removed.connect(lambda rid: removed.append(rid))
    mgr.remove(r.id)
    assert removed == [r.id]
    assert mgr.get(r.id) is None
    assert len(mgr) == 0


def test_roundtrip_serialization(qapp):
    mgr = RegionManager()
    r = Region(
        name="Spell bar",
        rect=QRect(10, 20, 300, 40),
        visible=False,
        locked=True,
        opacity=0.5,
        border_glow=True,
        grid=True,
        grid_spacing=8,
    )
    mgr.add(r)

    data = mgr.to_list()

    mgr2 = RegionManager()
    mgr2.load_list(data)
    r2 = mgr2.all()[0]
    assert r2.name == r.name
    assert r2.rect == r.rect
    assert r2.visible == r.visible
    assert r2.locked == r.locked
    assert abs(r2.opacity - 0.5) < 1e-6
    assert r2.border_glow is True
    assert r2.grid is True
    assert r2.grid_spacing == 8


def test_set_all_visible_emits_changed_only_on_diff(qapp):
    mgr = RegionManager()
    a = Region(name="A", rect=QRect(0, 0, 5, 5), visible=True)
    b = Region(name="B", rect=QRect(0, 0, 5, 5), visible=False)
    mgr.add(a)
    mgr.add(b)

    changes = []
    mgr.region_changed.connect(lambda r: changes.append(r.id))

    mgr.set_all_visible(True)  # only b flips
    assert changes == [b.id]

    changes.clear()
    mgr.set_all_visible(True)  # nothing to do
    assert changes == []
