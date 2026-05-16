"""Discord voice-call bridge for the desktop assistant.

This layer uses a Discord bot account (not a user token) and can:
- join/leave server voice channels by channel id
- listen to live call audio (when discord-ext-voice-recv is installed)
- synthesize and play reply audio back into the call (xAI TTS preferred, edge-tts fallback)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
from PySide6.QtCore import QObject, Signal

__all__ = [
    "DiscordLayerError",
    "DiscordVoiceLayer",
    "parse_channel_id",
]

try:  # pragma: no cover - import availability is environment-dependent
    import discord
except Exception as exc:  # pragma: no cover - import availability is environment-dependent
    discord = None
    _DISCORD_IMPORT_ERROR = str(exc)
else:
    _DISCORD_IMPORT_ERROR = ""

try:  # pragma: no cover - import availability is environment-dependent
    from discord.ext import voice_recv
except Exception as exc:  # pragma: no cover - import availability is environment-dependent
    voice_recv = None
    _VOICE_RECV_IMPORT_ERROR = str(exc)
else:
    _VOICE_RECV_IMPORT_ERROR = ""

try:  # pragma: no cover - import availability is environment-dependent
    import edge_tts
except Exception as exc:  # pragma: no cover - import availability is environment-dependent
    edge_tts = None
    _EDGE_TTS_IMPORT_ERROR = str(exc)
else:
    _EDGE_TTS_IMPORT_ERROR = ""

_PCM_SAMPLE_RATE = 48_000
_PCM_CHANNELS = 2
_PCM_SAMPLE_WIDTH = 2  # 16-bit signed pcm
_PCM_BYTES_PER_SECOND = _PCM_SAMPLE_RATE * _PCM_CHANNELS * _PCM_SAMPLE_WIDTH
_XAI_TTS_API_URL = "https://api.x.ai/v1/tts"
_XAI_TTS_VOICES = {"ara", "eve", "leo", "rex", "sal"}
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_MARKDOWN_RE = re.compile(r"[*_`~#>|]+")
_EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff]")
_SPACE_RE = re.compile(r"\s+")


def _looks_like_cuda_runtime_issue(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("libcublas", "cublas", "cudnn", "cuda"))


def _normalize_tts_text(text: str) -> str:
    """Clean model output so TTS sounds less robotic."""
    cleaned = text.strip()
    if not cleaned:
        return ""
    cleaned = _URL_RE.sub(" link ", cleaned)
    cleaned = cleaned.replace("&", " and ")
    cleaned = cleaned.replace("@", " at ")
    cleaned = _MARKDOWN_RE.sub(" ", cleaned)
    cleaned = _EMOJI_RE.sub(" ", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned)
    return cleaned.strip(" \t\r\n")


def _prepare_stt_pcm(pcm_chunk: bytes) -> tuple[np.ndarray, int]:
    """Downmix Discord PCM to mono and downsample to 16k for Whisper."""
    samples = np.frombuffer(pcm_chunk, dtype=np.int16)
    if samples.size == 0:
        return np.empty(0, dtype=np.int16), 16_000

    if _PCM_CHANNELS > 1 and (samples.size % _PCM_CHANNELS) == 0:
        samples = samples.reshape(-1, _PCM_CHANNELS).mean(axis=1).astype(np.int16)

    sample_rate = _PCM_SAMPLE_RATE
    if sample_rate == 48_000 and samples.size >= 3:
        samples = samples[::3]
        sample_rate = 16_000
    return samples, sample_rate


class DiscordLayerError(RuntimeError):
    """Raised for Discord bridge configuration/runtime issues."""


def parse_channel_id(raw: str) -> int:
    """Parse channel id from raw digits or `<#1234567890>` mention syntax."""
    cleaned = raw.strip()
    if cleaned.startswith("<#") and cleaned.endswith(">"):
        cleaned = cleaned[2:-1]
    if not cleaned.isdigit():
        raise ValueError("Voice channel id must be numeric (or a <#channel> mention).")
    channel_id = int(cleaned)
    if channel_id <= 0:
        raise ValueError("Voice channel id must be a positive integer.")
    return channel_id


@dataclass(slots=True)
class _SpeakerBuffer:
    display_name: str
    active: bool = False
    started_at: float = 0.0
    last_voice_at: float = 0.0
    pcm: bytearray = field(default_factory=bytearray)


class DiscordVoiceLayer(QObject):
    """Threaded Discord client that can join/listen/speak in a voice channel."""

    status_changed = Signal(str)
    joined_changed = Signal(bool)
    heard_text = Signal(str, str)  # speaker name, transcript
    # Emits True when bot starts TTS playback into the voice channel,
    # False when playback completes. SystemAudioListener subscribes to
    # this so it can pause capture and avoid feeding the bot's own
    # voice back into Whisper (infinite-loop scenario).
    tts_busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any | None = None
        self._voice_client: Any | None = None
        self._sink: Any | None = None
        self._state_lock = threading.Lock()
        self._tts_lock: asyncio.Lock | None = None
        self._listen_enabled = True
        self._stt_model: Any | None = None
        self._stt_model_lock = threading.Lock()
        self._stt_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="discord-stt")
        self._speaker_buffers: dict[int, _SpeakerBuffer] = {}
        self._buffers_lock = threading.Lock()

        # Tunable VAD-ish thresholds; environment overrides are useful for noisy channels.
        self._voice_threshold = int(os.environ.get("TVASSIST_DISCORD_VAD_THRESHOLD", "120"))
        self._silence_seconds = float(os.environ.get("TVASSIST_DISCORD_SILENCE_S", "0.45"))
        self._min_utterance_seconds = float(
            os.environ.get("TVASSIST_DISCORD_MIN_UTTERANCE_S", "0.22")
        )
        self._max_utterance_seconds = float(
            os.environ.get("TVASSIST_DISCORD_MAX_UTTERANCE_S", "12.0")
        )
        self._last_audio_detected_status_at = 0.0
        self._last_no_words_status_at = 0.0

        self._stt_model_name = (
            os.environ.get("TVASSIST_DISCORD_WHISPER_MODEL", "base").strip() or "base"
        )
        self._stt_device = os.environ.get("TVASSIST_DISCORD_WHISPER_DEVICE", "cpu").strip() or "cpu"
        self._stt_compute_type = (
            os.environ.get("TVASSIST_DISCORD_WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
        )
        # Empty string env value → None (Whisper auto-detects language per chunk).
        _stt_lang_env = os.environ.get("TVASSIST_DISCORD_STT_LANGUAGE", "en").strip()
        self._stt_language: str | None = _stt_lang_env or None

        self._tts_voice = os.environ.get("TVASSIST_DISCORD_TTS_VOICE", "ara").strip() or "ara"
        self._tts_rate = os.environ.get("TVASSIST_DISCORD_TTS_RATE", "+0%").strip() or "+0%"
        self._tts_pitch = os.environ.get("TVASSIST_DISCORD_TTS_PITCH", "+0Hz").strip() or "+0Hz"
        self._tts_volume = os.environ.get("TVASSIST_DISCORD_TTS_VOLUME", "+0%").strip() or "+0%"
        self._xai_tts_api_key = os.environ.get("XAI_API_KEY", "").strip()
        self._xai_tts_language = (
            os.environ.get("TVASSIST_DISCORD_TTS_LANGUAGE", "en").strip() or "en"
        )
        self._last_low_bitrate_warn_at = 0.0

    @property
    def available(self) -> bool:
        return discord is not None

    @property
    def unavailable_reason(self) -> str:
        if self.available:
            return ""
        return _DISCORD_IMPORT_ERROR or "discord.py is not installed."

    @property
    def hearing_available(self) -> bool:
        if voice_recv is None:
            return False
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception:
            return False
        return True

    @property
    def hearing_unavailable_reason(self) -> str:
        if voice_recv is None:
            return (
                "discord-ext-voice-recv is missing."
                f" Install assistant extras. ({_VOICE_RECV_IMPORT_ERROR})"
            )
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception as exc:
            return f"faster-whisper is unavailable: {exc}"
        return ""

    @property
    def call_tts_available(self) -> bool:
        if self._resolve_xai_tts_api_key():
            return True
        return edge_tts is not None

    @property
    def call_tts_unavailable_reason(self) -> str:
        if self._resolve_xai_tts_api_key():
            return ""
        if edge_tts is not None:
            return ""
        return f"edge-tts is unavailable: {_EDGE_TTS_IMPORT_ERROR}"

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    def set_listening_enabled(self, enabled: bool) -> None:
        self._listen_enabled = bool(enabled)
        if not self.running:
            return
        if self._listen_enabled:
            self._run_coro_threadsafe(self._ensure_listening())
        else:
            self._run_coro_threadsafe(self._stop_listening())

    def set_tts_profile(
        self,
        *,
        voice: str | None = None,
        rate: str | None = None,
        pitch: str | None = None,
        volume: str | None = None,
    ) -> None:
        """Update Discord call TTS profile used for future utterances."""
        if voice is not None:
            cleaned = voice.strip()
            if cleaned:
                self._tts_voice = cleaned
        if rate is not None:
            cleaned = rate.strip()
            if cleaned:
                self._tts_rate = cleaned
        if pitch is not None:
            cleaned = pitch.strip()
            if cleaned:
                self._tts_pitch = cleaned
        if volume is not None:
            cleaned = volume.strip()
            if cleaned:
                self._tts_volume = cleaned

    def set_xai_api_key(self, api_key: str) -> None:
        self._xai_tts_api_key = api_key.strip()

    @property
    def tts_profile_summary(self) -> str:
        voice_key = self._tts_voice.strip().lower()
        provider = "xAI TTS" if voice_key in _XAI_TTS_VOICES else "Edge TTS"
        return (
            f"{provider} / {self._tts_voice} / rate {self._tts_rate} / "
            f"pitch {self._tts_pitch} / volume {self._tts_volume}"
        )

    def _resolve_xai_tts_api_key(self) -> str:
        return self._xai_tts_api_key or os.environ.get("XAI_API_KEY", "").strip()

    def speak_in_call(self, text: str) -> None:
        """Synthesize `text` and play it into the currently joined voice call."""
        cleaned = text.strip()
        if not cleaned:
            return
        if not self.running:
            self.status_changed.emit("Discord bot is not connected.")
            return
        self._run_coro_threadsafe(self._play_tts(cleaned))

    def join_call(self, *, token: str, channel_id: int) -> None:
        """Start Discord client if needed, then join/move into `channel_id`."""
        if not self.available:
            self.status_changed.emit(f"Discord bridge unavailable: {self.unavailable_reason}")
            self.joined_changed.emit(False)
            return
        normalized_token = token.strip()
        if normalized_token.lower().startswith("bot "):
            normalized_token = normalized_token[4:].strip()
        if not normalized_token:
            raise DiscordLayerError("Discord bot token is required.")

        if self.running:
            self.status_changed.emit(
                "Discord bot already connected. Moving to requested channel..."
            )
            self._run_coro_threadsafe(self._join_or_move(channel_id))
            return

        thread = threading.Thread(
            target=self._thread_main,
            name="discord-voice-bridge",
            args=(normalized_token, channel_id),
            daemon=True,
        )
        with self._state_lock:
            self._thread = thread
        self.status_changed.emit("Connecting Discord bot...")
        thread.start()

    def leave_call(self) -> None:
        if not self.running:
            self.joined_changed.emit(False)
            self.status_changed.emit("Discord bot is not connected.")
            return
        self._run_coro_threadsafe(self._leave_and_close())

    def shutdown(self, *, timeout_s: float = 5.0) -> None:
        """Best-effort graceful stop used by window teardown."""
        if self.running:
            self._run_coro_threadsafe(self._leave_and_close())
            thread: threading.Thread | None
            with self._state_lock:
                thread = self._thread
            if thread is not None:
                thread.join(max(0.2, float(timeout_s)))
        self._stt_executor.shutdown(wait=False, cancel_futures=True)

    def _thread_main(self, token: str, channel_id: int) -> None:
        assert discord is not None  # guarded by `available`
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        client = discord.Client(intents=intents)

        with self._state_lock:
            self._loop = loop
            self._client = client
            self._voice_client = None

        @client.event
        async def on_ready() -> None:  # pragma: no cover - network integration
            try:
                await self._join_or_move(channel_id)
            except Exception as exc:  # pragma: no cover - network integration
                self.status_changed.emit(f"Discord join failed: {exc}")

        @client.event
        async def on_disconnect() -> None:  # pragma: no cover - network integration
            self.joined_changed.emit(False)
            self.status_changed.emit("Discord disconnected.")

        try:
            loop.run_until_complete(client.start(token))
        except discord.LoginFailure:
            self.status_changed.emit(
                "Discord login failed. Verify your bot token and bot invite permissions."
            )
        except Exception as exc:
            self.status_changed.emit(f"Discord runtime error: {exc}")
        finally:
            self.joined_changed.emit(False)
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            with self._state_lock:
                self._voice_client = None
                self._client = None
                self._loop = None
                self._thread = None
            loop.close()

    def _run_coro_threadsafe(self, coro) -> None:  # type: ignore[no-untyped-def]
        with self._state_lock:
            loop = self._loop
        if loop is None:
            self.status_changed.emit("Discord event loop is not ready yet.")
            return
        future = asyncio.run_coroutine_threadsafe(coro, loop)

        def _log_done(fut) -> None:  # type: ignore[no-untyped-def]
            try:
                fut.result()
            except Exception as exc:
                self.status_changed.emit(f"Discord operation failed: {exc}")

        future.add_done_callback(_log_done)

    async def _resolve_voice_channel(self, channel_id: int):  # type: ignore[no-untyped-def]
        assert discord is not None
        client = self._client
        if client is None:
            raise DiscordLayerError("Discord client is not initialized.")
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except discord.Forbidden as exc:
                raise DiscordLayerError(
                    "Missing access to that channel. "
                    "Invite the bot to the same server and allow "
                    "View Channel + Connect + Speak on that voice channel."
                ) from exc
            except discord.NotFound as exc:
                raise DiscordLayerError(
                    "Voice channel ID not found. "
                    "Copy the ID again from the target voice channel."
                ) from exc
            except discord.HTTPException as exc:
                raise DiscordLayerError(f"Discord API error while opening channel: {exc}") from exc
        if not isinstance(channel, discord.VoiceChannel | discord.StageChannel):
            raise DiscordLayerError(
                "Target channel is not a voice/stage channel. "
                "Discord bots can only join server voice channels."
            )
        return channel

    async def _join_or_move(self, channel_id: int) -> None:
        channel = await self._resolve_voice_channel(channel_id)
        guild = channel.guild
        voice_client = guild.voice_client
        if voice_client is not None and voice_client.is_connected():
            await voice_client.move_to(channel)
        else:
            connect_kwargs: dict[str, Any] = {"self_deaf": False}
            if voice_recv is not None:
                connect_kwargs["cls"] = voice_recv.VoiceRecvClient
            voice_client = await channel.connect(**connect_kwargs)
        self._voice_client = voice_client
        self.joined_changed.emit(True)
        channel_kbps = int(getattr(channel, "bitrate", 0) or 0) // 1000
        if channel_kbps > 0:
            self.status_changed.emit(
                f"Joined Discord voice: {guild.name} / {channel.name} ({channel_kbps} kbps)"
            )
        else:
            self.status_changed.emit(f"Joined Discord voice: {guild.name} / {channel.name}")
        await self._ensure_listening()

    async def _leave_and_close(self) -> None:
        client = self._client
        if client is None:
            return
        await self._stop_listening()
        voice_client = self._voice_client
        if voice_client is not None and voice_client.is_connected():
            await voice_client.disconnect(force=True)
        self._voice_client = None
        self.joined_changed.emit(False)
        self.status_changed.emit("Left Discord voice channel.")
        await client.close()

    async def _ensure_listening(self) -> None:
        if not self._listen_enabled:
            return
        vc = self._voice_client
        if vc is None or not vc.is_connected():
            return
        if voice_recv is None:
            self.status_changed.emit(
                "Voice listening unavailable (discord-ext-voice-recv missing). "
                "Bot can join call but cannot hear speakers."
            )
            return
        if not isinstance(vc, voice_recv.VoiceRecvClient):
            self.status_changed.emit("Voice client does not support receive mode in this session.")
            return
        if not self.hearing_available:
            self.status_changed.emit(
                "Voice listening unavailable. " + self.hearing_unavailable_reason
            )
            return
        if vc.is_listening():
            return
        import sys as _sys

        def _dbg_rtcp(packet):  # type: ignore[no-untyped-def]
            if not hasattr(self, "_dbg_rtcp_n"):
                self._dbg_rtcp_n = 0
            self._dbg_rtcp_n += 1
            if self._dbg_rtcp_n <= 3 or self._dbg_rtcp_n % 50 == 0:
                print(
                    f"[svetlana-dbg] RTCP #{self._dbg_rtcp_n} type={type(packet).__name__}",
                    file=_sys.stderr,
                    flush=True,
                )

        self._sink = voice_recv.BasicSink(
            self._on_voice_frame,
            rtcp_event=_dbg_rtcp,
            decode=True,
        )
        try:
            vc.listen(self._sink)
            print(
                f"[svetlana-dbg] vc.listen() returned. "
                f"vc_type={type(vc).__name__} "
                f"is_listening={vc.is_listening()} "
                f"sink={self._sink!r}",
                file=_sys.stderr,
                flush=True,
            )
        except Exception as _exc:
            print(f"[svetlana-dbg] vc.listen() RAISED: {_exc!r}", file=_sys.stderr, flush=True)
            raise
        self.status_changed.emit("Discord voice listening active.")

    async def _stop_listening(self) -> None:
        vc = self._voice_client
        if voice_recv is not None and isinstance(vc, voice_recv.VoiceRecvClient):
            with contextlib.suppress(Exception):
                if vc.is_listening():
                    vc.stop_listening()
        self._sink = None
        self._clear_speaker_buffers()

    def _on_voice_frame(self, user, data) -> None:  # type: ignore[no-untyped-def]
        """Called by voice-recv for each incoming PCM frame."""
        # DIAGNOSTIC: count every frame entry
        if not hasattr(self, "_dbg_frames"):
            self._dbg_frames = 0
            self._dbg_voiced = 0
            self._dbg_submits = 0
            self._dbg_last_report = 0.0
        self._dbg_frames += 1
        _now = time.monotonic()
        if _now - self._dbg_last_report >= 3.0:
            self._dbg_last_report = _now
            import sys as _sys

            print(
                f"[svetlana-dbg] frames={self._dbg_frames} voiced={self._dbg_voiced} "
                f"submits={self._dbg_submits} listen_enabled={self._listen_enabled} "
                f"playing={self._voice_client.is_playing() if self._voice_client else None}",
                file=_sys.stderr,
                flush=True,
            )
        if not self._listen_enabled:
            return
        if self._voice_client is not None and self._voice_client.is_playing():
            # Avoid feeding our own TTS playback back into STT.
            return
        if user is not None and getattr(user, "bot", False):
            return
        pcm: bytes | None = getattr(data, "pcm", None)
        if not pcm:
            return

        uid = 0
        speaker_name = "caller"
        if user is not None:
            uid = int(getattr(user, "id", 0) or 0)
            speaker_name = str(
                getattr(user, "display_name", None) or getattr(user, "name", "caller")
            )
        else:
            packet = getattr(data, "packet", None)
            ssrc = int(getattr(packet, "ssrc", 0) or 0)
            if ssrc > 0:
                uid = -ssrc
                speaker_name = f"caller-{ssrc}"
        if uid <= 0:
            return
        now = time.monotonic()
        samples = np.frombuffer(pcm, dtype=np.int16)
        if samples.size == 0:
            return
        peak = int(np.max(np.abs(samples)))
        voiced = peak >= self._voice_threshold
        if (
            voiced
            and (now - self._last_audio_detected_status_at) >= 25.0
            and self._voice_client is not None
            and self._voice_client.is_connected()
        ):
            self._last_audio_detected_status_at = now
            self.status_changed.emit(f"Discord audio detected from call (peak {peak}).")

        to_submit: tuple[str, bytes] | None = None

        with self._buffers_lock:
            buf = self._speaker_buffers.get(uid)
            if buf is None:
                buf = _SpeakerBuffer(display_name=speaker_name)
                self._speaker_buffers[uid] = buf
            else:
                buf.display_name = speaker_name

            if voiced:
                if not buf.active:
                    buf.active = True
                    buf.started_at = now
                    buf.last_voice_at = now
                    buf.pcm.clear()
                buf.last_voice_at = now
                buf.pcm.extend(pcm)
                if (now - buf.started_at) >= self._max_utterance_seconds:
                    to_submit = (buf.display_name, bytes(buf.pcm))
                    buf.active = False
                    buf.pcm.clear()
            elif buf.active and (now - buf.last_voice_at) >= self._silence_seconds:
                if (buf.last_voice_at - buf.started_at) >= self._min_utterance_seconds:
                    to_submit = (buf.display_name, bytes(buf.pcm))
                buf.active = False
                buf.pcm.clear()

        if voiced:
            self._dbg_voiced += 1
        if to_submit is not None:
            self._dbg_submits += 1
            import sys as _sys

            print(
                f"[svetlana-dbg] SUBMIT speaker={to_submit[0]} pcm_bytes={len(to_submit[1])}",
                file=_sys.stderr,
                flush=True,
            )
            self._submit_transcription(to_submit[0], to_submit[1])

    def _submit_transcription(self, speaker_name: str, pcm_chunk: bytes) -> None:
        if not pcm_chunk:
            return
        self._stt_executor.submit(self._transcribe_chunk, speaker_name, pcm_chunk)

    def _transcribe_chunk(self, speaker_name: str, pcm_chunk: bytes) -> None:
        model = self._load_stt_model()
        if model is None:
            return
        stt_samples, stt_sample_rate = _prepare_stt_pcm(pcm_chunk)
        if stt_samples.size == 0:
            return

        fd, wav_path = tempfile.mkstemp(prefix="tvassist-discord-", suffix=".wav")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(_PCM_SAMPLE_WIDTH)
                wav.setframerate(stt_sample_rate)
                wav.writeframes(stt_samples.tobytes())
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
            self.status_changed.emit(f"Discord STT failed: {exc}")
            return
        finally:
            with contextlib.suppress(Exception):
                os.remove(wav_path)
        if transcript:
            self.heard_text.emit(speaker_name, transcript)
        elif time.monotonic() - self._last_no_words_status_at >= 20.0:
            self._last_no_words_status_at = time.monotonic()
            self.status_changed.emit(
                "Discord audio captured, but no words recognized. Try speaking closer/louder."
            )

    def _load_stt_model(self):  # type: ignore[no-untyped-def]
        with self._stt_model_lock:
            if self._stt_model is not None:
                return self._stt_model
            try:
                from faster_whisper import WhisperModel
            except Exception as exc:
                self.status_changed.emit(f"Discord STT unavailable: {exc}")
                return None
            chosen_device = self._stt_device
            chosen_compute_type = self._stt_compute_type
            try:
                self._stt_model = WhisperModel(
                    self._stt_model_name,
                    device=chosen_device,
                    compute_type=chosen_compute_type,
                )
            except Exception as exc:
                if chosen_device != "cpu" and _looks_like_cuda_runtime_issue(exc):
                    self.status_changed.emit(
                        "Discord STT CUDA runtime missing; falling back to CPU."
                    )
                    chosen_device = "cpu"
                    chosen_compute_type = "int8"
                    try:
                        self._stt_model = WhisperModel(
                            self._stt_model_name,
                            device=chosen_device,
                            compute_type=chosen_compute_type,
                        )
                    except Exception as cpu_exc:
                        self.status_changed.emit(f"Discord STT unavailable: {cpu_exc}")
                        return None
                else:
                    self.status_changed.emit(f"Discord STT unavailable: {exc}")
                    return None
            self.status_changed.emit(
                f"Loaded Discord STT model: {self._stt_model_name} ({chosen_device}/{chosen_compute_type})"
            )
            return self._stt_model

    async def _play_tts(self, text: str) -> None:
        lock = self._tts_lock
        if lock is None:
            lock = asyncio.Lock()
            self._tts_lock = lock
        async with lock:
            vc = self._voice_client
            if vc is None or not vc.is_connected():
                self.status_changed.emit("Bot is not in a voice channel.")
                return
            snippet = _normalize_tts_text(text)
            if not snippet:
                return
            if len(snippet) > 1500:
                snippet = snippet[:1500].rsplit(" ", 1)[0] + "..."

            xai_api_key = self._resolve_xai_tts_api_key()
            use_xai_tts = bool(xai_api_key)
            suffix = ".wav" if use_xai_tts else ".mp3"
            fd, audio_path = tempfile.mkstemp(prefix="tvassist-discord-tts-", suffix=suffix)
            os.close(fd)
            was_listening = False
            try:
                if use_xai_tts:
                    audio_bytes = await asyncio.to_thread(
                        self._synthesize_with_xai_tts,
                        snippet,
                        xai_api_key,
                    )
                    # xAI's WAV stream ships with malformed RIFF/data chunk
                    # sizes ("Packet corrupt", "Estimating duration from
                    # bitrate" warnings). Discord's FFmpegOpusAudio inherits
                    # those parse errors and produces silent playback.
                    # Normalize the WAV by re-muxing through a tolerant ffmpeg
                    # pass before handing it to discord.
                    raw_path = audio_path + ".raw"
                    with open(raw_path, "wb") as audio_file:
                        audio_file.write(audio_bytes)
                    try:
                        await asyncio.to_thread(
                            subprocess.run,
                            [
                                "ffmpeg",
                                "-y",
                                "-nostdin",
                                "-loglevel",
                                "error",
                                "-err_detect",
                                "ignore_err",
                                "-fflags",
                                "+discardcorrupt",
                                "-i",
                                raw_path,
                                "-c:a",
                                "pcm_s16le",
                                "-ar",
                                "48000",
                                "-ac",
                                "1",
                                audio_path,
                            ],
                            check=True,
                            timeout=30,
                        )
                    finally:
                        with contextlib.suppress(Exception):
                            os.remove(raw_path)
                else:
                    if edge_tts is None:
                        self.status_changed.emit(
                            "No TTS backend available. Set XAI_API_KEY or install edge-tts."
                        )
                        return
                    communicate = edge_tts.Communicate(
                        text=snippet,
                        voice=self._tts_voice,
                        rate=self._tts_rate,
                        pitch=self._tts_pitch,
                        volume=self._tts_volume,
                    )
                    await communicate.save(audio_path)

                if voice_recv is not None and isinstance(vc, voice_recv.VoiceRecvClient):
                    with contextlib.suppress(Exception):
                        if vc.is_listening():
                            vc.stop_listening()
                            was_listening = True

                loop = asyncio.get_running_loop()
                done = loop.create_future()

                def _after(err: Exception | None) -> None:
                    if err is not None:
                        if not done.done():
                            loop.call_soon_threadsafe(done.set_exception, RuntimeError(str(err)))
                        return
                    if not done.done():
                        loop.call_soon_threadsafe(done.set_result, None)

                target_bitrate = self._target_tts_bitrate_kbps(vc)
                if (
                    target_bitrate <= 56
                    and (time.monotonic() - self._last_low_bitrate_warn_at) >= 90.0
                ):
                    self._last_low_bitrate_warn_at = time.monotonic()
                    self.status_changed.emit(
                        "Discord channel bitrate is low; voice quality is constrained by channel settings."
                    )
                source = discord.FFmpegOpusAudio(
                    audio_path,
                    bitrate=target_bitrate,
                    before_options="-nostdin -thread_queue_size 512",
                    options=(
                        "-application voip -vbr constrained -compression_level 10 "
                        "-packet_loss 0 -fec false -frame_duration 20"
                    ),
                )
                self.tts_busy_changed.emit(True)
                vc.play(source, after=_after)
                try:
                    await done
                finally:
                    self.tts_busy_changed.emit(False)
            except Exception as exc:
                self.status_changed.emit(f"Discord playback failed: {exc}")
                # Defensive: in case we emitted True but raised before False.
                with contextlib.suppress(Exception):
                    self.tts_busy_changed.emit(False)
            finally:
                with contextlib.suppress(Exception):
                    os.remove(audio_path)
                if was_listening and self._listen_enabled:
                    with contextlib.suppress(Exception):
                        await self._ensure_listening()

    def _synthesize_with_xai_tts(self, text: str, api_key: str) -> bytes:
        voice_id = self._tts_voice.strip().lower()
        if voice_id not in _XAI_TTS_VOICES:
            voice_id = "ara"
        payload = {
            "text": text,
            "voice_id": voice_id,
            "language": self._xai_tts_language,
            "output_format": {
                "codec": "wav",
                "sample_rate": 48000,
            },
            "optimize_streaming_latency": 0,
            "text_normalization": True,
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url=_XAI_TTS_API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "audio/wav",
            },
        )
        try:
            with urlopen(request, timeout=90) as response:
                audio_bytes = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"xAI TTS API error ({exc.code}): {detail or exc.reason}") from exc
        except URLError as exc:
            raise RuntimeError(f"xAI TTS network error: {exc.reason}") from exc
        if not audio_bytes:
            raise RuntimeError("xAI TTS returned empty audio.")
        return audio_bytes

    @staticmethod
    def _target_tts_bitrate_kbps(vc: Any) -> int:
        channel = getattr(vc, "channel", None)
        raw = int(getattr(channel, "bitrate", 96000) or 96000)
        kbps = max(32, raw // 1000)
        if kbps <= 64:
            return 48
        if kbps <= 96:
            return 64
        if kbps <= 128:
            return 96
        return min(160, kbps - 12)

    @staticmethod
    def _segment_duration_seconds(pcm: bytes | bytearray) -> float:
        return len(pcm) / float(_PCM_BYTES_PER_SECOND)

    def _clear_speaker_buffers(self) -> None:
        with self._buffers_lock:
            self._speaker_buffers.clear()
