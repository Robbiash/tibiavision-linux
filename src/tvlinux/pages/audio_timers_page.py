"""Audio Timers page -- formerly :class:`AudioTimersDialog`.

Folds the dialog into a page so it lives inside the sidebar nav like every
other feature. The backing :class:`AudioTimerManager` is unchanged; we just
host its UI in-place instead of a modal window.
"""

from __future__ import annotations

from uuid import UUID

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..audio_timers import MAX_HOTKEY_SLOTS, AudioTimer, AudioTimerManager
from ..theme import TOKENS
from ..ui_helpers import card, pill_button, section_label


class AudioTimersPage(QWidget):
    """Same behaviour as the old ``AudioTimersDialog``, embedded in the shell."""

    def __init__(self, manager: AudioTimerManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mgr = manager
        self._build_ui()
        self._mgr.timer_added.connect(lambda _t: self._refresh())
        self._mgr.timer_removed.connect(lambda _tid: self._refresh())
        self._mgr.timer_changed.connect(lambda _t: self._refresh())
        self._mgr.countdown_tick.connect(self._on_tick)
        self._mgr.timer_fired.connect(lambda _tid: self._refresh())
        self._refresh()

    def _build_ui(self) -> None:
        s = TOKENS.spacing
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(s.md)

        list_card = card(self)
        list_layout = list_card.body_layout
        list_layout.addWidget(section_label("TIMERS", list_card))
        self._list = QListWidget(list_card)
        self._list.currentRowChanged.connect(self._on_row_changed)
        list_layout.addWidget(self._list)
        outer.addWidget(list_card, 2)

        detail_card = card(self)
        d_layout = detail_card.body_layout
        d_layout.addWidget(section_label("EDIT", detail_card))

        row = QHBoxLayout()
        self._name_edit = QLineEdit(detail_card)
        self._name_edit.setPlaceholderText("Timer name (e.g. Exura sio)")
        self._duration_spin = QDoubleSpinBox(detail_card)
        self._duration_spin.setRange(0.5, 3600.0)
        self._duration_spin.setSuffix(" s")
        self._duration_spin.setDecimals(1)
        row.addWidget(QLabel("Name", detail_card))
        row.addWidget(self._name_edit, 2)
        row.addWidget(QLabel("Duration", detail_card))
        row.addWidget(self._duration_spin)
        d_layout.addLayout(row)

        sound_row = QHBoxLayout()
        self._sound_label = QLabel("(no sound selected)", detail_card)
        self._sound_label.setProperty("role", "muted")
        self._sound_btn = pill_button("Choose sound...", parent=detail_card)
        sound_row.addWidget(self._sound_label, 1)
        sound_row.addWidget(self._sound_btn)
        d_layout.addLayout(sound_row)

        self._progress = QProgressBar(detail_card)
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m")
        d_layout.addWidget(self._progress)

        btn_row = QHBoxLayout()
        self._btn_add = pill_button("New timer", variant="primary", parent=detail_card)
        self._btn_remove = pill_button("Remove", variant="danger", parent=detail_card)
        self._btn_start = pill_button("Start", parent=detail_card)
        self._btn_stop = pill_button("Stop", variant="ghost", parent=detail_card)
        for b in (self._btn_add, self._btn_remove, self._btn_start, self._btn_stop):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        d_layout.addLayout(btn_row)

        outer.addWidget(detail_card, 3)

        self._btn_add.clicked.connect(self._add_timer)
        self._btn_remove.clicked.connect(self._remove_timer)
        self._btn_start.clicked.connect(self._start_timer)
        self._btn_stop.clicked.connect(self._stop_timer)
        self._sound_btn.clicked.connect(self._pick_sound)
        self._name_edit.editingFinished.connect(self._commit_editor)
        self._duration_spin.editingFinished.connect(self._commit_editor)

    # -- Helpers --------------------------------------------------------------

    def _current_timer(self) -> AudioTimer | None:
        row = self._list.currentRow()
        if row < 0:
            return None
        all_timers = self._mgr.all()
        return all_timers[row] if row < len(all_timers) else None

    def _refresh(self) -> None:
        row = self._list.currentRow()
        self._list.clear()
        for t in self._mgr.all():
            tag = f" [hot {t.hotkey_slot}]" if t.hotkey_slot is not None else ""
            item = QListWidgetItem(f"{t.name} - {t.duration_s:.1f}s{tag}")
            self._list.addItem(item)
        if 0 <= row < self._list.count():
            self._list.setCurrentRow(row)
        else:
            self._on_row_changed(self._list.currentRow())

    def _on_row_changed(self, _row: int) -> None:
        t = self._current_timer()
        if t is None:
            self._name_edit.setText("")
            self._duration_spin.setValue(60.0)
            self._sound_label.setText("(no sound selected)")
            self._progress.setValue(0)
            return
        self._name_edit.setText(t.name)
        self._duration_spin.setValue(t.duration_s)
        self._sound_label.setText(t.sound_path or "(no sound selected)")
        remaining = self._mgr.remaining(t.id)
        if t.duration_s > 0:
            self._progress.setMaximum(int(t.duration_s * 10))
            self._progress.setValue(int(remaining * 10))

    def _on_tick(self, tid: UUID, remaining: float) -> None:
        t = self._current_timer()
        if t is None or t.id != tid:
            return
        self._progress.setValue(int(remaining * 10))

    def _commit_editor(self) -> None:
        t = self._current_timer()
        if t is None:
            return
        changed = False
        name = self._name_edit.text().strip()
        if name and t.name != name:
            t.name = name
            changed = True
        dur = float(self._duration_spin.value())
        if abs(t.duration_s - dur) > 1e-3:
            t.duration_s = dur
            changed = True
        if changed:
            self._mgr.update(t)

    def _pick_sound(self) -> None:
        t = self._current_timer()
        if t is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose sound", "", "Audio files (*.wav *.mp3 *.ogg *.flac)"
        )
        if path:
            t.sound_path = path
            self._mgr.update(t)

    def _add_timer(self) -> None:
        used = {t.hotkey_slot for t in self._mgr.all() if t.hotkey_slot is not None}
        slot: int | None = None
        for i in range(MAX_HOTKEY_SLOTS):
            if i not in used:
                slot = i
                break
        self._mgr.add(AudioTimer(name="New timer", duration_s=60.0, hotkey_slot=slot))

    def _remove_timer(self) -> None:
        t = self._current_timer()
        if t is not None:
            self._mgr.remove(t.id)

    def _start_timer(self) -> None:
        t = self._current_timer()
        if t is not None:
            self._mgr.start(t.id)

    def _stop_timer(self) -> None:
        t = self._current_timer()
        if t is not None:
            self._mgr.stop(t.id)


__all__ = ["AudioTimersPage"]
