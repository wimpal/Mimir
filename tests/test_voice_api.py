"""Tests for /v1/stt and /v1/tts — mocked engines, no live models."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.config import Settings
from brain.main import create_app
from brain.ollama import ChatMessage, ChatResponse
from brain.voice.errors import VoiceError
from brain.voice.service import VoiceService
from brain.voice.stt import SttResult
from brain.voice.tts import Locale


class ScriptedOllama:
    def __init__(self, *, ping_ok: bool = True) -> None:
        self.ping_ok = ping_ok

    def chat(self, *args: Any, **kwargs: Any) -> ChatResponse:
        return ChatResponse(message=ChatMessage(role="assistant", content="ok"))

    def ping(self, *, timeout_s: float = 5.0) -> bool:
        return self.ping_ok

    def close(self) -> None:
        return None


class MockStt:
    def __init__(self, result: SttResult | None = None, *, error: VoiceError | None = None) -> None:
        self.result = result or SttResult(text="hello world", language="en")
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def transcribe(
        self,
        audio: bytes,
        *,
        content_type: str,
        language_hint: str | None,
        max_duration_s: float,
    ) -> SttResult:
        self.calls.append(
            {
                "len": len(audio),
                "content_type": content_type,
                "language_hint": language_hint,
                "max_duration_s": max_duration_s,
            }
        )
        if self.error is not None:
            raise self.error
        if not audio:
            raise VoiceError(
                code="invalid_input",
                message="I couldn't make out any speech.",
                retryable=False,
                http_status=400,
            )
        return self.result


class MockTts:
    def __init__(self, wav: bytes | None = None, *, error: VoiceError | None = None) -> None:
        self.wav = wav or _minimal_wav()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def synthesize(self, text: str, *, locale: Locale) -> bytes:
        self.calls.append({"text": text, "locale": locale})
        if self.error is not None:
            raise self.error
        if not (text or "").strip():
            raise VoiceError(
                code="invalid_input",
                message="Text is required for speech synthesis.",
                retryable=False,
                http_status=400,
            )
        return self.wav


def _minimal_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 160)
    return buf.getvalue()


def _settings(tmp_path: Path, **kwargs: Any) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    base: dict[str, Any] = {
        "location": {"latitude": 1.0, "longitude": 2.0},
        "ollama": {"url": "http://test", "model": "qwen3:8b"},
        "runtime": {"data_dir": data_dir, "log_level": "WARNING", "host": "127.0.0.1"},
        "agent": {"max_iterations": 3},
        "timeouts": {"ollama_s": 30, "tool_s": 5, "turn_s": 60, "stt_s": 60, "tts_s": 30},
        "voice": {
            "limits": {"max_audio_bytes": 1024, "max_audio_duration_s": 30, "max_tts_chars": 2000},
        },
    }
    for key, value in kwargs.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return Settings(**base)


def _client(
    settings: Settings,
    *,
    stt: MockStt | None = None,
    tts: MockTts | None = None,
) -> TestClient:
    app = create_app(
        settings,
        client=ScriptedOllama(),  # type: ignore[arg-type]
        system_prompt="test",
        prompt_id="test:voice",
    )
    app.state.voice_service = VoiceService(
        settings,
        data_dir=settings.runtime.data_dir,
        stt=stt or MockStt(),
        tts=tts or MockTts(),
    )
    return TestClient(app)


def _tiny_wav() -> bytes:
    return _minimal_wav()


def test_stt_requires_bearer_when_token_mode(tmp_path: Path) -> None:
    s = _settings(tmp_path, auth={"mode": "token", "token": "sekrit"})
    with _client(s) as tc:
        denied = tc.post(
            "/v1/stt",
            content=_tiny_wav(),
            headers={"Content-Type": "audio/wav"},
        )
        assert denied.status_code == 401
        ok = tc.post(
            "/v1/stt",
            content=_tiny_wav(),
            headers={
                "Content-Type": "audio/wav",
                "Authorization": "Bearer sekrit",
            },
        )
        assert ok.status_code == 200
        assert ok.json()["text"] == "hello world"


def test_tts_requires_bearer_when_token_mode(tmp_path: Path) -> None:
    s = _settings(tmp_path, auth={"mode": "token", "token": "sekrit"})
    with _client(s) as tc:
        denied = tc.post("/v1/tts", json={"text": "hi"})
        assert denied.status_code == 401
        ok = tc.post(
            "/v1/tts",
            json={"text": "hi", "locale": "nl"},
            headers={"Authorization": "Bearer sekrit"},
        )
        assert ok.status_code == 200
        assert ok.headers["content-type"].startswith("audio/wav")


def test_stt_happy_path(tmp_path: Path) -> None:
    stt = MockStt(SttResult(text="wat is het weer", language="nl"))
    with _client(_settings(tmp_path), stt=stt) as tc:
        resp = tc.post(
            "/v1/stt",
            content=_tiny_wav(),
            headers={"Content-Type": "audio/wav"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == "wat is het weer"
        assert body["language"] == "nl"
        assert stt.calls[0]["content_type"] == "audio/wav"


def test_stt_empty_audio(tmp_path: Path) -> None:
    stt = MockStt(error=VoiceError("invalid_input", "I couldn't make out any speech.", http_status=400))
    with _client(_settings(tmp_path), stt=stt) as tc:
        resp = tc.post(
            "/v1/stt",
            content=b"",
            headers={"Content-Type": "audio/wav"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_input"


def test_stt_payload_too_large(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        voice={"limits": {"max_audio_bytes": 10, "max_audio_duration_s": 30, "max_tts_chars": 2000}},
    )
    with _client(s) as tc:
        resp = tc.post(
            "/v1/stt",
            content=b"x" * 20,
            headers={"Content-Type": "audio/wav"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "invalid_input"


def test_tts_happy_path(tmp_path: Path) -> None:
    tts = MockTts()
    with _client(_settings(tmp_path), tts=tts) as tc:
        resp = tc.post("/v1/tts", json={"text": "Morgen 18 graden.", "locale": "nl"})
        assert resp.status_code == 200
        assert resp.content.startswith(b"RIFF")
        assert tts.calls[0]["locale"] == "nl"


def test_tts_empty_text(tmp_path: Path) -> None:
    tts = MockTts(
        error=VoiceError(
            "invalid_input",
            "Text is required for speech synthesis.",
            http_status=400,
        )
    )
    with _client(_settings(tmp_path), tts=tts) as tc:
        resp = tc.post("/v1/tts", json={"text": "   "})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_input"


def test_health_includes_voice(tmp_path: Path) -> None:
    with _client(_settings(tmp_path)) as tc:
        resp = tc.get("/health")
        assert resp.status_code == 200
        voice = resp.json().get("voice")
        assert voice is not None
        assert voice["enabled"] is True
        assert "stt" in voice
        assert "tts" in voice
