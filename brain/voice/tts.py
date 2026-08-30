"""Text-to-speech via Piper."""

from __future__ import annotations

import io
import logging
import threading
import wave
from pathlib import Path
from typing import Literal, Protocol

from brain.voice.errors import VoiceError

logger = logging.getLogger("mimir.voice.tts")

Locale = Literal["nl", "en"]


class TtsEngine(Protocol):
    def synthesize(self, text: str, *, locale: Locale) -> bytes: ...


class PiperEngine:
    """Lazy-loaded Piper voices per locale."""

    def __init__(self, voice_paths: dict[str, Path]) -> None:
        self._voice_paths = voice_paths
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()

    def _load_voice(self, locale: Locale) -> object:
        if locale in self._voices:
            return self._voices[locale]
        with self._lock:
            if locale in self._voices:
                return self._voices[locale]
            path = self._voice_paths.get(locale)
            if path is None or not path.is_file():
                raise VoiceError(
                    code="unavailable",
                    message=(
                        f"Voice for '{locale}' is not available. "
                        "Run scripts/download_voice_models.ps1."
                    ),
                    retryable=True,
                    http_status=503,
                )
            config_path = Path(f"{path}.json")
            if not config_path.is_file():
                raise VoiceError(
                    code="unavailable",
                    message=(
                        f"Voice config for '{locale}' is missing ({config_path.name}). "
                        "Run scripts/download_voice_models.ps1."
                    ),
                    retryable=True,
                    http_status=503,
                )
            try:
                from piper.voice import PiperVoice
            except ImportError as exc:
                raise VoiceError(
                    code="unavailable",
                    message="Speech synthesis is not installed.",
                    retryable=True,
                    http_status=503,
                ) from exc
            try:
                voice = PiperVoice.load(path, config_path=config_path)
            except Exception as exc:  # noqa: BLE001
                logger.exception("failed to load piper voice locale=%s path=%s", locale, path)
                raise VoiceError(
                    code="unavailable",
                    message="Speech synthesis failed to start.",
                    retryable=True,
                    http_status=503,
                ) from exc
            self._voices[locale] = voice
            return voice

    def synthesize(self, text: str, *, locale: Locale) -> bytes:
        cleaned = (text or "").strip()
        if not cleaned:
            raise VoiceError(
                code="invalid_input",
                message="Text is required for speech synthesis.",
                retryable=False,
                http_status=400,
            )
        voice = self._load_voice(locale)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(cleaned, wav_file)  # type: ignore[attr-defined]
        return buf.getvalue()

    def voice_file_exists(self, locale: Locale) -> bool:
        path = self._voice_paths.get(locale)
        if path is None:
            return False
        return path.is_file() and Path(f"{path}.json").is_file()
