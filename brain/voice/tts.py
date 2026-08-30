"""Text-to-speech via Piper."""

from __future__ import annotations

import io
import logging
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import numpy as np

from brain.config import VoiceTtsSettings
from brain.voice.errors import VoiceError
from brain.voice.text import Locale, prepare_text_for_speech

logger = logging.getLogger("mimir.voice.tts")

_MAX_WAV_VALUE = 32767.0


@dataclass(frozen=True)
class TtsSynthesisSettings:
    length_scale: float
    noise_scale: float
    noise_w_scale: float
    volume: float
    sentence_silence_s: float
    normalize: Literal["peak", "rms", "none"]
    fade_ms: float


class TtsEngine(Protocol):
    def synthesize(self, text: str, *, locale: Locale) -> bytes: ...

    def warm(self, locale: Locale) -> None: ...


def _silence_samples(sample_rate: int, duration_s: float) -> np.ndarray:
    count = max(0, int(sample_rate * duration_s))
    return np.zeros(count, dtype=np.float32)


def _apply_fade(audio: np.ndarray, *, sample_rate: int, fade_ms: float) -> np.ndarray:
    if fade_ms <= 0 or audio.size == 0:
        return audio
    fade_samples = min(int(sample_rate * fade_ms / 1000.0), audio.size // 2)
    if fade_samples <= 0:
        return audio
    out = audio.copy()
    ramp = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    out[:fade_samples] *= ramp
    out[-fade_samples:] *= ramp[::-1]
    return out


def _normalize_audio(
    audio: np.ndarray,
    *,
    mode: Literal["peak", "rms", "none"],
    target_peak: float = 0.95,
    target_rms: float = 0.12,
) -> np.ndarray:
    if mode == "none" or audio.size == 0:
        return audio
    if mode == "peak":
        peak = float(np.max(np.abs(audio)))
        if peak < 1e-8:
            return audio
        return audio * (target_peak / peak)
    rms = float(np.sqrt(np.mean(np.square(audio))))
    if rms < 1e-8:
        return audio
    scaled = audio * (target_rms / rms)
    peak = float(np.max(np.abs(scaled)))
    if peak > 1.0:
        scaled = scaled / peak
    return scaled


def _assemble_wav(chunks: list[np.ndarray], *, sample_rate: int) -> bytes:
    if not chunks:
        chunks = [np.zeros(0, dtype=np.float32)]
    merged = np.concatenate(chunks)
    merged = np.clip(merged, -1.0, 1.0)
    pcm = (merged * _MAX_WAV_VALUE).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())
    return buf.getvalue()


class PiperEngine:
    """Lazy-loaded Piper voices per locale."""

    def __init__(
        self,
        voice_paths: dict[str, Path],
        *,
        synthesis: TtsSynthesisSettings | None = None,
    ) -> None:
        self._voice_paths = voice_paths
        self._synthesis = synthesis or TtsSynthesisSettings(
            length_scale=1.05,
            noise_scale=0.667,
            noise_w_scale=0.75,
            volume=1.0,
            sentence_silence_s=0.14,
            normalize="peak",
            fade_ms=5.0,
        )
        self._voices: dict[str, object] = {}
        self._lock = threading.Lock()
        self._warmed: set[str] = set()

    @classmethod
    def from_settings(
        cls,
        voice_paths: dict[str, Path],
        settings: VoiceTtsSettings,
    ) -> PiperEngine:
        return cls(
            voice_paths,
            synthesis=TtsSynthesisSettings(
                length_scale=settings.length_scale,
                noise_scale=settings.noise_scale,
                noise_w_scale=settings.noise_w_scale,
                volume=settings.volume,
                sentence_silence_s=settings.sentence_silence_s,
                normalize=settings.normalize,
                fade_ms=settings.fade_ms,
            ),
        )

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
                from piper.config import SynthesisConfig
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
            self._syn_config = SynthesisConfig(
                length_scale=self._synthesis.length_scale,
                noise_scale=self._synthesis.noise_scale,
                noise_w_scale=self._synthesis.noise_w_scale,
                volume=self._synthesis.volume,
                normalize_audio=False,
            )
            return voice

    def _synthesize_audio(self, text: str, *, locale: Locale) -> tuple[bytes, float]:
        voice = self._load_voice(locale)
        sample_rate = voice.config.sample_rate  # type: ignore[attr-defined]
        audio_parts: list[np.ndarray] = []
        silence = _silence_samples(sample_rate, self._synthesis.sentence_silence_s)

        for idx, chunk in enumerate(voice.synthesize(text, syn_config=self._syn_config)):  # type: ignore[attr-defined]
            part = chunk.audio_float_array.astype(np.float32)
            part = _apply_fade(
                part,
                sample_rate=sample_rate,
                fade_ms=self._synthesis.fade_ms,
            )
            if idx > 0 and silence.size:
                audio_parts.append(silence.copy())
            audio_parts.append(part)

        merged = (
            np.concatenate(audio_parts)
            if audio_parts
            else np.zeros(0, dtype=np.float32)
        )
        merged = _normalize_audio(merged, mode=self._synthesis.normalize)
        if self._synthesis.volume != 1.0:
            merged = merged * self._synthesis.volume
        merged = np.clip(merged, -1.0, 1.0)

        duration_s = merged.size / sample_rate if sample_rate else 0.0
        pcm = (merged * _MAX_WAV_VALUE).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())
        return buf.getvalue(), duration_s

    def synthesize(self, text: str, *, locale: Locale) -> bytes:
        cleaned = prepare_text_for_speech(text, locale=locale)
        if not cleaned:
            raise VoiceError(
                code="invalid_input",
                message="Text is required for speech synthesis.",
                retryable=False,
                http_status=400,
            )
        wav, _duration = self._synthesize_audio(cleaned, locale=locale)
        return wav

    def warm(self, locale: Locale) -> None:
        if locale in self._warmed:
            return
        phrase = "Hallo." if locale == "nl" else "Hello."
        self._synthesize_audio(phrase, locale=locale)
        self._warmed.add(locale)

    def is_warmed(self, locale: Locale) -> bool:
        return locale in self._warmed

    def voice_file_exists(self, locale: Locale) -> bool:
        path = self._voice_paths.get(locale)
        if path is None:
            return False
        return path.is_file() and Path(f"{path}.json").is_file()
