"""VoiceService — validates limits and delegates to STT/TTS engines."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from brain.config import Settings, resolve_voice_path
from brain.voice.errors import VoiceError
from brain.voice.log import append_voice_log
from brain.voice.stt import FasterWhisperEngine, SttEngine, SttResult, ffmpeg_available
from brain.voice.tts import Locale, PiperEngine, TtsEngine

logger = logging.getLogger("mimir.voice")

LocaleHint = Literal["nl", "en"]


@dataclass(frozen=True)
class VoiceHealth:
    enabled: bool
    stt: str
    tts: str
    ffmpeg: str
    warmed: bool = False


class VoiceService:
    def __init__(
        self,
        settings: Settings,
        *,
        data_dir: Path,
        stt: SttEngine | None = None,
        tts: TtsEngine | None = None,
    ) -> None:
        self.settings = settings
        self.data_dir = data_dir
        self._enabled = settings.voice.enabled
        if stt is not None:
            self._stt = stt
        else:
            self._stt = FasterWhisperEngine(settings.voice.stt)
        if tts is not None:
            self._tts = tts
        else:
            paths = {
                loc: resolve_voice_path(path, data_dir=data_dir)
                for loc, path in settings.voice.tts.voices.items()
            }
            self._tts = PiperEngine.from_settings(paths, settings.voice.tts)
        self._warmed = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def is_warmed(self) -> bool:
        if not self._enabled:
            return False
        stt_ok = getattr(self._stt, "is_warmed", False)
        tts_ok = all(
            getattr(self._tts, "is_warmed", lambda _loc: False)(loc)
            for loc in ("nl", "en")
        )
        return bool(self._warmed and stt_ok and tts_ok)

    def health_status(self) -> VoiceHealth:
        if not self._enabled:
            return VoiceHealth(enabled=False, stt="disabled", tts="disabled", ffmpeg="n/a")
        ffmpeg = "ok" if ffmpeg_available() else "missing"
        stt = "ok" if ffmpeg == "ok" else "fail"
        tts = "ok"
        if isinstance(self._tts, PiperEngine):
            tts = (
                "ok"
                if all(self._tts.voice_file_exists(loc) for loc in ("nl", "en"))
                else "fail"
            )
        return VoiceHealth(
            enabled=True,
            stt=stt,
            tts=tts,
            ffmpeg=ffmpeg,
            warmed=self.is_warmed(),
        )

    def warm(self) -> None:
        """Load STT/TTS models. Non-fatal on partial failure."""
        if not self._enabled:
            return
        if not ffmpeg_available():
            logger.warning("voice warm skipped: ffmpeg missing")
            return
        try:
            if hasattr(self._stt, "warm"):
                self._stt.warm()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            logger.warning("STT warm failed", exc_info=True)
        for loc in ("nl", "en"):
            try:
                if hasattr(self._tts, "warm"):
                    self._tts.warm(loc)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                logger.warning("TTS warm failed locale=%s", loc, exc_info=True)
        self._warmed = True

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        language_hint: LocaleHint | None = None,
    ) -> SttResult:
        if not self._enabled:
            raise VoiceError(
                code="unavailable",
                message="Speech recognition is disabled.",
                retryable=False,
                http_status=503,
            )
        limits = self.settings.voice.limits
        if len(audio) > limits.max_audio_bytes:
            raise VoiceError(
                code="invalid_input",
                message="Audio file is too large.",
                retryable=False,
                http_status=413,
            )
        if not ffmpeg_available():
            raise VoiceError(
                code="unavailable",
                message="Speech recognition requires ffmpeg on PATH.",
                retryable=True,
                http_status=503,
            )

        hint = language_hint or self.settings.voice.stt.language_hint
        started = time.monotonic()
        ok = False
        err_msg: str | None = None
        result: SttResult | None = None
        try:
            result = self._stt.transcribe(
                audio,
                content_type=content_type,
                language_hint=hint,
                max_duration_s=limits.max_audio_duration_s,
            )
            ok = True
            return result
        except VoiceError as exc:
            err_msg = exc.message
            raise
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)
            append_voice_log(
                self.data_dir,
                {
                    "op": "stt",
                    "ok": ok,
                    "latency_ms": latency_ms,
                    "bytes": len(audio),
                    "content_type": content_type,
                    "language": result.language if result else None,
                    "error": err_msg,
                },
            )

    def synthesize(self, text: str, *, locale: Locale | None = None) -> bytes:
        if not self._enabled:
            raise VoiceError(
                code="unavailable",
                message="Speech synthesis is disabled.",
                retryable=False,
                http_status=503,
            )
        limits = self.settings.voice.limits
        if len(text) > limits.max_tts_chars:
            raise VoiceError(
                code="invalid_input",
                message=f"Text is too long (max {limits.max_tts_chars} characters).",
                retryable=False,
                http_status=400,
            )
        loc: Locale = locale or self.settings.voice.tts.default_locale
        if loc not in ("nl", "en"):
            raise VoiceError(
                code="invalid_input",
                message="locale must be 'nl' or 'en'.",
                retryable=False,
                http_status=400,
            )

        started = time.monotonic()
        ok = False
        err_msg: str | None = None
        out_len = 0
        try:
            wav = self._tts.synthesize(text, locale=loc)
            ok = True
            out_len = len(wav)
            return wav
        except VoiceError as exc:
            err_msg = exc.message
            raise
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)
            append_voice_log(
                self.data_dir,
                {
                    "op": "tts",
                    "ok": ok,
                    "latency_ms": latency_ms,
                    "locale": loc,
                    "text_chars": len(text.strip()),
                    "audio_bytes": out_len,
                    "error": err_msg,
                },
            )
