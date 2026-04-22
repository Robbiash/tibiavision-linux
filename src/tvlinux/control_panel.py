"""Main control panel window.

This is the primary user-facing UI:

- A list of regions with drag-to-reorder, rename, toggle-visibility, toggle-lock.
- Global "Hide all / Show all / Lock all / Unlock all" buttons.
- Per-region opacity slider, grid toggle, glow toggle.
- Profile selector and management.
- "Add region" / "Delete region" / "About" / status bar.

The control panel never owns ``MirrorWindow`` instances; the top-level ``Application``
does that. The panel emits intent signals (``add_region_requested``, etc.) and reflects
state changes back into the list.
"""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .about_dialog import AboutDialog
from .regions import Region, RegionManager
from .theme import TOKENS
from .ui_helpers import (
    apply_color_swatch,
    card,
    color_picker_button,
    default_icon_size,
    hline,
    icon,
    pill_button,
    section_label,
    swatch_button,
)

ROLE_REGION_ID = Qt.ItemDataRole.UserRole + 1

PRESET_BORDER_COLORS: list[tuple[str, str]] = [
    ("#e6342c", "Red / Exori Gran"),
    ("#34c759", "Green / heal"),
    ("#0f8fbf", "Blue / utility"),
    ("#a64ae6", "Purple / strong"),
    ("#ffa831", "Orange / buff"),
    ("#b0b0b0", "Grey / neutral"),
]


class ControlPanel(QMainWindow):
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
    show_all_requested = Signal(bool)
    lock_all_requested = Signal(bool)

    save_profile_requested = Signal(str)
    load_profile_requested = Signal(str)
    delete_profile_requested = Signal(str)
    export_profile_requested = Signal()
    import_profile_requested = Signal()

    open_audio_timers_requested = Signal()
    donate_requested = Signal()

    def __init__(self, regions: RegionManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._regions = regions
        self.setWindowTitle("TibiaVision-Linux")
        self.resize(460, 620)

        self._build_ui()
        self._wire_region_manager()

    # -- UI construction --------------------------------------------------------------

    def _build_ui(self) -> None:
        s = TOKENS.spacing

        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(s.md, s.sm, s.md, s.md)
        layout.setSpacing(s.md)

        self._toolbar = QToolBar("Main", self)
        self._toolbar.setMovable(False)
        self._toolbar.setIconSize(default_icon_size())
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._toolbar)

        # Toolbar actions use bundled Lucide icons (falls back to empty icon
        # if an asset is missing, so "missing icon" is a cosmetic glitch
        # rather than a crash).
        self._act_add = QAction(icon("plus"), "Add region", self)
        self._act_delete = QAction(icon("trash-2"), "Delete", self)
        self._act_show_all = QAction(icon("eye"), "Show all", self)
        self._act_hide_all = QAction(icon("eye-off"), "Hide all", self)
        self._act_lock_all = QAction(icon("lock"), "Lock all", self)
        self._act_unlock_all = QAction(icon("unlock"), "Unlock all", self)
        self._act_audio = QAction(icon("volume-2"), "Audio timers", self)
        self._act_about = QAction(icon("info"), "About", self)
        self._act_donate = QAction(icon("heart"), "Donate", self)
        for a in (
            self._act_add,
            self._act_delete,
            self._act_show_all,
            self._act_hide_all,
            self._act_lock_all,
            self._act_unlock_all,
            self._act_audio,
            self._act_about,
            self._act_donate,
        ):
            self._toolbar.addAction(a)
            if a in (self._act_delete, self._act_unlock_all, self._act_audio):
                self._toolbar.addSeparator()

        self._act_add.triggered.connect(self.add_region_requested.emit)
        self._act_delete.triggered.connect(self._on_delete_current)
        self._act_show_all.triggered.connect(lambda: self.show_all_requested.emit(True))
        self._act_hide_all.triggered.connect(lambda: self.show_all_requested.emit(False))
        self._act_lock_all.triggered.connect(lambda: self.lock_all_requested.emit(True))
        self._act_unlock_all.triggered.connect(lambda: self.lock_all_requested.emit(False))
        self._act_audio.triggered.connect(self.open_audio_timers_requested.emit)
        self._act_about.triggered.connect(self._show_about)
        self._act_donate.triggered.connect(self.donate_requested.emit)

        # Region list header + list, inside a card.
        regions_card = card(self)
        header_row = QHBoxLayout()
        self._regions_header = section_label("REGIONS", regions_card)
        self._regions_count = QLabel("0", regions_card)
        self._regions_count.setProperty("role", "muted")
        header_row.addWidget(self._regions_header)
        header_row.addStretch(1)
        header_row.addWidget(self._regions_count)
        regions_card.layout().addLayout(header_row)  # type: ignore[union-attr]

        self._list = QListWidget(regions_card)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_list_context)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.currentItemChanged.connect(self._on_current_changed)
        delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self._list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self._on_delete_current)
        regions_card.layout().addWidget(self._list)  # type: ignore[union-attr]

        # Per-region detail (scrolls; three cards inside).
        self._detail = self._build_detail_scroll()

        # Profiles card.
        profiles_card = self._build_profiles_card()

        layout.addWidget(regions_card, 1)
        layout.addWidget(self._detail, 2)
        layout.addWidget(profiles_card)
        self.setCentralWidget(central)

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status.showMessage("Waiting for capture session...")

    def _build_detail_scroll(self) -> QScrollArea:
        """Build the per-region detail panel as three cards in a scroll area.

        We use a ``QScrollArea`` so the panel does not collapse / clip when
        future features add more controls (e.g. per-region cooldown config
        sliders). The inner container exposes its ``setEnabled`` via a
        pass-through override on the scroll area.
        """
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

    def _build_appearance_card(self, parent: QWidget) -> QFrame:
        frame = card(parent)
        layout = frame.layout()
        assert isinstance(layout, QVBoxLayout)

        layout.addWidget(section_label("APPEARANCE", frame))

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity", frame))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, frame)
        self._opacity_slider.setRange(20, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._opacity_value = QLabel("100%", frame)
        self._opacity_value.setMinimumWidth(36)
        self._opacity_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        opacity_row.addWidget(self._opacity_slider, 1)
        opacity_row.addWidget(self._opacity_value)
        layout.addLayout(opacity_row)

        self._chk_grid = QCheckBox("Grid overlay", frame)
        layout.addWidget(self._chk_grid)
        self._chk_grid.toggled.connect(self._on_grid_toggled)

        return frame

    def _build_border_card(self, parent: QWidget) -> QFrame:
        frame = card(parent)
        layout = frame.layout()
        assert isinstance(layout, QVBoxLayout)

        layout.addWidget(section_label("BORDER", frame))

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color", frame))
        self._btn_border_color = color_picker_button(frame)
        self._btn_border_color.clicked.connect(self._on_border_color_clicked)
        self._current_border_color = "#0f8fbf"
        self._apply_border_color_swatch(self._current_border_color)
        color_row.addWidget(self._btn_border_color)
        color_row.addStretch(1)
        layout.addLayout(color_row)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(TOKENS.spacing.xs)
        preset_row.addWidget(QLabel("Presets", frame))
        for hex_color, label in PRESET_BORDER_COLORS:
            swatch = swatch_button(hex_color, label, frame)
            swatch.clicked.connect(lambda _=False, h=hex_color: self._on_preset_color_clicked(h))
            preset_row.addWidget(swatch)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        layout.addWidget(hline(frame))

        radius_row = QHBoxLayout()
        radius_row.addWidget(QLabel("Corner radius", frame))
        self._spin_radius = QSpinBox(frame)
        self._spin_radius.setRange(0, 32)
        self._spin_radius.setSuffix(" px")
        self._spin_radius.setValue(12)
        self._spin_radius.valueChanged.connect(self._on_corner_radius_changed)
        radius_row.addWidget(self._spin_radius)
        radius_row.addStretch(1)
        layout.addLayout(radius_row)

        self._chk_glow = QCheckBox("Glow effect", frame)
        layout.addWidget(self._chk_glow)
        self._chk_glow.toggled.connect(self._on_glow_toggled)

        return frame

    def _build_behavior_card(self, parent: QWidget) -> QFrame:
        frame = card(parent)
        layout = frame.layout()
        assert isinstance(layout, QVBoxLayout)

        layout.addWidget(section_label("BEHAVIOR", frame))

        self._chk_lock = QCheckBox("Locked (ignore mouse drags)", frame)
        layout.addWidget(self._chk_lock)
        self._chk_lock.toggled.connect(self._on_lock_toggled)

        self._chk_track_cooldown = QCheckBox("Track cooldown proc (OCR)", frame)
        self._chk_track_cooldown.setToolTip(
            "Use OCR to watch for cooldown drops (e.g. helmet procs) and flash the border"
        )
        layout.addWidget(self._chk_track_cooldown)
        self._chk_track_cooldown.toggled.connect(self._on_track_cooldown_toggled)

        return frame

    def _apply_border_color_swatch(self, hex_color: str) -> None:
        c = QColor(hex_color)
        if not c.isValid():
            hex_color = TOKENS.palette.accent
        self._current_border_color = hex_color
        apply_color_swatch(self._btn_border_color, hex_color)

    def _build_profiles_card(self) -> QFrame:
        frame = card(self)
        layout = frame.layout()
        assert isinstance(layout, QVBoxLayout)

        layout.addWidget(section_label("PROFILES", frame))

        body = QHBoxLayout()
        self._profile_list = QListWidget(frame)
        self._profile_list.setFixedHeight(92)
        body.addWidget(self._profile_list, 1)

        buttons_col = QVBoxLayout()
        buttons_col.setSpacing(TOKENS.spacing.xs)
        self._btn_save = pill_button("Save as...", variant="primary", parent=frame)
        self._btn_load = pill_button("Load", parent=frame)
        self._btn_delete = pill_button("Delete", variant="danger", parent=frame)
        self._btn_import = pill_button("Import", variant="ghost", parent=frame)
        self._btn_export = pill_button("Export", variant="ghost", parent=frame)
        self._btn_save.clicked.connect(self._on_save_profile)
        self._btn_load.clicked.connect(self._on_load_profile)
        self._btn_delete.clicked.connect(self._on_delete_profile)
        self._btn_import.clicked.connect(self.import_profile_requested.emit)
        self._btn_export.clicked.connect(self.export_profile_requested.emit)
        for b in (
            self._btn_save,
            self._btn_load,
            self._btn_delete,
            self._btn_import,
            self._btn_export,
        ):
            buttons_col.addWidget(b)
        buttons_col.addStretch(1)
        body.addLayout(buttons_col)
        layout.addLayout(body)

        return frame

    # -- Region manager wiring --------------------------------------------------------

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

    # -- Detail editors ---------------------------------------------------------------

    def _refresh_detail(self, region: Region) -> None:
        self._detail_inner.setEnabled(True)
        self._opacity_slider.blockSignals(True)
        self._chk_glow.blockSignals(True)
        self._chk_grid.blockSignals(True)
        self._chk_lock.blockSignals(True)
        self._spin_radius.blockSignals(True)
        self._chk_track_cooldown.blockSignals(True)
        val = round(region.opacity * 100)
        self._opacity_slider.setValue(max(20, min(100, val)))
        self._opacity_value.setText(f"{self._opacity_slider.value()}%")
        self._chk_glow.setChecked(region.border_glow)
        self._chk_grid.setChecked(region.grid)
        self._chk_lock.setChecked(region.locked)
        self._spin_radius.setValue(max(0, min(32, region.corner_radius)))
        self._chk_track_cooldown.setChecked(region.track_cooldown)
        self._apply_border_color_swatch(region.border_color)
        self._opacity_slider.blockSignals(False)
        self._chk_glow.blockSignals(False)
        self._chk_grid.blockSignals(False)
        self._chk_lock.blockSignals(False)
        self._spin_radius.blockSignals(False)
        self._chk_track_cooldown.blockSignals(False)

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

    # -- Context + actions ------------------------------------------------------------

    def _show_list_context(self, pos) -> None:  # type: ignore[no-untyped-def]
        item = self._list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_rename = menu.addAction("Rename...")
        act_delete = menu.addAction("Delete")
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen is act_rename:
            new_name, ok = QInputDialog.getText(
                self, "Rename region", "New name:", text=item.text()
            )
            if ok and new_name.strip():
                self.rename_region_requested.emit(item.data(ROLE_REGION_ID), new_name.strip())
        elif chosen is act_delete:
            self.delete_region_requested.emit(item.data(ROLE_REGION_ID))

    def _on_delete_current(self) -> None:
        rid = self._current_region_id()
        if rid is not None:
            self.delete_region_requested.emit(rid)

    # -- Profiles ---------------------------------------------------------------------

    def set_profiles(self, names: list[str], current: str | None) -> None:
        self._profile_list.blockSignals(True)
        self._profile_list.clear()
        for n in names:
            item = QListWidgetItem(n)
            if n == current:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self._profile_list.addItem(item)
        self._profile_list.blockSignals(False)

    def _selected_profile(self) -> str | None:
        item = self._profile_list.currentItem()
        return None if item is None else item.text()

    def _on_save_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "Save profile", "Profile name:")
        if ok and name.strip():
            self.save_profile_requested.emit(name.strip())

    def _on_load_profile(self) -> None:
        p = self._selected_profile()
        if p:
            self.load_profile_requested.emit(p)

    def _on_delete_profile(self) -> None:
        p = self._selected_profile()
        if not p:
            return
        if (
            QMessageBox.question(self, "Delete profile?", f"Delete profile '{p}'?")
            == QMessageBox.StandardButton.Yes
        ):
            self.delete_profile_requested.emit(p)

    # -- Misc -------------------------------------------------------------------------

    def set_status(self, text: str) -> None:
        self._status.showMessage(text)

    def _show_about(self) -> None:
        dlg = AboutDialog(self)
        dlg.exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Hide to tray instead of actually quitting; the app lifecycle is owned by
        # the Application object.
        event.ignore()
        self.hide()
