"""TibiaAudio clone: named countdown timers with custom sounds + global hotkeys.

Data model
----------

Each ``AudioTimer`` is a persistent entry with a name, duration (seconds), and a sound
file path. ``AudioTimerManager`` owns the list, persists it to disk, and drives per-timer
countdowns with ``QTimer``. Playback uses ``QMediaPlayer`` so any Qt-supported format
(.wav, .mp3, .ogg, ...) works.

Each timer can be assigned a shortcut id ("audio_start_0", ..., "audio_start_9") that is
bound via ``GlobalShortcutManager``. Ten slots is enough in practice - Tibia hotkey bar
memory capacity is usually the real bottleneck.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .logging_config import get_logger
from .paths import audio_timers_path

log = get_logger(__name__)

MAX_HOTKEY_SLOTS = 10


@dataclass
class AudioTimer:
    id: UUID = field(default_factory=uuid4)
    name: str = "Timer"
    duration_s: float = 60.0
    sound_path: str = ""
    hotkey_slot: int | None = None  # 0..9, or None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = str(self.id)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> AudioTimer:
        return cls(
            id=UUID(data.get("id")) if data.get("id") else uuid4(),
            name=str(data.get("name", "Timer")),
            duration_s=float(data.get("duration_s", 60.0)),
            sound_path=str(data.get("sound_path", "")),
            hotkey_slot=(int(data["hotkey_slot"]) if data.get("hotkey_slot") is not None else None),
        )


class AudioTimerManager(QObject):
    timer_added = Signal(AudioTimer)
    timer_removed = Signal(UUID)
    timer_changed = Signal(AudioTimer)
    countdown_tick = Signal(UUID, float)  # remaining seconds
    timer_fired = Signal(UUID)

    def __init__(self, path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = path or audio_timers_path()
        self._timers: dict[UUID, AudioTimer] = {}
        self._order: list[UUID] = []
        self._running: dict[UUID, QTimer] = {}
        self._remaining: dict[UUID, float] = {}
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self.load()

    # -- CRUD + persistence -----------------------------------------------------------

    def all(self) -> list[AudioTimer]:
        return [self._timers[tid] for tid in self._order]

    def add(self, timer: AudioTimer) -> None:
        self._timers[timer.id] = timer
        self._order.append(timer.id)
        self.timer_added.emit(timer)
        self.save()

    def remove(self, tid: UUID) -> None:
        self.stop(tid)
        if tid in self._timers:
            del self._timers[tid]
            self._order.remove(tid)
            self.timer_removed.emit(tid)
            self.save()

    def update(self, timer: AudioTimer) -> None:
        if timer.id not in self._timers:
            return
        self._timers[timer.id] = timer
        self.timer_changed.emit(timer)
        self.save()

    def get_by_slot(self, slot: int) -> AudioTimer | None:
        for t in self._timers.values():
            if t.hotkey_slot == slot:
                return t
        return None

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("audio.load_failed", error=str(e))
            return
        self._timers.clear()
        self._order.clear()
        for d in data.get("timers", []):
            t = AudioTimer.from_dict(d)
            self._timers[t.id] = t
            self._order.append(t.id)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "timers": [t.to_dict() for t in self.all()]}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # -- Playback / countdown ---------------------------------------------------------

    def start(self, tid: UUID) -> None:
        timer = self._timers.get(tid)
        if timer is None:
            return
        self.stop(tid)
        self._remaining[tid] = timer.duration_s
        qt = QTimer(self)
        qt.setInterval(100)  # 10 Hz UI updates; fine for seconds-granular timers.

        def on_tick() -> None:
            self._remaining[tid] = max(0.0, self._remaining.get(tid, 0.0) - 0.1)
            self.countdown_tick.emit(tid, self._remaining[tid])
            if self._remaining[tid] <= 0:
                qt.stop()
                self._running.pop(tid, None)
                self._play_sound(timer.sound_path)
                self.timer_fired.emit(tid)

        qt.timeout.connect(on_tick)
        qt.start()
        self._running[tid] = qt

    def stop(self, tid: UUID) -> None:
        qt = self._running.pop(tid, None)
        if qt is not None:
            qt.stop()
        self._remaining.pop(tid, None)

    def remaining(self, tid: UUID) -> float:
        return self._remaining.get(tid, 0.0)

    def is_running(self, tid: UUID) -> bool:
        return tid in self._running

    def _play_sound(self, path: str) -> None:
        if not path:
            return
        self._player.setSource(QUrl.fromLocalFile(path))
        self._audio_out.setVolume(1.0)
        self._player.play()


class AudioTimersDialog(QDialog):
    """UI for managing + exercising audio timers."""

    def __init__(self, manager: AudioTimerManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mgr = manager
        self.setWindowTitle("Audio timers")
        self.resize(520, 440)
        self._build_ui()
        self._mgr.timer_added.connect(lambda _t: self._refresh())
        self._mgr.timer_removed.connect(lambda _tid: self._refresh())
        self._mgr.timer_changed.connect(lambda _t: self._refresh())
        self._mgr.countdown_tick.connect(self._on_tick)
        self._mgr.timer_fired.connect(lambda _tid: self._refresh())
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._list = QListWidget(self)
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list, 1)

        # Detail row
        detail = QWidget(self)
        d_layout = QHBoxLayout(detail)
        d_layout.setContentsMargins(0, 0, 0, 0)
        self._name_edit = QLineEdit(detail)
        self._name_edit.setPlaceholderText("Timer name (e.g. Exura sio)")
        self._duration_spin = QDoubleSpinBox(detail)
        self._duration_spin.setRange(0.5, 3600.0)
        self._duration_spin.setSuffix(" s")
        self._duration_spin.setDecimals(1)
        self._sound_btn = QPushButton("Sound...", detail)
        d_layout.addWidget(QLabel("Name"))
        d_layout.addWidget(self._name_edit, 2)
        d_layout.addWidget(QLabel("Duration"))
        d_layout.addWidget(self._duration_spin)
        d_layout.addWidget(self._sound_btn)
        layout.addWidget(detail)

        self._sound_label = QLabel("(no sound selected)")
        layout.addWidget(self._sound_label)

        # Progress for currently selected
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 1000)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m")
        layout.addWidget(self._progress)

        # Buttons
        row = QHBoxLayout()
        self._btn_add = QPushButton("New")
        self._btn_remove = QPushButton("Remove")
        self._btn_start = QPushButton("Start")
        self._btn_stop = QPushButton("Stop")
        self._btn_close = QPushButton("Close")
        self._btn_start.setProperty("accent", True)
        self._btn_stop.setProperty("danger", True)
        for b in (
            self._btn_add,
            self._btn_remove,
            self._btn_start,
            self._btn_stop,
            self._btn_close,
        ):
            row.addWidget(b)
        layout.addLayout(row)

        self._btn_add.clicked.connect(self._add_timer)
        self._btn_remove.clicked.connect(self._remove_timer)
        self._btn_start.clicked.connect(self._start_timer)
        self._btn_stop.clicked.connect(self._stop_timer)
        self._btn_close.clicked.connect(self.close)
        self._sound_btn.clicked.connect(self._pick_sound)
        self._name_edit.editingFinished.connect(self._commit_editor)
        self._duration_spin.editingFinished.connect(self._commit_editor)

    # -- Helpers ---------------------------------------------------------------------

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
        if row >= 0 and row < self._list.count():
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
        if t.name != self._name_edit.text().strip():
            t.name = self._name_edit.text().strip() or t.name
            changed = True
        if abs(t.duration_s - self._duration_spin.value()) > 1e-3:
            t.duration_s = self._duration_spin.value()
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
        # Auto-assign the next free hotkey slot if possible.
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
