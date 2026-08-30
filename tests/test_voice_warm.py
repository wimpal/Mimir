"""Tests for voice warm-start wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from brain.config import Settings
from brain.main import create_app
from brain.ollama import ChatMessage, ChatResponse
from brain.voice.service import VoiceService
from brain.voice.stt import SttResult


class WarmStt:
    warmed = False

    def warm(self) -> None:
        self.warmed = True

    @property
    def is_warmed(self) -> bool:
        return self.warmed

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        language_hint: str | None,
        max_duration_s: float,
    ) -> SttResult:
        return SttResult(text="hi", language="nl")


class WarmTts:
    def __init__(self) -> None:
        self._warmed: set[str] = set()

    def warm(self, locale: str) -> None:
        self._warmed.add(locale)

    def is_warmed(self, locale: str) -> bool:
        return locale in self._warmed

    def synthesize(self, text: str, *, locale: str) -> bytes:
        return b"RIFF" + b"\x00" * 40


class ScriptOllama:
    def chat(self, *args: Any, **kwargs: Any) -> ChatResponse:
        return ChatResponse(message=ChatMessage(role="assistant", content="ok"))

    def ping(self, *, timeout_s: float = 5.0) -> bool:
        return True

    def close(self) -> None:
        return None


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        ollama={"url": "http://test", "model": "qwen3:8b", "keep_alive": "45m"},
        runtime={"data_dir": data_dir, "log_level": "WARNING", "host": "127.0.0.1"},
        voice={"enabled": True, "warm_on_start": True},
    )


def test_voice_service_warm(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    stt = WarmStt()
    tts = WarmTts()
    svc = VoiceService(s, data_dir=s.runtime.data_dir, stt=stt, tts=tts)
    assert not svc.is_warmed()
    svc.warm()
    assert stt.warmed
    assert svc.is_warmed()


def test_health_reports_warmed(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    app = create_app(s, client=ScriptOllama(), system_prompt="test", prompt_id="test:warm")
    stt = WarmStt()
    tts = WarmTts()
    app.state.voice_service = VoiceService(s, data_dir=s.runtime.data_dir, stt=stt, tts=tts)
    svc: VoiceService = app.state.voice_service
    svc.warm()
    with TestClient(app) as tc:
        resp = tc.get("/health")
        voice = resp.json()["voice"]
        assert voice["warmed"] is True
