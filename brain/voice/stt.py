"""Speech-to-text via faster-whisper."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from brain.config import VoiceSttSettings
from brain.voice.errors import VoiceError

logger = logging.getLogger("mimir.voice.stt")

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp3",
    }
)

_LANGUAGE_MAP = {"nl": "nl", "en": "en"}


@dataclass(frozen=True)
class SttResult:
    text: str
    language: str


class SttEngine(Protocol):
    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        language_hint: str | None,
        max_duration_s: float,
    ) -> SttResult: ...


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe_duration_s(path: Path) -> float | None:
    if not ffmpeg_available():
        return None
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return float(proc.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _suffix_for_content_type(content_type: str) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in ("audio/wav", "audio/x-wav"):
        return ".wav"
    if ct == "audio/webm":
        return ".webm"
    if ct == "audio/ogg":
        return ".ogg"
    if ct in ("audio/mpeg", "audio/mp3"):
        return ".mp3"
    return ".bin"


class FasterWhisperEngine:
    """Lazy-loaded faster-whisper model."""

    def __init__(self, settings: VoiceSttSettings) -> None:
        self._settings = settings
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise VoiceError(
                    code="unavailable",
                    message="Speech recognition is not installed.",
                    retryable=True,
                    http_status=503,
                ) from exc
            device: Literal["cpu", "cuda"] = self._settings.device
            try:
                self._model = WhisperModel(
                    self._settings.model,
                    device=device,
                    compute_type=self._settings.compute_type,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("failed to load faster-whisper model")
                raise VoiceError(
                    code="unavailable",
                    message="Speech recognition failed to start.",
                    retryable=True,
                    http_status=503,
                ) from exc

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        language_hint: str | None,
        max_duration_s: float,
    ) -> SttResult:
        ct = (content_type or "").split(";", 1)[0].strip().lower()
        if ct not in ALLOWED_CONTENT_TYPES:
            raise VoiceError(
                code="invalid_input",
                message=f"Unsupported audio type '{ct or 'missing'}'.",
                retryable=False,
                http_status=400,
            )
        if not audio:
            raise VoiceError(
                code="invalid_input",
                message="I couldn't make out any speech.",
                retryable=False,
                http_status=400,
            )

        suffix = _suffix_for_content_type(ct)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio)
            tmp_path = Path(tmp.name)

        try:
            duration = _probe_duration_s(tmp_path)
            if duration is not None and duration > max_duration_s:
                raise VoiceError(
                    code="invalid_input",
                    message=f"Audio is too long ({duration:.0f}s; max {max_duration_s:.0f}s).",
                    retryable=False,
                    http_status=400,
                )

            self._ensure_model()
            lang = _LANGUAGE_MAP.get(language_hint or "", language_hint)
            segments, info = self._model.transcribe(  # type: ignore[union-attr]
                str(tmp_path),
                beam_size=1,
                vad_filter=True,
                language=lang,
            )
            parts = [seg.text.strip() for seg in segments if seg.text.strip()]
            text = " ".join(parts).strip()
            detected = getattr(info, "language", None) or lang or "unknown"
            if not text:
                raise VoiceError(
                    code="invalid_input",
                    message="I couldn't make out any speech.",
                    retryable=False,
                    http_status=400,
                )
            return SttResult(text=text, language=str(detected))
        finally:
            tmp_path.unlink(missing_ok=True)
