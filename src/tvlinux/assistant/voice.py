"""Voice input/output helpers for the assistant UI.

Text-to-speech uses Qt (`QTextToSpeech`) when available.
Speech-to-text uses optional local dependencies:
    faster-whisper + sounddevice + soundfile
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PySide6.QtCore import QObject

__all__ = ["LocalSpeaker", "WhisperTranscriber"]


def _looks_like_cuda_runtime_issue(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("libcublas", "cublas", "cudnn", "cuda"))


class LocalSpeaker(QObject):
    """Best-effort local TTS wrapper around Qt TextToSpeech."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine: Any | None = None
        self._error: str | None = None
        try:
            from PySide6.QtTextToSpeech import QTextToSpeech
        except Exception as exc:  # pragma: no cover - depends on local Qt install
            self._error = f"Qt TextToSpeech unavailable: {exc}"
            return
        engine = QTextToSpeech(self)
        if not engine.availableEngines():
            self._error = "Qt TextToSpeech has no installed engines on this system."
            return
        self._select_voice(engine)
        self._engine = engine

    @property
    def available(self) -> bool:
        return self._engine is not None

    @property
    def error(self) -> str | None:
        return self._error

    def speak(self, text: str) -> None:
        if not self.available:
            return
        if not text.strip():
            return
        snippet = text.strip()
        # Keep spoken responses short by default.
        if len(snippet) > 700:
            snippet = snippet[:700].rsplit(" ", 1)[0] + "..."
        self._engine.stop()  # type: ignore[union-attr]
        self._engine.say(snippet)  # type: ignore[union-attr]

    def _select_voice(self, engine: Any) -> None:
        """Prefer a female voice when the backend exposes one."""
        try:
            from PySide6.QtTextToSpeech import QVoice
        except Exception:
            return
        voices = list(engine.availableVoices())
        if not voices:
            return
        preferred = [v for v in voices if v.gender() == QVoice.Gender.Female]
        if not preferred:
            preferred = [
                v
                for v in voices
                if "female" in (v.name() or "").lower() or "woman" in (v.name() or "").lower()
            ]
        choice = preferred[0] if preferred else voices[0]
        with contextlib.suppress(Exception):
            engine.setVoice(choice)
        with contextlib.suppress(Exception):
            # Stick near natural defaults; some Linux engines get metallic above 0.
            engine.setPitch(0.0)
        with contextlib.suppress(Exception):
            engine.setRate(-0.05)


class WhisperTranscriber:
    """Optional local microphone capture + transcription using faster-whisper."""

    def __init__(self, *, model_size: str = "base") -> None:
        self._model_size = model_size
        self._model: Any | None = None
        self._deps_checked = False
        self._deps_error: str | None = None
        self._device = os.environ.get("TVASSIST_WHISPER_DEVICE", "cpu").strip() or "cpu"
        self._compute_type = (
            os.environ.get("TVASSIST_WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
        )

    def _check_deps(self) -> None:
        if self._deps_checked:
            return
        self._deps_checked = True
        try:
            import sounddevice  # noqa: F401
            import soundfile  # noqa: F401
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception as exc:
            self._deps_error = (
                "Speech input needs optional packages. Install with "
                "`pip install -e '.[assistant]'`. "
                f"Original error: {exc}"
            )

    @property
    def available(self) -> bool:
        self._check_deps()
        return self._deps_error is None

    @property
    def unavailable_reason(self) -> str:
        self._check_deps()
        return self._deps_error or ""

    def _load_model(self) -> Any:
        self._check_deps()
        if self._deps_error is not None:
            raise RuntimeError(self._deps_error)
        if self._model is None:
            from faster_whisper import WhisperModel

            try:
                self._model = WhisperModel(
                    self._model_size,
                    device=self._device,
                    compute_type=self._compute_type,
                )
            except Exception as exc:
                # Common on Linux desktops without CUDA runtime libs.
                if self._device != "cpu" and _looks_like_cuda_runtime_issue(exc):
                    self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
                else:
                    raise RuntimeError(
                        "Failed to load speech model "
                        f"'{self._model_size}' ({self._device}/{self._compute_type}): {exc}"
                    ) from exc
        return self._model

    def record_and_transcribe(
        self,
        *,
        duration_s: int = 6,
        sample_rate: int = 16_000,
        language: str = "en",
    ) -> str:
        """Capture microphone audio and return recognized text."""
        if duration_s < 1:
            raise ValueError("duration_s must be at least 1 second.")
        if sample_rate < 8000:
            raise ValueError("sample_rate is too low.")
        self._check_deps()
        if self._deps_error is not None:
            raise RuntimeError(self._deps_error)

        import sounddevice as sd
        import soundfile as sf

        model = self._load_model()
        frames = int(duration_s * sample_rate)
        audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32")
        sd.wait()

        with TemporaryDirectory(prefix="tvassistant-stt-") as tmpdir:
            wav_path = Path(tmpdir) / "recording.wav"
            sf.write(str(wav_path), audio, sample_rate)
            segments, _info = model.transcribe(
                str(wav_path),
                language=language,
                vad_filter=True,
                beam_size=4,
                best_of=2,
                condition_on_previous_text=False,
            )
            text = " ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ).strip()
        return text


def stt_model_from_env(default: str = "base") -> str:
    return os.environ.get("TVASSIST_WHISPER_MODEL", default).strip() or default
