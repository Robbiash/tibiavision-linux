"""System-audio capture path — Svetlana's ears when Discord voice-receive is broken.

This module sidesteps Discord's voice-receive API entirely by capturing the
audio that the user already hears through their headphones via PipeWire's
PulseAudio compat layer (``@DEFAULT_MONITOR@`` source). That stream
contains everyone in a Discord voice call (mixed by Discord client) plus
any other audio the system is playing.

Why: ``discord-ext-voice-recv 0.5.2a179`` only partially supports the
``aead_xchacha20_poly1305_rtpsize`` encryption Discord switched to in
Nov 2024, dropping ~99% of voice frames silently. See
``Obsidian/Projects/Svetlana - System Audio Capture Plan.md`` for the
full design.

The capture loop, VAD/segmentation, and Whisper STT mirror the
``DiscordVoiceLayer`` implementation so the AssistantWindow can swap
sources without restructuring the response path. The ``heard_text``
signal carries the same ``(speaker_name, transcript)`` payload.

Requires:
- ``parec`` (PulseAudio CLI client; ships with ``pulseaudio-utils`` on
  Fedora, works against PipeWire's pulse compat layer).
- ``faster-whisper`` (already required by the assistant extra).
- The user must be on **headphones**. With speakers the bot's own TTS
  output would loop back through the system mic into Discord and back
  into the monitor stream.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, Signal

__all__ = ["SystemAudioListener", "system_audio_available"]

# 16 kHz mono int16 — Whisper-native, no resample needed.
_SAMPLE_RATE = 16_000
_SAMPLE_WIDTH = 2
_CHANNELS = 1
_CHUNK_SAMPLES = 320  # 20 ms at 16 kHz
_CHUNK_BYTES = _CHUNK_SAMPLES * _SAMPLE_WIDTH * _CHANNELS


def system_audio_available() -> tuple[bool, str]:
    """Return ``(ok, reason)`` for whether system-audio capture can run here."""
    if (
        subprocess.run(
            ["which", "parec"],
            capture_output=True,
            check=False,
        ).returncode
        != 0
    ):
        return False, "parec not found (install pulseaudio-utils)"
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except Exception as exc:  # pragma: no cover - import-only check
        return False, f"faster-whisper unavailable: {exc}"
    return True, ""


@dataclass
class _Buffer:
    pcm: bytearray = field(default_factory=bytearray)
    active: bool = False
    started_at: float = 0.0
    last_voice_at: float = 0.0


class SystemAudioListener(QObject):
    """Capture system-audio via parec, run VAD + Whisper STT, emit transcripts."""

    heard_text = Signal(str, str)  # speaker_name (always "voice"), transcript
    status_changed = Signal(str)
    listening_changed = Signal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        device: str | None = None,
        source_label: str = "voice",
    ) -> None:
        super().__init__(parent)

        # Per-instance device override lets us run two listeners in parallel
        # (one on @DEFAULT_MONITOR@ for the call audio, one on @DEFAULT_SOURCE@
        # for the user's own mic) since Discord doesn't echo the user's mic
        # back into their headphones.
        self._device_override = device
        self._source_label = source_label

        # Env-tunable knobs. Defaults tuned higher than DiscordVoiceLayer
        # because system-audio capture picks up the user's keyboard +
        # mouse clicks (~peak 120-200) which kept triggering Whisper for
        # nothing. Real speech peaks well above 1000.
        self._voice_threshold = int(os.environ.get("TVASSIST_SYSAUDIO_VAD_THRESHOLD", "400"))
        self._silence_seconds = float(os.environ.get("TVASSIST_SYSAUDIO_SILENCE_S", "0.45"))
        self._min_utterance_seconds = float(
            os.environ.get("TVASSIST_SYSAUDIO_MIN_UTTERANCE_S", "0.4")
        )
        self._max_utterance_seconds = float(
            os.environ.get("TVASSIST_SYSAUDIO_MAX_UTTERANCE_S", "12.0")
        )
        self._device = (
            (self._device_override or "").strip()
            or os.environ.get("TVASSIST_SYSAUDIO_DEVICE", "@DEFAULT_MONITOR@").strip()
            or "@DEFAULT_MONITOR@"
        )
        self._stt_model_name = (
            os.environ.get("TVASSIST_SYSAUDIO_WHISPER_MODEL", "small").strip() or "small"
        )
        self._stt_device = (
            os.environ.get("TVASSIST_SYSAUDIO_WHISPER_DEVICE", "cpu").strip() or "cpu"
        )
        self._stt_compute_type = (
            os.environ.get("TVASSIST_SYSAUDIO_WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
        )
        # Empty → None (Whisper auto-detects per chunk)
        _lang_env = os.environ.get("TVASSIST_SYSAUDIO_STT_LANGUAGE", "").strip()
        self._stt_language: str | None = _lang_env or None

        self._proc: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._buf = _Buffer()
        self._stt_executor = ThreadPoolExecutor(max_workers=1)
        self._stt_model: Any | None = None
        self._stt_model_lock = threading.Lock()
        self._last_audio_detected_at = 0.0
        self._last_no_words_at = 0.0
        # Pause gate to avoid hearing the bot's own TTS playback.
        # Discord TTS is mixed into the user's headphones, which our
        # PipeWire monitor captures verbatim. _resume_after_monotonic
        # gives us a small grace period after TTS ends so trailing
        # tail-end audio doesn't leak in.
        self._paused = False
        self._resume_after_monotonic = 0.0
        self._tts_tail_grace_seconds = float(
            os.environ.get("TVASSIST_SYSAUDIO_TTS_TAIL_GRACE_S", "0.6")
        )

    # ── Public control ───────────────────────────────────────────────────

    def is_listening(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def set_paused(self, paused: bool) -> None:
        """Gate the capture loop. Discord-layer wires its TTS busy signal here."""
        if paused:
            self._paused = True
            # Discard any in-flight buffered audio so we don't carry
            # half a sentence into the response after we unpause.
            self._buf = _Buffer()
        else:
            # Schedule a delayed resume so the tail of TTS audio
            # (which Discord may still be flushing into our headphones)
            # doesn't get captured the moment we unpause.
            self._resume_after_monotonic = time.monotonic() + self._tts_tail_grace_seconds

    def start(self) -> None:
        if self.is_listening():
            return
        ok, reason = system_audio_available()
        if not ok:
            self.status_changed.emit(f"System audio unavailable: {reason}")
            return
        try:
            self._proc = subprocess.Popen(
                [
                    "parec",
                    f"--device={self._device}",
                    "--format=s16le",
                    f"--rate={_SAMPLE_RATE}",
                    f"--channels={_CHANNELS}",
                    "--latency-msec=20",
                    "--client-name=Svetlana",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as exc:
            self.status_changed.emit(f"Failed to launch parec: {exc}")
            self._proc = None
            return

        self._stop_flag.clear()
        self._buf = _Buffer()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="svetlana-sysaudio-reader",
            daemon=True,
        )
        self._reader_thread.start()
        self.listening_changed.emit(True)
        self.status_changed.emit(
            f"System audio listening active (device={self._device}, model={self._stt_model_name})."
        )

    def stop(self) -> None:
        if self._proc is None:
            return
        self._stop_flag.set()
        proc = self._proc
        self._proc = None
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)
            self._reader_thread = None
        self._buf = _Buffer()
        self.listening_changed.emit(False)
        self.status_changed.emit("System audio listening stopped.")

    # ── Capture loop ─────────────────────────────────────────────────────

    def _reader_loop(self) -> None:
        assert self._proc is not None
        stdout = self._proc.stdout
        if stdout is None:
            return
        try:
            while not self._stop_flag.is_set():
                chunk = stdout.read(_CHUNK_BYTES)
                if not chunk:
                    # parec died or pipe closed
                    break
                if len(chunk) < _CHUNK_BYTES:
                    # Partial read at process end; ignore.
                    continue
                self._process_chunk(chunk)
        except Exception as exc:
            self.status_changed.emit(f"System audio reader error: {exc}")
        finally:
            # parec stderr may carry useful errors (device-not-found etc.)
            if self._proc is not None and self._proc.stderr is not None:
                try:
                    err = self._proc.stderr.read(2048).decode("utf-8", errors="replace")
                    if err.strip():
                        print(
                            f"[svetlana-sysaudio] parec stderr: {err.strip()}",
                            file=sys.stderr,
                            flush=True,
                        )
                except Exception:
                    pass

    def _process_chunk(self, pcm: bytes) -> None:
        now = time.monotonic()
        # Honor the pause gate (set by Discord TTS playback) plus the
        # tail-grace window after unpause.
        if self._paused:
            return
        if now < self._resume_after_monotonic:
            return
        # First chunk after the grace window: clear any state.
        if self._buf.active and self._resume_after_monotonic != 0.0:
            self._buf = _Buffer()
        self._resume_after_monotonic = 0.0
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return
        peak = int(np.max(np.abs(samples)))
        voiced = peak >= self._voice_threshold

        if voiced and (now - self._last_audio_detected_at) >= 25.0:
            self._last_audio_detected_at = now
            self.status_changed.emit(f"System audio detected (peak {peak}).")

        to_submit: bytes | None = None

        if voiced:
            if not self._buf.active:
                self._buf.active = True
                self._buf.started_at = now
                self._buf.last_voice_at = now
                self._buf.pcm.clear()
            self._buf.last_voice_at = now
            self._buf.pcm.extend(pcm)
            if (now - self._buf.started_at) >= self._max_utterance_seconds:
                to_submit = bytes(self._buf.pcm)
                self._buf.active = False
                self._buf.pcm.clear()
        elif self._buf.active and (now - self._buf.last_voice_at) >= self._silence_seconds:
            if (self._buf.last_voice_at - self._buf.started_at) >= self._min_utterance_seconds:
                to_submit = bytes(self._buf.pcm)
            self._buf.active = False
            self._buf.pcm.clear()

        if to_submit is not None:
            self._stt_executor.submit(self._transcribe_chunk, to_submit)

    # ── STT ──────────────────────────────────────────────────────────────

    def _load_stt_model(self) -> Any | None:
        with self._stt_model_lock:
            if self._stt_model is not None:
                return self._stt_model
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                self.status_changed.emit(f"System audio STT unavailable: {exc}")
                return None
            try:
                self._stt_model = WhisperModel(
                    self._stt_model_name,
                    device=self._stt_device,
                    compute_type=self._stt_compute_type,
                )
            except Exception as exc:
                self.status_changed.emit(f"System audio STT load failed: {exc}")
                return None
            self.status_changed.emit(
                f"Loaded system audio STT model: {self._stt_model_name} "
                f"({self._stt_device}/{self._stt_compute_type})"
            )
            return self._stt_model

    def _transcribe_chunk(self, pcm_chunk: bytes) -> None:
        model = self._load_stt_model()
        if model is None:
            return
        fd, wav_path = tempfile.mkstemp(prefix="tvassist-sysaudio-", suffix=".wav")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(_CHANNELS)
                wav.setsampwidth(_SAMPLE_WIDTH)
                wav.setframerate(_SAMPLE_RATE)
                wav.writeframes(pcm_chunk)
            segments, _info = model.transcribe(
                wav_path,
                language=self._stt_language,
                vad_filter=True,
                beam_size=4,
                best_of=2,
                condition_on_previous_text=False,
            )
            transcript = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
        except Exception as exc:
            self.status_changed.emit(f"System audio STT failed: {exc}")
            return
        finally:
            with contextlib.suppress(OSError):
                os.remove(wav_path)

        if transcript:
            # Use the per-instance source label so the handler can tell
            # mic-capture and monitor-capture apart in status messages.
            self.heard_text.emit(self._source_label, transcript)
        else:
            now = time.monotonic()
            if now - self._last_no_words_at >= 20.0:
                self._last_no_words_at = now
                self.status_changed.emit(
                    "System audio captured a chunk, but Whisper found no words. "
                    "Try speaking more clearly or lowering TVASSIST_SYSAUDIO_VAD_THRESHOLD."
                )
