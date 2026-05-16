"""PySide6 desktop UI for Grok-style voice assistant."""

from __future__ import annotations

import html
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..paths import data_dir
from .client import XAIClient
from .discord_layer import DiscordLayerError, DiscordVoiceLayer, parse_channel_id
from .memory import MemoryFact, MemoryStore
from .persona import build_system_prompt
from .quest_oracle import QuestMatch, QuestOracle
from .system_audio import SystemAudioListener
from .voice import LocalSpeaker, WhisperTranscriber, stt_model_from_env

__all__ = ["AssistantWindow"]

_FORCED_MODEL = "grok unhinged"
_DEFAULT_DISCORD_TTS_PRESET = "studio_female"
_DISCORD_TTS_PRESETS: dict[str, dict[str, str]] = {
    "studio_female": {
        "label": "Ara (xAI warm female)",
        "voice": "ara",
        "rate": "+0%",
        "pitch": "+0Hz",
        "volume": "+0%",
    },
    "smooth_multilingual": {
        "label": "Eve (xAI upbeat female)",
        "voice": "eve",
        "rate": "+0%",
        "pitch": "+0Hz",
        "volume": "+0%",
    },
    "bright_clear": {
        "label": "Sal (xAI smooth male)",
        "voice": "sal",
        "rate": "+0%",
        "pitch": "+0Hz",
        "volume": "+0%",
    },
    "warm_british": {
        "label": "Leo (xAI british male)",
        "voice": "leo",
        "rate": "+0%",
        "pitch": "+0Hz",
        "volume": "+0%",
    },
    "calm_male": {
        "label": "Rex (xAI calm male)",
        "voice": "rex",
        "rate": "+0%",
        "pitch": "+0Hz",
        "volume": "+0%",
    },
}
_SOFT_REPLY_REWRITE_PROMPT = """
You are a rewrite pass for Svetlana tone.

Task:
- Rewrite the draft into sharp, vulgar, chaotic Svetlana style.
- Keep the same core meaning and same safety outcome.
- Remove corporate-support fluff such as "I'm here to help", "keep it cool", or moralizing filler.
- Match "Unhinged 18+" energy: dark humor, punchy jabs, no pep-talks.
- If the user sounds stressed or asks for practical help, keep it blunt but genuinely supportive.
- Keep it concise and punchy.
- Never add slurs, hate speech, or explicit sexual content.
""".strip()


class AssistantWindow(QMainWindow):
    """Main window: chat, memory management, and voice controls."""

    reply_ready = Signal(str)
    # Emitted per complete sentence as Grok streams its response. Lets us
    # start TTS for sentence 1 while Grok is still generating sentence 2,3,4.
    # Cuts perceived latency from ~2-3s to ~700ms-1s before bot starts talking.
    sentence_ready = Signal(str)
    error_reported = Signal(str)
    transcript_ready = Signal(str)

    def __init__(self, *, db_path: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TVLinux Super Assistant")
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)

        self._settings = QSettings("tibiavision-linux", "assistant")
        memory_db = db_path or (data_dir() / "assistant_memory.sqlite3")
        self._store = MemoryStore(memory_db)
        self._client = XAIClient()
        self._speaker = LocalSpeaker(self)
        self._transcriber = WhisperTranscriber(model_size=stt_model_from_env())
        self._discord = DiscordVoiceLayer(self)
        # Two parallel listeners: monitor catches the call audio (everyone
        # except the user — Discord doesn't echo user's own mic back), mic
        # catches the user's own voice. Together they cover the whole call.
        self._sysaudio = SystemAudioListener(self, device="@DEFAULT_MONITOR@", source_label="call")
        self._sysaudio_mic = SystemAudioListener(self, device="@DEFAULT_SOURCE@", source_label="me")
        self._pending_discord_inputs: list[tuple[str, str]] = []
        # Hard cooldown after the bot's TTS finishes so trailing Whisper
        # results (from chunks that were in-flight when the TTS pause
        # kicked in) can't trigger a fresh response. Without this we
        # spiral: she TTSes → leftover transcript arrives → Grok →
        # more TTS → forever.
        self._sysaudio_cooldown_until = 0.0
        self._sysaudio_post_tts_cooldown_s = 2.0
        # When user barges in with "stop" / "shut up", we cancel her TTS
        # and want to immediately accept new input — set this flag so
        # _on_tts_busy_changed(False) doesn't re-arm the post-TTS cooldown.
        self._sysaudio_abort_in_progress = False
        self._oracle = QuestOracle()
        self._active_quest: QuestMatch | None = None

        self._busy = False

        self._build_ui()
        self._wire_signals()
        self._load_settings()
        self._restore_recent_chat()
        self._refresh_memory_list()

    # -- UI -----------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._build_connection_box())

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_chat_panel())
        splitter.addWidget(self._build_memory_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready.")

    def _build_connection_box(self) -> QGroupBox:
        box = QGroupBox("xAI Connection", self)
        layout = QFormLayout(box)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._api_key = QLineEdit(box)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("XAI_API_KEY (optional if set in environment)")
        layout.addRow("API key", self._api_key)

        self._model = QLineEdit(box)
        self._model.setPlaceholderText(_FORCED_MODEL)
        self._model.setText(_FORCED_MODEL)
        self._model.setReadOnly(True)
        self._model.setToolTip("Model is locked to grok unhinged.")
        layout.addRow("Model", self._model)

        self._discord_token = QLineEdit(box)
        self._discord_token.setEchoMode(QLineEdit.EchoMode.Password)
        self._discord_token.setPlaceholderText("DISCORD_BOT_TOKEN")
        layout.addRow("Discord bot token", self._discord_token)

        self._discord_channel = QLineEdit(box)
        self._discord_channel.setPlaceholderText("Voice channel ID")
        layout.addRow("Discord voice channel", self._discord_channel)

        discord_buttons = QHBoxLayout()
        discord_buttons.setContentsMargins(0, 0, 0, 0)
        discord_buttons.setSpacing(8)
        self._discord_join = QPushButton("Join Discord call", box)
        self._discord_leave = QPushButton("Leave call", box)
        discord_buttons.addWidget(self._discord_join)
        discord_buttons.addWidget(self._discord_leave)
        discord_buttons.addStretch(1)
        layout.addRow("", _layout_host(discord_buttons, box))

        discord_options = QHBoxLayout()
        discord_options.setContentsMargins(0, 0, 0, 0)
        discord_options.setSpacing(8)
        self._discord_auto_listen = QCheckBox("Auto-hear Discord voice", box)
        self._discord_auto_listen.setChecked(True)
        self._discord_speak_back = QCheckBox("Speak replies in Discord call", box)
        self._discord_speak_back.setChecked(True)
        self._discord_require_wake = QCheckBox("Require wake word", box)
        self._discord_require_wake.setChecked(False)
        discord_options.addWidget(self._discord_auto_listen)
        discord_options.addWidget(self._discord_speak_back)
        discord_options.addWidget(self._discord_require_wake)
        discord_options.addStretch(1)
        layout.addRow("", _layout_host(discord_options, box))

        # System-audio capture row (alternative listening source — captures
        # whatever the user hears in their headphones, so it picks up every
        # speaker in the Discord call without relying on Discord's broken
        # voice-receive API).
        sysaudio_row = QHBoxLayout()
        sysaudio_row.setContentsMargins(0, 0, 0, 0)
        sysaudio_row.setSpacing(8)
        self._sysaudio_listen = QCheckBox("Listen to system audio (headphones required)", box)
        self._sysaudio_listen.setChecked(False)
        self._sysaudio_listen.setToolTip(
            "Capture audio from your default output's PipeWire monitor. "
            "Picks up everyone in your Discord call. You MUST be on headphones — "
            "with speakers, the bot's TTS would feed back into your microphone."
        )
        sysaudio_row.addWidget(self._sysaudio_listen)
        sysaudio_row.addStretch(1)
        layout.addRow("", _layout_host(sysaudio_row, box))

        self._discord_wake_word = QLineEdit(box)
        self._discord_wake_word.setPlaceholderText("svetlana")
        layout.addRow("Discord wake word", self._discord_wake_word)

        self._discord_voice_preset = QComboBox(box)
        for preset_key, preset in _DISCORD_TTS_PRESETS.items():
            self._discord_voice_preset.addItem(preset["label"], preset_key)
        layout.addRow("Discord voice preset", self._discord_voice_preset)

        self._discord_hint = QLabel("", box)
        self._discord_hint.setWordWrap(True)
        self._discord_hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addRow("", self._discord_hint)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)

        self._save_connection = QPushButton("Save connection", box)
        buttons.addWidget(self._save_connection)

        self._read_aloud = QCheckBox("Read replies aloud", box)
        buttons.addWidget(self._read_aloud)
        buttons.addStretch(1)

        self._voice_hint = QLabel("", box)
        self._voice_hint.setWordWrap(True)
        self._voice_hint.setTextFormat(Qt.TextFormat.PlainText)
        layout.addRow("", self._voice_hint)
        layout.addRow("", _layout_host(buttons, box))
        return box

    def _build_chat_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._chat_log = QTextEdit(panel)
        self._chat_log.setReadOnly(True)
        self._chat_log.setPlaceholderText("Conversation appears here...")
        layout.addWidget(self._chat_log, 1)

        self._input = QPlainTextEdit(panel)
        self._input.setPlaceholderText(
            "Type a message, or /remember <fact>, then press Send.\n"
            "Example: /remember I prefer short actionable answers."
        )
        self._input.setMaximumHeight(110)
        layout.addWidget(self._input)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)

        self._send = QPushButton("Send", panel)
        self._send.setDefault(True)
        actions.addWidget(self._send)

        self._dictate = QPushButton("Dictate + Send", panel)
        actions.addWidget(self._dictate)

        self._dictate_seconds = QSpinBox(panel)
        self._dictate_seconds.setRange(3, 20)
        self._dictate_seconds.setSuffix(" s")
        actions.addWidget(self._dictate_seconds)

        self._clear_chat = QPushButton("Clear chat", panel)
        actions.addWidget(self._clear_chat)
        actions.addStretch(1)

        layout.addLayout(actions)
        return panel

    def _build_memory_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        title = QLabel("Long-term memory", panel)
        title.setStyleSheet("font-weight: 700;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Facts are auto-extracted from your messages.\n"
            "Use /remember <fact> to pin something explicitly.",
            panel,
        )
        subtitle.setWordWrap(True)
        subtitle.setProperty("role", "muted")
        layout.addWidget(subtitle)

        self._memory_list = QListWidget(panel)
        self._memory_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self._memory_list, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._remember_input = QPushButton("Remember input", panel)
        self._forget_selected = QPushButton("Forget selected", panel)
        self._clear_memories = QPushButton("Clear all", panel)
        row.addWidget(self._remember_input)
        row.addWidget(self._forget_selected)
        row.addWidget(self._clear_memories)
        layout.addLayout(row)
        return panel

    def _wire_signals(self) -> None:
        self._save_connection.clicked.connect(self._save_connection_settings)
        self._send.clicked.connect(self._on_send_clicked)
        self._dictate.clicked.connect(self._on_dictate_clicked)
        self._clear_chat.clicked.connect(self._on_clear_chat_clicked)
        self._remember_input.clicked.connect(self._remember_current_input)
        self._forget_selected.clicked.connect(self._forget_selected_memory)
        self._clear_memories.clicked.connect(self._on_clear_memories_clicked)
        self._read_aloud.toggled.connect(self._persist_runtime_settings)
        self._dictate_seconds.valueChanged.connect(lambda _v: self._persist_runtime_settings())
        self._discord_join.clicked.connect(self._on_discord_join_clicked)
        self._discord_leave.clicked.connect(self._on_discord_leave_clicked)
        self._discord_auto_listen.toggled.connect(self._on_discord_auto_listen_toggled)
        self._discord_require_wake.toggled.connect(self._persist_runtime_settings)
        self._discord_speak_back.toggled.connect(self._persist_runtime_settings)
        self._discord_wake_word.editingFinished.connect(self._persist_runtime_settings)
        self._discord_voice_preset.currentIndexChanged.connect(
            self._on_discord_voice_preset_changed
        )
        self._discord.status_changed.connect(self._on_discord_status_changed)
        self._discord.joined_changed.connect(self._on_discord_joined_changed)
        self._discord.heard_text.connect(self._on_discord_heard_text)
        self._sysaudio_listen.toggled.connect(self._on_sysaudio_listen_toggled)
        # Reuse the Discord status channel for system-audio messages too —
        # the AssistantWindow only has one status area and "[14:30] System: ..."
        # is the right surface for either source.
        self._sysaudio.status_changed.connect(self._on_discord_status_changed)
        self._sysaudio.heard_text.connect(self._on_sysaudio_heard_text)
        self._sysaudio_mic.status_changed.connect(self._on_discord_status_changed)
        self._sysaudio_mic.heard_text.connect(self._on_sysaudio_heard_text)
        # Pause MONITOR capture during TTS — would otherwise loop her own
        # voice back into Whisper. MIC capture stays active so the user
        # can interrupt with an abort phrase ("stop", "shut up", etc.) —
        # the mic source never carries her TTS, so no feedback risk.
        self._discord.tts_busy_changed.connect(self._sysaudio.set_paused)
        # Bump a cooldown timestamp when TTS ends — chunks that were
        # already submitted to Whisper before the pause kicked in can come
        # back as transcripts seconds later and would re-trigger her.
        self._discord.tts_busy_changed.connect(self._on_tts_busy_changed)

        self.reply_ready.connect(self._on_reply_ready)
        self.sentence_ready.connect(self._on_sentence_ready)
        self.error_reported.connect(self._on_error_reported)
        self.transcript_ready.connect(self._on_transcript_ready)

    # -- Startup / persistence -----------------------------------------------

    def _load_settings(self) -> None:
        saved_key = str(self._settings.value("assistant/api_key", "", str) or "")
        _saved_model = str(self._settings.value("assistant/model", _FORCED_MODEL, str) or "")
        read_aloud = bool(self._settings.value("assistant/read_aloud", False, bool))
        dictate_seconds = int(str(self._settings.value("assistant/dictate_seconds", 6, int) or 6))
        discord_token = str(self._settings.value("assistant/discord_bot_token", "", str) or "")
        discord_channel = str(self._settings.value("assistant/discord_channel_id", "", str) or "")
        discord_auto_listen = bool(
            self._settings.value("assistant/discord_auto_listen", True, bool)
        )
        discord_speak_back = bool(self._settings.value("assistant/discord_speak_back", True, bool))
        discord_require_wake = bool(
            self._settings.value("assistant/discord_require_wake", False, bool)
        )
        discord_wake_word = str(
            self._settings.value("assistant/discord_wake_word", "svetlana", str) or "svetlana"
        )
        discord_voice_preset = str(
            self._settings.value(
                "assistant/discord_voice_preset",
                _DEFAULT_DISCORD_TTS_PRESET,
                str,
            )
            or _DEFAULT_DISCORD_TTS_PRESET
        )
        sysaudio_listen = bool(self._settings.value("assistant/sysaudio_listen", False, bool))

        # Older defaults required a wake word ("svetlana"), which can make the bot seem deaf.
        if discord_require_wake and discord_wake_word.strip().lower() == "svetlana":
            discord_require_wake = False
            self._settings.setValue("assistant/discord_require_wake", False)

        self._api_key.setText(saved_key)
        self._model.setText(_FORCED_MODEL)
        self._read_aloud.setChecked(read_aloud)
        self._dictate_seconds.setValue(max(3, min(20, dictate_seconds)))
        self._discord_token.setText(discord_token)
        self._discord_channel.setText(discord_channel)
        self._discord_auto_listen.setChecked(discord_auto_listen)
        self._discord_speak_back.setChecked(discord_speak_back)
        self._discord_require_wake.setChecked(discord_require_wake)
        self._discord_wake_word.setText(discord_wake_word)
        self._set_discord_voice_preset(
            discord_voice_preset or _DEFAULT_DISCORD_TTS_PRESET, persist=False
        )

        if saved_key:
            self._client.set_api_key(saved_key)
        self._discord.set_xai_api_key(self._client.api_key or "")
        self._client.set_model(_FORCED_MODEL)
        self._settings.setValue("assistant/model", _FORCED_MODEL)
        self._discord.set_listening_enabled(discord_auto_listen)
        # Wire the system-audio toggle last so the start() side effect
        # only fires after everything else is settled.
        self._sysaudio_listen.setChecked(sysaudio_listen)
        if sysaudio_listen:
            self._sysaudio.start()
            self._sysaudio_mic.start()
        self._update_voice_hint()
        self._update_discord_hint()

    def _persist_runtime_settings(self) -> None:
        self._settings.setValue("assistant/read_aloud", self._read_aloud.isChecked())
        self._settings.setValue("assistant/dictate_seconds", int(self._dictate_seconds.value()))
        self._settings.setValue(
            "assistant/discord_auto_listen", self._discord_auto_listen.isChecked()
        )
        self._settings.setValue(
            "assistant/discord_speak_back", self._discord_speak_back.isChecked()
        )
        self._settings.setValue(
            "assistant/discord_require_wake", self._discord_require_wake.isChecked()
        )
        wake_word = self._discord_wake_word.text().strip() or "svetlana"
        self._settings.setValue("assistant/discord_wake_word", wake_word)
        preset_key = str(self._discord_voice_preset.currentData() or _DEFAULT_DISCORD_TTS_PRESET)
        self._settings.setValue("assistant/discord_voice_preset", preset_key)
        self._settings.setValue("assistant/sysaudio_listen", self._sysaudio_listen.isChecked())

    def _save_connection_settings(self) -> None:
        key = self._api_key.text().strip()
        model = _FORCED_MODEL
        self._model.setText(model)
        self._client.set_model(model)
        if key:
            self._client.set_api_key(key)
            self._settings.setValue("assistant/api_key", key)
        else:
            self._client.set_api_key(os.environ.get("XAI_API_KEY", ""))
            self._settings.remove("assistant/api_key")
        self._discord.set_xai_api_key(self._client.api_key or "")
        self._settings.setValue("assistant/model", model)
        discord_token = self._discord_token.text().strip()
        if discord_token:
            self._settings.setValue("assistant/discord_bot_token", discord_token)
        else:
            self._settings.remove("assistant/discord_bot_token")
        channel_text = self._discord_channel.text().strip()
        if channel_text:
            self._settings.setValue("assistant/discord_channel_id", channel_text)
        else:
            self._settings.remove("assistant/discord_channel_id")
        self._persist_runtime_settings()
        self.statusBar().showMessage(
            f"Connection settings saved. Active xAI model id: {self._client.model}",
            4500,
        )

    def _restore_recent_chat(self) -> None:
        for msg in self._store.recent_messages(limit=30):
            speaker = "You" if msg.role == "user" else "Assistant"
            self._append_chat_line(speaker, msg.content)

    def _update_voice_hint(self) -> None:
        speaker_state = "TTS ready" if self._speaker.available else "TTS unavailable"
        if self._speaker.available:
            tts_hint = ""
        else:
            tts_hint = f" ({self._speaker.error})" if self._speaker.error else ""
        if self._transcriber.available:
            stt_hint = "Speech input ready"
        else:
            stt_hint = f"Speech input unavailable: {self._transcriber.unavailable_reason}"
        self._voice_hint.setText(f"{speaker_state}{tts_hint} | {stt_hint}")
        self._dictate.setEnabled(self._transcriber.available)
        self._read_aloud.setEnabled(self._speaker.available)

    def _update_discord_hint(self) -> None:
        if self._discord.available:
            hear = (
                "hear-ready"
                if self._discord.hearing_available
                else f"hear-unavailable ({self._discord.hearing_unavailable_reason})"
            )
            speak = (
                "speak-ready"
                if self._discord.call_tts_available
                else f"speak-unavailable ({self._discord.call_tts_unavailable_reason})"
            )
            text = (
                "Discord bridge ready. Use a bot token + server voice channel id. "
                "Bots cannot join private/group DM calls.\n"
                f"Voice agent capabilities: {hear} | {speak}\n"
                f"Voice and text both use xAI model id: {self._client.model}\n"
                f"Discord voice profile: {self._discord_voice_preset.currentText()} "
                f"({self._discord.tts_profile_summary})"
            )
            self._discord_join.setEnabled(True)
            self._discord_leave.setEnabled(False)
        else:
            text = f"Discord bridge unavailable: {self._discord.unavailable_reason}"
            self._discord_join.setEnabled(False)
            self._discord_leave.setEnabled(False)
        self._discord_hint.setText(text)

    # -- Chat ----------------------------------------------------------------

    def _on_send_clicked(self) -> None:
        if self._busy:
            return
        text = self._input.toPlainText().strip()
        if not text:
            return
        self._input.clear()
        self._submit_prompt(
            display_speaker="You",
            display_text=text,
            model_text=text,
            ingest_memory=True,
        )

    def _submit_prompt(
        self,
        *,
        display_speaker: str,
        display_text: str,
        model_text: str,
        ingest_memory: bool,
    ) -> None:
        self._append_chat_line(display_speaker, display_text)
        self._store.append_message("user", model_text)

        new_memories: list[str] = []
        if ingest_memory:
            new_memories = self._store.ingest_user_text(display_text)
            if new_memories:
                self.statusBar().showMessage(f"Stored {len(new_memories)} memory item(s).", 3500)
                self._refresh_memory_list()

        # Explicit memory command can short-circuit without hitting the API.
        if display_text.strip().lower().startswith("/remember "):
            ack = f"Stored memory: {new_memories[0] if new_memories else '(empty)'}"
            self._append_chat_line("Assistant", ack)
            self._store.append_message("assistant", ack)
            return

        self._set_busy(True, "Thinking...")
        history = self._store.recent_messages(limit=18)
        wake_word = self._discord_wake_word.text().strip() or "svetlana"
        history_pairs = [(item.role, item.content) for item in history]
        model_messages = _prepare_model_messages(history_pairs, wake_word=wake_word, limit=12)
        if not model_messages:
            fallback_user = _sanitize_user_message_for_model(
                model_text, wake_word=wake_word
            ).strip()
            if fallback_user:
                model_messages = [{"role": "user", "content": fallback_user}]
        remembered = self._store.search(model_text, limit=8)
        quest_ctx = self._oracle.format_context(self._active_quest) if self._active_quest else None
        system_prompt = build_system_prompt(
            (memory.text for memory in remembered), quest_context=quest_ctx
        )

        def worker() -> None:
            # Streaming Grok: as tokens arrive, accumulate into a sentence
            # buffer; each completed sentence fires sentence_ready which
            # pipes into discord.speak_in_call (serialized via TTS lock).
            # Bot starts speaking sentence 1 while Grok is still generating
            # sentences 2-N. Big latency win over the old triple-pass
            # synchronous chat() + rewrite + maybe second_pass pattern.
            streamer = _SentenceStreamer(on_sentence=lambda s: self.sentence_ready.emit(s))
            try:
                response = self._client.chat_stream(
                    system_prompt=system_prompt,
                    messages=model_messages,
                    on_chunk=streamer.feed,
                    temperature=1.45,
                    top_p=0.97,
                )
                streamer.flush()
            except Exception as exc:
                self.error_reported.emit(str(exc))
                return
            self.reply_ready.emit(response)

        threading.Thread(target=worker, name="assistant-chat-stream", daemon=True).start()

    def _on_sentence_ready(self, sentence: str) -> None:
        """Streaming hook: each completed sentence from Grok pipes into the
        TTS layer immediately so the bot can start talking before the full
        response has generated. The TTS lock in _play_tts serializes the
        playback so sentences come out in order."""
        cleaned = sentence.strip()
        if not cleaned:
            return
        discord_speak_live = self._discord_speak_back.isChecked() and self._discord.running
        if discord_speak_live:
            self._discord.speak_in_call(cleaned)
        elif self._read_aloud.isChecked():
            self._speaker.speak(cleaned)

    def _on_reply_ready(self, reply: str) -> None:
        # Full text has finished streaming. TTS for each sentence was
        # already queued via _on_sentence_ready; just finalize the chat
        # log + memory store + busy state here. We deliberately DO NOT
        # re-speak the full reply since sentences are already being played.
        self._set_busy(False, "Ready.")
        self._append_chat_line("Assistant", reply)
        self._store.append_message("assistant", reply)
        if self._pending_discord_inputs and self._discord_auto_listen.isChecked():
            QTimer.singleShot(0, self._process_next_discord_pending)

    def _on_error_reported(self, error: str) -> None:
        self._set_busy(False, "Ready.")
        self._append_chat_line("System", error)
        self.statusBar().showMessage(error, 7000)
        if self._pending_discord_inputs and self._discord_auto_listen.isChecked():
            QTimer.singleShot(0, self._process_next_discord_pending)

    def _on_clear_chat_clicked(self) -> None:
        if self._busy:
            return
        answer = QMessageBox.question(
            self,
            "Clear chat history?",
            "This removes local conversation context. Memories stay untouched.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.clear_conversation()
        self._chat_log.clear()
        self.statusBar().showMessage("Chat history cleared.", 3500)

    # -- Voice ----------------------------------------------------------------

    def _on_dictate_clicked(self) -> None:
        if self._busy:
            return
        if not self._transcriber.available:
            self._on_error_reported(self._transcriber.unavailable_reason)
            return
        duration = int(self._dictate_seconds.value())
        self._set_busy(True, f"Listening for {duration}s...")

        def worker() -> None:
            try:
                transcript = self._transcriber.record_and_transcribe(duration_s=duration)
            except Exception as exc:
                self.error_reported.emit(f"Voice capture failed: {exc}")
                return
            self.transcript_ready.emit(transcript)

        threading.Thread(target=worker, name="assistant-stt", daemon=True).start()

    def _on_transcript_ready(self, transcript: str) -> None:
        self._set_busy(False, "Ready.")
        cleaned = transcript.strip()
        if not cleaned:
            self.statusBar().showMessage("No speech detected.", 3500)
            return
        self._input.setPlainText(cleaned)
        self._on_send_clicked()

    # -- Discord --------------------------------------------------------------

    def _on_discord_join_clicked(self) -> None:
        token = (
            self._discord_token.text().strip() or os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        )
        raw_channel = self._discord_channel.text().strip()
        if not token:
            self._on_error_reported(
                "Discord bot token is missing. Set DISCORD_BOT_TOKEN or add it in the UI."
            )
            return
        if not _looks_like_discord_bot_token(token):
            self._on_error_reported(
                "The Discord bot token looks invalid. "
                "Use Bot page token (Reset Token/Copy), not App ID/Public Key."
            )
            return
        try:
            channel_id = parse_channel_id(raw_channel)
        except ValueError as exc:
            self._on_error_reported(str(exc))
            return
        try:
            self._discord.join_call(token=token, channel_id=channel_id)
        except DiscordLayerError as exc:
            self._on_error_reported(str(exc))
            return
        self.statusBar().showMessage("Connecting Discord bot...", 5000)
        self._discord_join.setEnabled(False)
        self._discord_leave.setEnabled(True)

    def _on_discord_leave_clicked(self) -> None:
        self._discord.leave_call()
        self._pending_discord_inputs.clear()
        self._discord_leave.setEnabled(False)
        if self._discord.available:
            self._discord_join.setEnabled(True)

    def _on_discord_status_changed(self, status: str) -> None:
        self.statusBar().showMessage(status, 7000)
        self._append_chat_line("System", status)

    def _on_discord_joined_changed(self, joined: bool) -> None:
        if not self._discord.available:
            return
        self._discord_join.setEnabled(not joined)
        self._discord_leave.setEnabled(joined)
        if joined:
            self._discord.set_listening_enabled(self._discord_auto_listen.isChecked())
            if self._discord_require_wake.isChecked():
                wake = self._discord_wake_word.text().strip() or "svetlana"
                self.statusBar().showMessage(
                    f"Discord connected. Wake word mode is ON ('{wake}').",
                    4500,
                )
            else:
                self.statusBar().showMessage(
                    "Discord connected. Listening without wake word.", 3500
                )

    def _on_discord_auto_listen_toggled(self, enabled: bool) -> None:
        self._discord.set_listening_enabled(enabled)
        self._persist_runtime_settings()

    def _on_sysaudio_listen_toggled(self, enabled: bool) -> None:
        if enabled:
            self._sysaudio.start()
            self._sysaudio_mic.start()
        else:
            self._sysaudio.stop()
            self._sysaudio_mic.stop()
        self._persist_runtime_settings()

    def _on_tts_busy_changed(self, busy: bool) -> None:
        if busy:
            # While playing, push the cooldown deadline far into the future
            # so any in-flight Whisper job that finishes mid-TTS is also
            # rejected by the cooldown gate.
            self._sysaudio_cooldown_until = time.monotonic() + 3600.0
        elif self._sysaudio_abort_in_progress:
            # User barged in — let them speak again instantly.
            self._sysaudio_cooldown_until = 0.0
            self._sysaudio_abort_in_progress = False
        else:
            self._sysaudio_cooldown_until = time.monotonic() + self._sysaudio_post_tts_cooldown_s

    def _on_sysaudio_heard_text(self, speaker: str, transcript: str) -> None:
        """System-audio path equivalent of _on_discord_heard_text.

        Mirrors the wake-word / quest-session / submit logic of the Discord
        handler so the response path stays the same — the only difference is
        the source label and the gating toggle. Refactor candidate once we
        ship Phase B (friend profiles) and need a shared listening core.
        """
        if not self._sysaudio_listen.isChecked():
            return
        cleaned = transcript.strip()
        if not cleaned:
            return
        # Barge-in: abort phrases bypass every other gate. Only accept them
        # from the user's mic (label "me") so the bot's own TTS coming back
        # through monitor capture can't abort itself.
        if speaker == "me" and _is_abort_phrase(cleaned):
            aborted = self._discord.cancel_speech()
            self._sysaudio_abort_in_progress = aborted
            self._sysaudio_cooldown_until = 0.0
            self.statusBar().showMessage(
                f"Abort phrase heard: '{cleaned}' — speech halted."
                if aborted
                else f"Abort phrase heard: '{cleaned}' (no speech in progress).",
                2500,
            )
            return
        # Junk filter: Whisper hallucinates single words like "you", "thanks",
        # "okay" from random noise, music, ad tails. Demand at least 2 words
        # to consider it real input.
        if len(cleaned.split()) < 2:
            self.statusBar().showMessage(
                f"Sysaudio: ignored short fragment '{cleaned}' (likely Whisper noise).",
                2000,
            )
            return
        # Post-TTS cooldown: while she's talking and for a few seconds after,
        # ignore everything sysaudio hears so trailing in-flight transcripts
        # can't trigger a fresh spiral.
        if time.monotonic() < self._sysaudio_cooldown_until:
            self.statusBar().showMessage("Sysaudio: ignored during post-TTS cooldown.", 1500)
            return
        if self._discord_require_wake.isChecked():
            wake_word = (self._discord_wake_word.text().strip() or "svetlana").lower()
            if wake_word and wake_word not in cleaned.lower():
                self.statusBar().showMessage(
                    f"Heard {speaker} on system audio; waiting for wake word '{wake_word}'.",
                    2200,
                )
                return
            cleaned = _strip_wake_word_mentions(cleaned, wake_word)
            if not cleaned:
                self.statusBar().showMessage(
                    f"Heard wake word from {speaker}; waiting for the command after it.",
                    2200,
                )
                return

        lowered = cleaned.lower()
        if any(
            phrase in lowered
            for phrase in ("stop quest", "forget quest", "cancel quest", "new quest")
        ):
            self._active_quest = None
            self.statusBar().showMessage("Quest session cleared.", 2500)
            if not self._busy:
                self._submit_prompt(
                    display_speaker=f"call {speaker}",
                    display_text=cleaned,
                    model_text=f"Someone in the call says: {cleaned}",
                    ingest_memory=False,
                )
            return
        if any(phrase in lowered for phrase in ("what quest", "which quest", "current quest")):
            quest_name = self._active_quest.name if self._active_quest else None
            ack = (
                f"Current quest session: {quest_name}" if quest_name else "No active quest session."
            )
            if not self._busy:
                self._append_chat_line(f"call {speaker}", cleaned)
                self._append_chat_line("Assistant", ack)
                if self._discord_speak_back.isChecked() and self._discord.running:
                    self._discord.speak_in_call(ack)
            return

        # Detect quest mention — pulls real quest/boss data from tibia-reborn
        # into the system prompt so she stops hallucinating Tibia lore.
        detected = self._oracle.search(cleaned)
        if detected is not None:
            self._active_quest = detected
            self.statusBar().showMessage(f"Quest detected: {detected.name}", 3000)

        if not self._busy:
            self._submit_prompt(
                display_speaker=f"call {speaker}",
                display_text=cleaned,
                model_text=f"Someone in the call says: {cleaned}",
                ingest_memory=True,
            )
        else:
            # Queue it for after current reply finishes (mirror of pending
            # discord inputs queue).
            self._pending_discord_inputs.append((speaker, cleaned))

    def _on_discord_voice_preset_changed(self, _index: int) -> None:
        preset_key = str(self._discord_voice_preset.currentData() or _DEFAULT_DISCORD_TTS_PRESET)
        self._set_discord_voice_preset(preset_key, persist=True)
        self.statusBar().showMessage(
            f"Discord voice preset set to: {self._discord_voice_preset.currentText()}",
            2600,
        )

    def _set_discord_voice_preset(self, preset_key: str, *, persist: bool) -> None:
        resolved_key, profile = _discord_tts_profile_from_preset(preset_key)
        index = self._discord_voice_preset.findData(resolved_key)
        if index >= 0 and self._discord_voice_preset.currentIndex() != index:
            self._discord_voice_preset.blockSignals(True)
            self._discord_voice_preset.setCurrentIndex(index)
            self._discord_voice_preset.blockSignals(False)
        self._discord.set_tts_profile(
            voice=profile["voice"],
            rate=profile["rate"],
            pitch=profile["pitch"],
            volume=profile["volume"],
        )
        if persist:
            self._settings.setValue("assistant/discord_voice_preset", resolved_key)
        self._update_discord_hint()

    def _on_discord_heard_text(self, speaker: str, transcript: str) -> None:
        if not self._discord_auto_listen.isChecked():
            return
        cleaned = transcript.strip()
        if not cleaned:
            return
        if self._discord_require_wake.isChecked():
            wake_word = (self._discord_wake_word.text().strip() or "svetlana").lower()
            if wake_word and wake_word not in cleaned.lower():
                self.statusBar().showMessage(
                    f"Heard {speaker} in Discord; waiting for wake word '{wake_word}'.",
                    2200,
                )
                return
            cleaned = _strip_wake_word_mentions(cleaned, wake_word)
            if not cleaned:
                self.statusBar().showMessage(
                    f"Heard wake word from {speaker}; waiting for the command after it.",
                    2200,
                )
                return

        # Handle quest session commands before hitting the model.
        lowered = cleaned.lower()
        if any(
            phrase in lowered
            for phrase in ("stop quest", "forget quest", "cancel quest", "new quest")
        ):
            self._active_quest = None
            self.statusBar().showMessage("Quest session cleared.", 2500)
            if not self._busy:
                self._submit_prompt(
                    display_speaker=f"Discord {speaker}",
                    display_text=cleaned,
                    model_text=f"Discord caller {speaker} says: {cleaned}",
                    ingest_memory=False,
                )
            return
        if any(phrase in lowered for phrase in ("what quest", "which quest", "current quest")):
            quest_name = self._active_quest.name if self._active_quest else None
            ack = (
                f"Current quest session: {quest_name}" if quest_name else "No active quest session."
            )
            if not self._busy:
                self._append_chat_line(f"Discord {speaker}", cleaned)
                self._append_chat_line("Assistant", ack)
                if self._discord_speak_back.isChecked() and self._discord.running:
                    self._discord.speak_in_call(ack)
            return

        # Detect quest mention and update session state.
        detected = self._oracle.search(cleaned)
        if detected is not None:
            self._active_quest = detected
            self.statusBar().showMessage(f"Quest detected: {detected.name}", 3000)

        if self._busy:
            # Keep a short queue so rapid speech does not flood the model.
            if len(self._pending_discord_inputs) < 12:
                self._pending_discord_inputs.append((speaker, cleaned))
            self.statusBar().showMessage("Queued Discord speech while assistant is thinking.", 2500)
            return
        self._submit_prompt(
            display_speaker=f"Discord {speaker}",
            display_text=cleaned,
            model_text=f"Discord caller {speaker} says: {cleaned}",
            ingest_memory=False,
        )

    def _process_next_discord_pending(self) -> None:
        if self._busy or not self._pending_discord_inputs:
            return
        speaker, cleaned = self._pending_discord_inputs.pop(0)
        self._submit_prompt(
            display_speaker=f"Discord {speaker}",
            display_text=cleaned,
            model_text=f"Discord caller {speaker} says: {cleaned}",
            ingest_memory=False,
        )

    # -- Memory UI -------------------------------------------------------------

    def _refresh_memory_list(self) -> None:
        self._memory_list.clear()
        for memory in self._store.list_memories(limit=150):
            self._memory_list.addItem(_memory_to_item(memory))

    def _remember_current_input(self) -> None:
        raw = self._input.toPlainText().strip()
        if not raw:
            self.statusBar().showMessage("Type something first.", 3000)
            return
        self._store.remember(raw, source="manual", score=1.0)
        self._refresh_memory_list()
        self.statusBar().showMessage("Saved memory from input.", 3000)

    def _forget_selected_memory(self) -> None:
        item = self._memory_list.currentItem()
        if item is None:
            self.statusBar().showMessage("Select a memory item first.", 3000)
            return
        memory_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(memory_id, int):
            return
        self._store.forget(memory_id)
        self._refresh_memory_list()
        self.statusBar().showMessage("Memory removed.", 3000)

    def _on_clear_memories_clicked(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear all memories?",
            "This permanently deletes all long-term memory facts.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.clear_memories()
        self._refresh_memory_list()
        self.statusBar().showMessage("All memories cleared.", 3500)

    # -- Helpers ---------------------------------------------------------------

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self._send.setEnabled(not busy)
        self._dictate.setEnabled((not busy) and self._transcriber.available)
        self._clear_chat.setEnabled(not busy)
        self.statusBar().showMessage(status)

    def _append_chat_line(self, speaker: str, content: str) -> None:
        timestamp = datetime.now().strftime("%H:%M")
        safe = html.escape(content).replace("\n", "<br>")
        self._chat_log.append(f"<b>[{timestamp}] {html.escape(speaker)}:</b> {safe}")
        self._chat_log.moveCursor(QTextCursor.MoveOperation.End)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._sysaudio.stop()
        self._sysaudio_mic.stop()
        self._discord.shutdown(timeout_s=2.5)
        super().closeEvent(event)


def _layout_host(layout: QHBoxLayout, parent: QWidget) -> QWidget:
    host = QWidget(parent)
    host.setLayout(layout)
    return host


def _memory_to_item(memory: MemoryFact) -> QListWidgetItem:
    label = f"#{memory.id} [{memory.source}] {memory.text}"
    item = QListWidgetItem(label)
    item.setData(Qt.ItemDataRole.UserRole, memory.id)
    return item


def _looks_like_discord_bot_token(token: str) -> bool:
    """Loose format check to catch common ID/public-key mistakes."""
    stripped = token.strip()
    if not stripped:
        return False
    if stripped.isdigit():
        return False
    if stripped.lower().startswith("bot "):
        stripped = stripped[4:].strip()
    # Discord bot tokens have 3 dot-separated segments.
    return stripped.count(".") == 2 and len(stripped) >= 40


# Sentence boundary: a period/exclam/question (one or more) optionally
# followed by closing quote or paren, then whitespace. Lookbehind blocks
# matches on digit-decimals like "3.14" so we don't break sentences inside
# numeric literals or version strings.
_SENTENCE_END_RE = re.compile(r"(?<![0-9])[.!?]+[\"')\]]*\s+")
# A sentence shorter than this many characters won't be flushed to TTS —
# instead we wait for more tokens to arrive. Avoids one-word/two-word
# micro-emissions that make playback choppy ("Yeah." → "Right.").
_SENTENCE_MIN_CHARS = 25


class _SentenceStreamer:
    """Buffer Grok streaming tokens, emit one completed sentence at a time."""

    def __init__(self, on_sentence: Callable[[str], None]) -> None:
        self._buf = ""
        self._on_sentence = on_sentence

    def feed(self, chunk: str) -> None:
        self._buf += chunk
        while True:
            # Find the FIRST sentence-end whose accumulated text reaches the
            # min-char gate. If only short sentences exist so far ("Yeah.")
            # we batch them with later content rather than emit choppy
            # micro-utterances.
            emit_end = -1
            for match in _SENTENCE_END_RE.finditer(self._buf):
                candidate = self._buf[: match.end()].strip()
                if len(candidate) >= _SENTENCE_MIN_CHARS:
                    emit_end = match.end()
                    break
            if emit_end < 0:
                # No batchable sentence found — wait for more tokens.
                return
            self._on_sentence(self._buf[:emit_end].strip())
            self._buf = self._buf[emit_end:]

    def flush(self) -> None:
        """Emit any leftover at end-of-stream (final sentence without trailing space)."""
        tail = self._buf.strip()
        if tail:
            self._on_sentence(tail)
            self._buf = ""


# Abort-phrase pattern. Matches "stop", "shut up", "shut it", "quiet",
# "be quiet", optionally with "svetlana" / "ok" prefix. Used by the
# sysaudio barge-in path to halt the bot's TTS mid-sentence.
_ABORT_PHRASE_RE = re.compile(
    r"(?i)\b(?:(?:ok(?:ay)?|hey|yo|svetlana)[, ]+)*"
    r"(?:shut\s*(?:up|it)|stop(?:\s+(?:talking|svetlana))?|be\s+quiet|quiet|"
    r"nevermind|never\s+mind|halt)\b"
)


def _is_abort_phrase(text: str) -> bool:
    """Return True if `text` is a short, clearly intentional abort command.

    Guards against false positives by also requiring the utterance to be
    short (< 6 words) — "stop pulling that lever" shouldn't abort her.
    """
    if not text:
        return False
    if len(text.split()) > 4:
        return False
    return bool(_ABORT_PHRASE_RE.search(text))


def _strip_wake_word_mentions(text: str, wake_word: str) -> str:
    """Remove standalone wake-word mentions from user speech text."""
    cleaned = text.strip()
    word = wake_word.strip()
    if not cleaned or not word:
        return cleaned
    pattern = re.compile(rf"(?i)\b{re.escape(word)}\b[,.;:!?]*")
    stripped = pattern.sub(" ", cleaned)
    stripped = re.sub(r"\s+", " ", stripped)
    return stripped.strip(" \t\r\n,.;:!?-")


def _sanitize_user_message_for_model(text: str, *, wake_word: str) -> str:
    """Drop wake-word noise from stored Discord caller messages."""
    cleaned = text.strip()
    if not cleaned.lower().startswith("discord caller ") or " says:" not in cleaned.lower():
        return cleaned
    prefix, body = cleaned.split(" says:", 1)
    body = _strip_wake_word_mentions(body, wake_word)
    if not body:
        return ""
    return f"{prefix} says: {body}"


def _discord_tts_profile_from_preset(preset_key: str) -> tuple[str, dict[str, str]]:
    key = (preset_key or "").strip()
    if key in _DISCORD_TTS_PRESETS:
        preset = _DISCORD_TTS_PRESETS[key]
        return key, preset
    fallback = _DISCORD_TTS_PRESETS[_DEFAULT_DISCORD_TTS_PRESET]
    return _DEFAULT_DISCORD_TTS_PRESET, fallback


def _looks_soft_reply(text: str) -> bool:
    lowered = text.lower()
    soft_markers = (
        "i'm here to help",
        "im here to help",
        "ready to help",
        "help out with whatever you need",
        "keep it cool",
        "dial back",
        "what can i help with",
        "what's up with you",
        "got something specific on your mind",
        "ready to assist",
        "how can i help",
        "let's keep",
    )
    return any(marker in lowered for marker in soft_markers)


def _prepare_model_messages(
    history: list[tuple[str, str]],
    *,
    wake_word: str,
    limit: int,
) -> list[dict[str, str]]:
    prepared: list[dict[str, str]] = []
    for role, content in history:
        cleaned = content
        if role == "user":
            cleaned = _sanitize_user_message_for_model(cleaned, wake_word=wake_word)
        elif role == "assistant":
            cleaned = _strip_wake_word_mentions(cleaned, wake_word)
            # Avoid feeding bland assistant phrasing back into the next turn.
            if _looks_soft_reply(cleaned):
                continue
        else:
            continue
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        prepared.append({"role": role, "content": cleaned})
    if len(prepared) > max(1, int(limit)):
        prepared = prepared[-max(1, int(limit)) :]
    return prepared
