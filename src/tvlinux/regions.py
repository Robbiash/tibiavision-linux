"""Region data model + RegionManager.

A ``Region`` is a pure data object describing *what* to show: a rectangle in source-stream
coordinates, plus per-region visual preferences (opacity, glow, grid, lock). The manager
owns the canonical list for a profile and emits signals on every mutation.

Keeping this Qt-free-ish (only ``QObject`` / ``Signal``) means tests can construct a
manager without instantiating a QApplication.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, QRect, Signal

WatchMode = Literal["off", "change"]


@dataclass
class Region:
    """A virtual mirror window description.

    ``rect``: the crop rectangle in the source (captured) stream's pixel space. This is
    independent of where the mirror window sits on the screen or how big it is.

    ``geometry``: where the mirror window is placed on the user's desktop. Optional,
    populated when the window is first created and updated on move/resize.
    """

    id: UUID = field(default_factory=uuid4)
    name: str = "Region"
    rect: QRect = field(default_factory=lambda: QRect(0, 0, 200, 200))
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0  # 0.2 .. 1.0
    border_glow: bool = False
    grid: bool = False
    grid_spacing: int = 16
    always_on_top: bool = True
    geometry: QRect | None = None
    border_color: str = "#0f8fbf"
    corner_radius: int = 12
    track_cooldown: bool = False
    # Pixel-watchdog mode. ``"off"`` = ignore, ``"change"`` = emit a
    # ``PIXEL_WATCH_CHANGED`` event whenever the region's captured pixels
    # change (see :class:`tvlinux.analyzers.pixel_watch.PixelWatchAnalyzer`).
    # Default is off so existing regions behave exactly as before.
    watch_mode: WatchMode = "off"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = str(self.id)
        d["rect"] = _rect_to_list(self.rect)
        d["geometry"] = None if self.geometry is None else _rect_to_list(self.geometry)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Region:
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            name=str(data.get("name", "Region")),
            rect=_list_to_rect(data.get("rect")) or QRect(0, 0, 200, 200),
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            opacity=float(data.get("opacity", 1.0)),
            border_glow=bool(data.get("border_glow", False)),
            grid=bool(data.get("grid", False)),
            grid_spacing=int(data.get("grid_spacing", 16)),
            always_on_top=bool(data.get("always_on_top", True)),
            geometry=_list_to_rect(data.get("geometry")),
            border_color=str(data.get("border_color", "#0f8fbf")),
            corner_radius=int(data.get("corner_radius", 12)),
            track_cooldown=bool(data.get("track_cooldown", False)),
            watch_mode=_coerce_watch_mode(data.get("watch_mode")),
        )


def _rect_to_list(r: QRect) -> list[int]:
    return [r.x(), r.y(), r.width(), r.height()]


def _coerce_watch_mode(v: Any) -> WatchMode:
    """Accept only known values; everything else falls back to ``"off"``.

    Guards against a future schema change that adds e.g. ``"threshold"``
    modes -- old clients opening a new profile won't explode, they'll just
    disable the feature on that region.
    """
    if v == "change":
        return "change"
    return "off"


def _list_to_rect(v: Any) -> QRect | None:
    if not v:
        return None
    try:
        x, y, w, h = (int(i) for i in v)
        return QRect(x, y, w, h)
    except (ValueError, TypeError):
        return None


class RegionManager(QObject):
    """Holds the canonical region list for the current profile."""

    region_added = Signal(Region)
    region_removed = Signal(UUID)
    region_changed = Signal(Region)
    regions_reset = Signal(list)  # list[Region]

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._regions: dict[UUID, Region] = {}
        self._order: list[UUID] = []

    # -- CRUD -------------------------------------------------------------------------

    def add(self, region: Region) -> None:
        if region.id in self._regions:
            raise ValueError(f"duplicate region id: {region.id}")
        region.name = self._unique_name(region.name)
        self._regions[region.id] = region
        self._order.append(region.id)
        self.region_added.emit(region)

    def remove(self, region_id: UUID) -> None:
        if region_id not in self._regions:
            return
        del self._regions[region_id]
        self._order.remove(region_id)
        self.region_removed.emit(region_id)

    def update(self, region: Region) -> None:
        if region.id not in self._regions:
            raise KeyError(region.id)
        self._regions[region.id] = region
        self.region_changed.emit(region)

    def get(self, region_id: UUID) -> Region | None:
        return self._regions.get(region_id)

    def all(self) -> list[Region]:
        return [self._regions[rid] for rid in self._order]

    def __len__(self) -> int:
        return len(self._regions)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.all())

    # -- Bulk operations --------------------------------------------------------------

    def reset(self, regions: list[Region]) -> None:
        self._regions = {r.id: r for r in regions}
        self._order = [r.id for r in regions]
        self.regions_reset.emit(self.all())

    def set_all_visible(self, visible: bool) -> None:
        for r in self.all():
            if r.visible != visible:
                r.visible = visible
                self.region_changed.emit(r)

    def set_all_locked(self, locked: bool) -> None:
        for r in self.all():
            if r.locked != locked:
                r.locked = locked
                self.region_changed.emit(r)

    # -- Serialization ----------------------------------------------------------------

    def to_list(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.all()]

    def load_list(self, data: list[dict[str, Any]]) -> None:
        self.reset([Region.from_dict(d) for d in data])

    # -- Helpers ----------------------------------------------------------------------

    def _unique_name(self, base: str) -> str:
        """Return a name unique within this manager by appending ' (2)', ' (3)', etc."""
        existing = {r.name for r in self._regions.values()}
        if base not in existing:
            return base
        i = 2
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"
