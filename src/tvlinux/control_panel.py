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
from PySide6.QtGui import QAction, QCloseEvent, QColor, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .about_dialog import AboutDialog
from .regions import Region, RegionManager

ROLE_REGION_ID = Qt.ItemDataRole.UserRole + 1


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

    def __init__(self, regions: RegionManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._regions = regions
        self.setWindowTitle("TibiaVision-Linux")
        self.resize(460, 620)

        self._build_ui()
        self._wire_region_manager()

    # -- UI construction --------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(10)

        self._toolbar = QToolBar("Main", self)
        self._toolbar.setMovable(False)
        self._toolbar.setIconSize(self._toolbar.iconSize() * 0.9)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self._toolbar)

        self._act_add = QAction(QIcon.fromTheme("list-add"), "Add region", self)
        self._act_delete = QAction(QIcon.fromTheme("list-remove"), "Delete", self)
        self._act_show_all = QAction(QIcon.fromTheme("view-visible"), "Show all", self)
        self._act_hide_all = QAction(QIcon.fromTheme("view-hidden"), "Hide all", self)
        self._act_lock_all = QAction(QIcon.fromTheme("object-locked"), "Lock all", self)
        self._act_unlock_all = QAction(QIcon.fromTheme("object-unlocked"), "Unlock all", self)
        self._act_audio = QAction(QIcon.fromTheme("audio-volume-high"), "Audio timers", self)
        self._act_about = QAction(QIcon.fromTheme("help-about"), "About", self)
        for a in (
            self._act_add,
            self._act_delete,
            self._act_show_all,
            self._act_hide_all,
            self._act_lock_all,
            self._act_unlock_all,
            self._act_audio,
            self._act_about,
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

        # Region list.
        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_list_context)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.currentItemChanged.connect(self._on_current_changed)
        delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self._list)
        delete_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_shortcut.activated.connect(self._on_delete_current)

        # Per-region detail group.
        self._detail = self._build_detail_group()

        # Profile row.
        profiles_group = self._build_profiles_group()

        layout.addWidget(self._list, 1)
        layout.addWidget(self._detail)
        layout.addWidget(profiles_group)
        self.setCentralWidget(central)

        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status.showMessage("Waiting for capture session...")

    def _build_detail_group(self) -> QGroupBox:
        group = QGroupBox("Selected region")
        layout = QVBoxLayout(group)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(20, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._opacity_value = QLabel("100%")
        opacity_row.addWidget(self._opacity_slider, 1)
        opacity_row.addWidget(self._opacity_value)
        layout.addLayout(opacity_row)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Border"))
        self._btn_border_color = QPushButton()
        self._btn_border_color.setFixedWidth(36)
        self._btn_border_color.setToolTip("Pick border color")
        self._btn_border_color.clicked.connect(self._on_border_color_clicked)
        self._current_border_color = "#0f8fbf"
        self._apply_border_color_swatch(self._current_border_color)
        style_row.addWidget(self._btn_border_color)
        style_row.addSpacing(8)
        style_row.addWidget(QLabel("Corner radius"))
        self._spin_radius = QSpinBox()
        self._spin_radius.setRange(0, 32)
        self._spin_radius.setSuffix(" px")
        self._spin_radius.setValue(12)
        self._spin_radius.valueChanged.connect(self._on_corner_radius_changed)
        style_row.addWidget(self._spin_radius)
        style_row.addStretch(1)
        layout.addLayout(style_row)

        row2 = QHBoxLayout()
        self._chk_glow = QCheckBox("Border glow")
        self._chk_grid = QCheckBox("Grid overlay")
        self._chk_lock = QCheckBox("Locked")
        row2.addWidget(self._chk_glow)
        row2.addWidget(self._chk_grid)
        row2.addWidget(self._chk_lock)
        row2.addStretch(1)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        self._chk_track_cooldown = QCheckBox("Track cooldown proc")
        self._chk_track_cooldown.setToolTip(
            "Use OCR to watch for cooldown drops (e.g. helmet procs) and flash the border"
        )
        row3.addWidget(self._chk_track_cooldown)
        row3.addStretch(1)
        layout.addLayout(row3)

        self._chk_glow.toggled.connect(self._on_glow_toggled)
        self._chk_grid.toggled.connect(self._on_grid_toggled)
        self._chk_lock.toggled.connect(self._on_lock_toggled)
        self._chk_track_cooldown.toggled.connect(self._on_track_cooldown_toggled)

        group.setEnabled(False)
        return group

    def _apply_border_color_swatch(self, hex_color: str) -> None:
        """Paint the color-picker button itself with the current hex color."""
        c = QColor(hex_color)
        if not c.isValid():
            c = QColor("#0f8fbf")
            hex_color = "#0f8fbf"
        self._current_border_color = hex_color
        text_color = "#000" if c.lightness() > 160 else "#fff"
        self._btn_border_color.setStyleSheet(
            f"QPushButton {{ background: {hex_color}; color: {text_color};"
            " border: 1px solid #444; border-radius: 4px; }}"
        )
        self._btn_border_color.setText(hex_color.upper())

    def _build_profiles_group(self) -> QGroupBox:
        group = QGroupBox("Profiles")
        layout = QHBoxLayout(group)
        self._profile_list = QListWidget()
        self._profile_list.setFixedHeight(92)
        layout.addWidget(self._profile_list, 1)

        buttons_col = QVBoxLayout()
        self._btn_save = QPushButton("Save as...")
        self._btn_load = QPushButton("Load")
        self._btn_delete = QPushButton("Delete")
        self._btn_import = QPushButton("Import")
        self._btn_export = QPushButton("Export")
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
        layout.addLayout(buttons_col)

        return group

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

    def _on_region_removed(self, region_id: UUID) -> None:
        item = self._find_item(region_id)
        if item is not None:
            self._list.takeItem(self._list.row(item))
        if self._list.count() == 0:
            self._detail.setEnabled(False)

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
        self._detail.setEnabled(False)

    # -- Detail editors ---------------------------------------------------------------

    def _refresh_detail(self, region: Region) -> None:
        self._detail.setEnabled(True)
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
            self._detail.setEnabled(False)
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
