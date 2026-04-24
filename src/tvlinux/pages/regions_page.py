"""Regions page -- the "home" of the shell window.

This is the former body of :class:`tvlinux.control_panel.ControlPanel`,
decoupled from the main window so the shell can nest it in a
:class:`~PySide6.QtWidgets.QStackedWidget` alongside the other pages.

All signals emitted by the old ``ControlPanel`` are re-exposed here so the
top-level ``Application`` wiring is a simple rename rather than a rewrite.
The shell forwards these signals straight to its own public API for tests /
backwards compat.
"""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..regions import Region, RegionManager
from ..theme import TOKENS
from ..ui_helpers import (
    CollapsibleCard,
    apply_color_swatch,
    color_picker_button,
    empty_state,
    hline,
)

ROLE_REGION_ID = Qt.ItemDataRole.UserRole + 1

PRESET_BORDER_COLORS: list[tuple[str, str]] = [
    ("#F43F5E", "Red / urgent"),
    ("#10B981", "Green / heal"),
    ("#00E5FF", "Cyan / utility"),
    ("#A855F7", "Purple / strong"),
    ("#F59E0B", "Orange / buff"),
    ("#94A3B8", "Grey / neutral"),
]


class RegionsPage(QWidget):
    """The regions list + per-region detail editors."""

    add_region_requested = Signal()
    delete_region_requested = Signal(UUID)
    rename_region_requested = Signal(UUID, str)
    toggle_visible_requested = Signal(UUID, bool)
    toggle_lock_requested = Signal(UUID, bool)
    toggle_glow_requested = Signal(UUID, bool)
    toggle_grid_requested = Signal(UUID, bool)
    opacity_requested = Signal(UUID, float)
    border_color_requested = Signal(UUID, str)
    corner_radius_requested = Signal(UUID, int)
    toggle_track_cooldown_requested = Signal(UUID, bool)
    toggle_watch_mode_requested = Signal(UUID, str)
    show_all_requested = Signal(bool)
    lock_all_requested = Signal(bool)

    def __init__(self, regions: RegionManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._regions = regions
        self._build_ui()
        self._wire_region_manager()

    # -- UI construction ------------------------------------------------------

    def _build_ui(self) -> None:
        s = TOKENS.spacing
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(s.md)

        regions_card = CollapsibleCard("REGIONS", self)
        self._regions_card = regions_card
        self._regions_count = QLabel("0 regions", regions_card)
        self._regions_count.setProperty("role", "muted")
        regions_card.add_header_widget(self._regions_count)

        self._list = QListWidget(regions_card)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_list_context)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.currentItemChanged.connect(self._on_current_changed)
        delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self._list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self._on_delete_current)
        regions_card.body_layout.addWidget(self._list)

        # Empty-state: far more inviting than a plain "No regions yet"
        # label -- a first-run user lands here and the primary action
        # is obvious. Keeps the same visibility contract as before
        # (shown only when the list is empty).
        self._empty_hint = empty_state(
            icon_name="layers",
            title="No regions yet",
            subtitle=(
                "Pick a rectangle on the live Tibia feed and we will stream it "
                "into a draggable mirror window."
            ),
            action_label="Create your first region",
            on_action=self.add_region_requested.emit,
            parent=regions_card,
        )
        regions_card.body_layout.addWidget(self._empty_hint)

        self._detail = self._build_detail_scroll()

        layout.addWidget(regions_card, 1)
        layout.addWidget(self._detail, 2)

    def _build_detail_scroll(self) -> QScrollArea:
        s = TOKENS.spacing

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget(scroll)
        self._detail_inner = inner
        detail_layout = QVBoxLayout(inner)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(s.md)

        detail_layout.addWidget(self._build_appearance_card(inner))
        detail_layout.addWidget(self._build_border_card(inner))
        detail_layout.addWidget(self._build_behavior_card(inner))
        detail_layout.addStretch(1)

        scroll.setWidget(inner)
        inner.setEnabled(False)
        return scroll

    def _build_appearance_card(self, parent: QWidget) -> CollapsibleCard:
        c = CollapsibleCard("APPEARANCE", parent)
        layout = c.body_layout

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity", c))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, c)
        self._opacity_slider.setRange(20, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._opacity_value = QLabel("100%", c)
        self._opacity_value.setMinimumWidth(36)
        self._opacity_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        opacity_row.addWidget(self._opacity_slider, 1)
        opacity_row.addWidget(self._opacity_value)
        layout.addLayout(opacity_row)

        self._chk_grid = QCheckBox("Grid overlay", c)
        layout.addWidget(self._chk_grid)
        self._chk_grid.toggled.connect(self._on_grid_toggled)

        return c

    def _build_border_card(self, parent: QWidget) -> CollapsibleCard:
        from ..ui_helpers import swatch_button

        c = CollapsibleCard("BORDER", parent)
        layout = c.body_layout

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color", c))
        self._btn_border_color = color_picker_button(c)
        self._btn_border_color.clicked.connect(self._on_border_color_clicked)
        self._current_border_color = "#0f8fbf"
        self._apply_border_color_swatch(self._current_border_color)
        color_row.addWidget(self._btn_border_color)
        color_row.addStretch(1)
        layout.addLayout(color_row)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(TOKENS.spacing.xs)
        preset_row.addWidget(QLabel("Presets", c))
        for hex_color, label in PRESET_BORDER_COLORS:
            swatch = swatch_button(hex_color, label, c)
            swatch.clicked.connect(lambda _=False, h=hex_color: self._on_preset_color_clicked(h))
            preset_row.addWidget(swatch)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        layout.addWidget(hline(c))

        radius_row = QHBoxLayout()
        radius_row.addWidget(QLabel("Corner radius", c))
        self._spin_radius = QSpinBox(c)
        self._spin_radius.setRange(0, 32)
        self._spin_radius.setSuffix(" px")
        self._spin_radius.setValue(12)
        self._spin_radius.valueChanged.connect(self._on_corner_radius_changed)
        radius_row.addWidget(self._spin_radius)
        radius_row.addStretch(1)
        layout.addLayout(radius_row)

        self._chk_glow = QCheckBox("Glow effect", c)
        layout.addWidget(self._chk_glow)
        self._chk_glow.toggled.connect(self._on_glow_toggled)

        return c

    def _build_behavior_card(self, parent: QWidget) -> CollapsibleCard:
        c = CollapsibleCard("BEHAVIOR", parent)
        layout = c.body_layout

        self._chk_lock = QCheckBox("Locked (ignore mouse drags)", c)
        layout.addWidget(self._chk_lock)
        self._chk_lock.toggled.connect(self._on_lock_toggled)

        self._chk_track_cooldown = QCheckBox("Track cooldown proc (OCR)", c)
        self._chk_track_cooldown.setToolTip(
            "Use OCR to watch for cooldown drops (e.g. helmet procs) and flash the border"
        )
        layout.addWidget(self._chk_track_cooldown)
        self._chk_track_cooldown.toggled.connect(self._on_track_cooldown_toggled)

        return c

    def _apply_border_color_swatch(self, hex_color: str) -> None:
        c = QColor(hex_color)
        if not c.isValid():
            hex_color = TOKENS.palette.accent
        self._current_border_color = hex_color
        apply_color_swatch(self._btn_border_color, hex_color)

    # -- Region manager wiring ------------------------------------------------

    def _wire_region_manager(self) -> None:
        self._regions.region_added.connect(self._on_region_added)
        self._regions.region_removed.connect(self._on_region_removed)
        self._regions.region_changed.connect(self._on_region_changed)
        self._regions.regions_reset.connect(self._on_regions_reset)
        self._on_regions_reset(self._regions.all())

    def _find_item(self, region_id: UUID) -> QListWidgetItem | None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is not None and item.data(ROLE_REGION_ID) == region_id:
                return item
        return None

    def _make_item(self, region: Region) -> QListWidgetItem:
        item = QListWidgetItem(region.name)
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEditable
        )
        item.setData(ROLE_REGION_ID, region.id)
        item.setCheckState(Qt.CheckState.Checked if region.visible else Qt.CheckState.Unchecked)
        return item

    def _on_region_added(self, region: Region) -> None:
        self._list.addItem(self._make_item(region))
        self._refresh_region_count()

    def _on_region_removed(self, region_id: UUID) -> None:
        item = self._find_item(region_id)
        if item is not None:
            self._list.takeItem(self._list.row(item))
        if self._list.count() == 0:
            self._detail_inner.setEnabled(False)
        self._refresh_region_count()

    def _on_region_changed(self, region: Region) -> None:
        item = self._find_item(region.id)
        if item is None:
            return
        self._list.blockSignals(True)
        item.setText(region.name)
        item.setCheckState(Qt.CheckState.Checked if region.visible else Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        current = self._list.currentItem()
        if current is not None and current.data(ROLE_REGION_ID) == region.id:
            self._refresh_detail(region)

    def _on_regions_reset(self, regions: list[Region]) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for r in regions:
            self._list.addItem(self._make_item(r))
        self._list.blockSignals(False)
        self._detail_inner.setEnabled(False)
        self._refresh_region_count()

    def _refresh_region_count(self) -> None:
        count = self._list.count()
        self._regions_count.setText(f"{count} region{'s' if count != 1 else ''}")
        # Flip the list <-> empty-state hint based on whether anything is
        # there; the hint only makes sense when the list is empty.
        self._list.setVisible(count > 0)
        self._empty_hint.setVisible(count == 0)

    # -- Detail editors -------------------------------------------------------

    def _refresh_detail(self, region: Region) -> None:
        self._detail_inner.setEnabled(True)
        for w in (
            self._opacity_slider,
            self._chk_glow,
            self._chk_grid,
            self._chk_lock,
            self._spin_radius,
            self._chk_track_cooldown,
        ):
            w.blockSignals(True)
        val = round(region.opacity * 100)
        self._opacity_slider.setValue(max(20, min(100, val)))
        self._opacity_value.setText(f"{self._opacity_slider.value()}%")
        self._chk_glow.setChecked(region.border_glow)
        self._chk_grid.setChecked(region.grid)
        self._chk_lock.setChecked(region.locked)
        self._spin_radius.setValue(max(0, min(32, region.corner_radius)))
        self._chk_track_cooldown.setChecked(region.track_cooldown)
        self._apply_border_color_swatch(region.border_color)
        for w in (
            self._opacity_slider,
            self._chk_glow,
            self._chk_grid,
            self._chk_lock,
            self._spin_radius,
            self._chk_track_cooldown,
        ):
            w.blockSignals(False)

    def _current_region_id(self) -> UUID | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(ROLE_REGION_ID)

    def _on_current_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self._detail_inner.setEnabled(False)
            return
        region = self._regions.get(current.data(ROLE_REGION_ID))
        if region is not None:
            self._refresh_detail(region)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        region_id: UUID = item.data(ROLE_REGION_ID)
        region = self._regions.get(region_id)
        if region is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        if checked != region.visible:
            self.toggle_visible_requested.emit(region_id, checked)
        if item.text() != region.name and item.text().strip():
            self.rename_region_requested.emit(region_id, item.text().strip())

    def _on_opacity_changed(self, value: int) -> None:
        self._opacity_value.setText(f"{value}%")
        rid = self._current_region_id()
        if rid is not None:
            self.opacity_requested.emit(rid, value / 100.0)

    def _on_glow_toggled(self, on: bool) -> None:
        rid = self._current_region_id()
        if rid is not None:
            self.toggle_glow_requested.emit(rid, on)

    def _on_grid_toggled(self, on: bool) -> None:
        rid = self._current_region_id()
        if rid is not None:
            self.toggle_grid_requested.emit(rid, on)

    def _on_lock_toggled(self, on: bool) -> None:
        rid = self._current_region_id()
        if rid is not None:
            self.toggle_lock_requested.emit(rid, on)

    def _on_border_color_clicked(self) -> None:
        rid = self._current_region_id()
        if rid is None:
            return
        initial = QColor(self._current_border_color)
        if not initial.isValid():
            initial = QColor("#0f8fbf")
        chosen = QColorDialog.getColor(initial, self, "Pick border color")
        if not chosen.isValid():
            return
        hex_color = chosen.name()
        self._apply_border_color_swatch(hex_color)
        self.border_color_requested.emit(rid, hex_color)

    def _on_preset_color_clicked(self, hex_color: str) -> None:
        rid = self._current_region_id()
        if rid is None:
            return
        self._apply_border_color_swatch(hex_color)
        self.border_color_requested.emit(rid, hex_color)

    def _on_corner_radius_changed(self, value: int) -> None:
        rid = self._current_region_id()
        if rid is not None:
            self.corner_radius_requested.emit(rid, int(value))

    def _on_track_cooldown_toggled(self, on: bool) -> None:
        rid = self._current_region_id()
        if rid is not None:
            self.toggle_track_cooldown_requested.emit(rid, on)

    # -- Context + actions -----------------------------------------------------

    def _show_list_context(self, pos) -> None:  # type: ignore[no-untyped-def]
        item = self._list.itemAt(pos)
        if item is None:
            return
        region_id: UUID = item.data(ROLE_REGION_ID)
        region = self._regions.get(region_id)

        menu = QMenu(self)
        act_rename = menu.addAction("Rename...")
        act_delete = menu.addAction("Delete")
        menu.addSeparator()
        is_watching = region is not None and region.watch_mode == "change"
        watch_label = "Stop watching pixels" if is_watching else "Watch pixels here"
        act_watch = menu.addAction(watch_label)

        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is act_rename:
            new_name, ok = QInputDialog.getText(
                self, "Rename region", "New name:", text=item.text()
            )
            if ok and new_name.strip():
                self.rename_region_requested.emit(region_id, new_name.strip())
        elif chosen is act_delete:
            self.delete_region_requested.emit(region_id)
        elif chosen is act_watch:
            new_mode = "off" if is_watching else "change"
            self.toggle_watch_mode_requested.emit(region_id, new_mode)

    def _on_delete_current(self) -> None:
        rid = self._current_region_id()
        if rid is not None:
            self.delete_region_requested.emit(rid)

    def selected_region_id(self) -> UUID | None:
        return self._current_region_id()


__all__ = ["RegionsPage"]
