"""Phase 7 auth / bind / Host-only tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.config import ConfigError, Settings, is_loopback_host, validate_bind_auth
from brain.main import create_app
from brain.ollama import ChatMessage, ChatResponse
from brain.turn_log import turns_log_path


class ScriptedOllama:
    def __init__(self, responses: list[ChatMessage] | None = None, *, ping_ok: bool = True) -> None:
        self._responses = list(responses or [])
        self.ping_ok = ping_ok

    def chat(self, *args: Any, **kwargs: Any) -> ChatResponse:
        if not self._responses:
            return ChatResponse(message=ChatMessage(role="assistant", content="ok"))
        return ChatResponse(message=self._responses.pop(0))

    def ping(self, *, timeout_s: float = 5.0) -> bool:
        return self.ping_ok

    def close(self) -> None:
        return None


def _settings(tmp_path: Path, **kwargs: Any) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    base: dict[str, Any] = {
        "location": {"latitude": 1.0, "longitude": 2.0},
        "ollama": {"url": "http://test", "model": "qwen3:8b"},
        "runtime": {"data_dir": data_dir, "log_level": "WARNING", "host": "127.0.0.1"},
        "agent": {"max_iterations": 3},
        "timeouts": {"ollama_s": 30, "tool_s": 5, "turn_s": 60},
    }
    for key, value in kwargs.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return Settings(**base)


def _client(settings: Settings) -> TestClient:
    app = create_app(
        settings,
        client=ScriptedOllama(),  # type: ignore[arg-type]
        system_prompt="You are Mimir.",
        prompt_id="test:prompt",
        data_dir=settings.runtime.data_dir,
    )
    return TestClient(app)


def test_is_loopback_host() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.5")


def test_validate_bind_auth_allows_loopback_without_token(tmp_path: Path) -> None:
    validate_bind_auth(_settings(tmp_path))


def test_validate_bind_auth_rejects_non_loopback_without_token(tmp_path: Path) -> None:
    s = _settings(tmp_path, runtime={"host": "0.0.0.0"})
    with pytest.raises(ConfigError, match="not loopback"):
        validate_bind_auth(s)


def test_validate_bind_auth_requires_token_when_mode_token(tmp_path: Path) -> None:
    s = _settings(tmp_path, auth={"mode": "token", "token": None})
    with pytest.raises(ConfigError, match="MIMIR_AUTH_TOKEN"):
        validate_bind_auth(s)


def test_validate_bind_auth_allows_non_loopback_with_token(tmp_path: Path) -> None:
    s = _settings(
        tmp_path,
        runtime={"host": "0.0.0.0"},
        auth={"mode": "token", "token": "secret"},
    )
    validate_bind_auth(s)


def test_create_app_refuses_insecure_bind(tmp_path: Path) -> None:
    s = _settings(tmp_path, runtime={"host": "0.0.0.0"})
    with pytest.raises(ConfigError, match="not loopback"):
        create_app(
            s,
            client=ScriptedOllama(),  # type: ignore[arg-type]
            data_dir=s.runtime.data_dir,
        )


def test_health_open_when_token_required(tmp_path: Path) -> None:
    s = _settings(tmp_path, auth={"mode": "token", "token": "sekrit"})
    with _client(s) as tc:
        resp = tc.get("/health")
    assert resp.status_code == 200


def test_chat_requires_bearer_when_token_mode(tmp_path: Path) -> None:
    s = _settings(tmp_path, auth={"mode": "token", "token": "sekrit"})
    with _client(s) as tc:
        denied = tc.post("/v1/chat", json={"message": "hi"})
        assert denied.status_code == 401
        ok = tc.post(
            "/v1/chat",
            json={"message": "hi"},
            headers={"Authorization": "Bearer sekrit"},
        )
        assert ok.status_code == 200
        assert ok.json()["reply"]


def test_sync_host_only_rejects_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brain.auth as auth_mod

    monkeypatch.setattr(auth_mod, "is_loopback_client", lambda _req: False)
    s = _settings(tmp_path)
    with _client(s) as tc:
        resp = tc.post("/v1/jellyfin/sync")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "host-only"


def test_debug_recent_traces(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    log = turns_log_path(s.runtime.data_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '{"ts":"t1","turn_id":"a","prompt_id":"p","conversation_id":null,'
        '"stopped_reason":"final","success":true,"error":null,"tools_used":["echo"],'
        '"steps":[{"ollama_latency_ms":10,"tool_latency_ms":1,"tool_names":["echo"],'
        '"success":true,"anomaly":null}],"secret_field":"nope"}\n',
        encoding="utf-8",
    )
    with _client(s) as tc:
        resp = tc.get("/debug/recent-traces?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    trace = body["traces"][0]
    assert trace["turn_id"] == "a"
    assert trace["tools_used"] == ["echo"]
    assert "secret_field" not in trace
    assert "error" not in trace
    assert trace["ollama_latency_ms_sum"] == 10.0


def test_debug_host_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import brain.auth as auth_mod

    monkeypatch.setattr(auth_mod, "is_loopback_client", lambda _req: False)
    s = _settings(tmp_path)
    with _client(s) as tc:
        resp = tc.get("/debug/recent-traces")
    assert resp.status_code == 403
