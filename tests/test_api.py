"""API tests for Phase 2 FastAPI brain — mocked Ollama, no live server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.agent import run_turn
from brain.config import Settings
from brain.db import SCHEMA_VERSION, Database
from brain.main import create_app
from brain.ollama import (
    ChatMessage,
    ChatResponse,
    OllamaError,
    ToolCall,
    ToolCallFunction,
)
from brain.prompt import load_system_prompt
from brain.service import MSG_OLLAMA_DOWN, MSG_TURN_TIMEOUT
from brain.turn_log import turns_log_path


class ScriptedOllama:
    """Duck-types OllamaClient for the brain service."""

    def __init__(self, responses: list[ChatMessage | Exception], *, ping_ok: bool = True) -> None:
        self._responses = list(responses)
        self.ping_ok = ping_ok
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        think: bool = False,
        stream: bool = False,
    ) -> ChatResponse:
        self.calls.append({"messages": list(messages), "tools": tools, "think": think})
        if not self._responses:
            raise AssertionError("no scripted responses left")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return ChatResponse(message=nxt)

    def ping(self, *, timeout_s: float = 5.0) -> bool:
        return self.ping_ok

    def close(self) -> None:
        return None


def _tool_call(name: str, arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(function=ToolCallFunction(name=name, arguments=arguments or {}))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        ollama={"url": "http://test", "model": "qwen3:8b"},
        runtime={"data_dir": data_dir, "log_level": "WARNING"},
        agent={"max_iterations": 3},
        timeouts={"ollama_s": 30, "tool_s": 5, "turn_s": 60},
    )


def _client(settings: Settings, ollama: ScriptedOllama) -> TestClient:
    app = create_app(
        settings,
        client=ollama,  # type: ignore[arg-type]
        system_prompt="You are Mimir.",
        prompt_id="test:prompt",
        data_dir=settings.runtime.data_dir,
    )
    return TestClient(app)


def test_health_ok(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([], ping_ok=True)) as tc:
        resp = tc.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "mimir"
    assert body["status"] == "ok"
    assert body["version"]
    assert body["checks"]["db"] == "ok"
    assert body["ollama"]["reachable"] is True
    assert body["db"]["ok"] is True
    assert body["db"]["schema_version"] == SCHEMA_VERSION
    assert "jellyfin_sync" in body
    assert body["jellyfin_sync"]["configured"] is False
    assert body["jellyfin_sync"]["movie_count"] == 0


def test_jellyfin_sync_not_configured(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([], ping_ok=True)) as tc:
        resp = tc.post("/v1/jellyfin/sync")
    assert resp.status_code == 503
    assert resp.json()["configured"] is False


def test_jellyfin_sync_ok(tmp_path: Path) -> None:
    import httpx

    from brain.jellyfin_client import JellyfinClient

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        ollama={"url": "http://test", "model": "qwen3:8b"},
        jellyfin={
            "url": "http://jellyfin.test",
            "api_key": "k",
            "user_id": "u1",
            "library_ids": ["lib"],
            "sync_interval_hours": 0,
        },
        runtime={"data_dir": data_dir, "log_level": "WARNING"},
        timeouts={"jellyfin_sync_s": 30, "tool_s": 5, "turn_s": 60, "ollama_s": 30},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("IncludeItemTypes") == "BoxSet":
            return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})
        return httpx.Response(
            200,
            json={
                "Items": [
                    {
                        "Id": "1",
                        "Name": "Synced",
                        "Genres": ["Drama"],
                        "UserData": {"Played": False, "PlaybackPositionTicks": 0},
                    }
                ],
                "TotalRecordCount": 1,
            },
        )

    ollama = ScriptedOllama([], ping_ok=True)
    app = create_app(
        settings,
        client=ollama,  # type: ignore[arg-type]
        system_prompt="You are Mimir.",
        prompt_id="test:prompt",
        data_dir=data_dir,
    )
    app.state.sync_manager._client = JellyfinClient(
        "http://jellyfin.test",
        "k",
        user_id="u1",
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://jellyfin.test/",
        ),
    )
    with TestClient(app) as tc:
        resp = tc.post("/v1/jellyfin/sync")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["state"]["movie_count"] == 1
        health = tc.get("/health").json()
        assert health["jellyfin_sync"]["movie_count"] == 1
        assert health["jellyfin_sync"]["configured"] is True


def test_health_degraded_when_ollama_down(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([], ping_ok=False)) as tc:
        resp = tc.get("/health")
    assert resp.json()["status"] == "degraded"
    assert resp.json()["ollama"]["reachable"] is False


def test_chat_tool_then_final(settings: Settings) -> None:
    ollama = ScriptedOllama(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[_tool_call("echo", {"text": "pong"})],
            ),
            ChatMessage(role="assistant", content="Tool returned pong."),
        ]
    )
    with _client(settings, ollama) as tc:
        resp = tc.post("/v1/chat", json={"message": "echo pong"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Tool returned pong."
    assert body["stopped_reason"] == "final"
    assert body["tools_used"] == ["echo"]
    assert body["turn_id"]

    log_path = turns_log_path(settings.runtime.data_dir)
    assert log_path.is_file()
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "echo" in line
    assert "test:prompt" in line


def test_chat_ollama_down_clear_message(settings: Settings) -> None:
    ollama = ScriptedOllama([OllamaError("connection refused")])
    with _client(settings, ollama) as tc:
        resp = tc.post("/v1/chat", json={"message": "hello"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == MSG_OLLAMA_DOWN
    assert resp.json()["stopped_reason"] == "ollama_error"


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        events.append(json.loads("\n".join(data_lines)))
    return events


def test_chat_stream_sse(settings: Settings) -> None:
    ollama = ScriptedOllama([ChatMessage(role="assistant", content="Hello stream")])
    with _client(settings, ollama) as tc:
        resp = tc.post("/v1/chat", json={"message": "hi", "stream": True})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        events = _parse_sse_events(resp.text)
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert "conversation_id" in events[0]
    assert "token" in types
    assert types[-1] == "done"
    text = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert text == "Hello stream"
    assert events[-1]["stopped_reason"] == "final"


def test_chat_stream_with_tools_emits_tool_events(settings: Settings) -> None:
    ollama = ScriptedOllama(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[_tool_call("echo", {"text": "ping"})],
            ),
            ChatMessage(role="assistant", content="pong"),
        ]
    )
    with _client(settings, ollama) as tc:
        resp = tc.post("/v1/chat", json={"message": "echo", "stream": True})
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
    types = [e["type"] for e in events]
    assert "tool_start" in types
    assert "tool_end" in types
    assert types[-1] == "done"
    text = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert text == "pong"


def test_conversation_messages_empty_unknown(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        resp = tc.get("/v1/conversations/does-not-exist/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == "does-not-exist"
    assert body["messages"] == []
    # GET must not create the conversation
    db = Database(settings.runtime.data_dir / "mimir.db")
    assert db.message_count("does-not-exist") == 0


def test_conversations_list_empty(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        resp = tc.get("/v1/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversations"] == []
    assert body["count"] == 0
    assert body["limit"] == 50


def test_conversations_list_shape_and_clamp(settings: Settings) -> None:
    ollama = ScriptedOllama(
        [
            ChatMessage(role="assistant", content="a"),
            ChatMessage(role="assistant", content="b"),
        ]
    )
    with _client(settings, ollama) as tc:
        r1 = tc.post("/v1/chat", json={"message": "alpha topic"})
        cid_a = r1.json()["conversation_id"]
        r2 = tc.post("/v1/chat", json={"message": "beta topic"})
        cid_b = r2.json()["conversation_id"]
        resp = tc.get("/v1/conversations?limit=1")
        all_rows = tc.get("/v1/conversations")
        clamped = tc.get("/v1/conversations?limit=999")
        over_zero = tc.get("/v1/conversations?limit=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 1
    assert body["count"] == 1
    assert len(body["conversations"]) == 1
    row = body["conversations"][0]
    assert set(row) >= {"id", "created_at", "updated_at", "preview", "message_count"}
    assert row["id"] in {cid_a, cid_b}
    assert row["preview"] in {"alpha topic", "beta topic"}
    assert row["message_count"] >= 2
    ids = {c["id"] for c in all_rows.json()["conversations"]}
    assert ids == {cid_a, cid_b}
    assert clamped.json()["limit"] == 200
    assert over_zero.json()["limit"] == 1


def test_conversation_messages_full_history(settings: Settings) -> None:
    ollama = ScriptedOllama(
        [
            ChatMessage(role="assistant", content="one"),
            ChatMessage(role="assistant", content="two"),
        ]
    )
    with _client(settings, ollama) as tc:
        r1 = tc.post("/v1/chat", json={"message": "first"})
        cid = r1.json()["conversation_id"]
        tc.post("/v1/chat", json={"message": "second", "conversation_id": cid})
        resp = tc.get(f"/v1/conversations/{cid}/messages")
    assert resp.status_code == 200
    msgs = resp.json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "first"),
        ("assistant", "one"),
        ("user", "second"),
        ("assistant", "two"),
    ]
    assert all(m.get("created_at") for m in msgs)


def test_preferences_list_empty_keys(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        resp = tc.get("/v1/preferences")
    assert resp.status_code == 200
    body = resp.json()
    prefs = body["preferences"]
    assert [p["key"] for p in prefs] == ["favorite_genres", "tone"]
    assert all(p["value"] is None for p in prefs)


def test_preferences_put_roundtrip_and_errors(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        ok = tc.put("/v1/preferences/tone", json={"value": "  dry  "})
        assert ok.status_code == 200
        assert ok.json() == {"key": "tone", "value": "dry"}

        genres = tc.put(
            "/v1/preferences/favorite_genres",
            json={"value": "sci-fi, drama"},
        )
        assert genres.status_code == 200
        assert genres.json()["value"] == '["sci-fi","drama"]'

        listed = tc.get("/v1/preferences")
        assert listed.status_code == 200
        by_key = {p["key"]: p["value"] for p in listed.json()["preferences"]}
        assert by_key["tone"] == "dry"
        assert by_key["favorite_genres"] == '["sci-fi","drama"]'

        bad_key = tc.put("/v1/preferences/nope", json={"value": "x"})
        assert bad_key.status_code == 400
        assert "unknown" in bad_key.json()["detail"].lower()

        bad_val = tc.put("/v1/preferences/tone", json={"value": "   "})
        assert bad_val.status_code == 400
        assert "invalid" in bad_val.json()["detail"].lower()

        bad_body = tc.put("/v1/preferences/tone", json={})
        assert bad_body.status_code == 422


def test_chat_missing_message_400(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        resp = tc.post("/v1/chat", json={})
    assert resp.status_code == 400


def test_openai_completions_shape(settings: Settings) -> None:
    ollama = ScriptedOllama([ChatMessage(role="assistant", content="Hello there")])
    with _client(settings, ollama) as tc:
        resp = tc.post(
            "/v1/chat/completions",
            json={
                "model": "ignored",
                "messages": [
                    {"role": "system", "content": "HA system"},
                    {"role": "user", "content": "hi"},
                ],
                "tools": [{"type": "function", "function": {"name": "ha_only"}}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "qwen3:8b"
    assert body["choices"][0]["message"]["content"] == "Hello there"
    # Client tools ignored; no tool round-trip in this scripted path
    assert len(ollama.calls) == 1
    # Mimir system prompt used; HA system dropped
    roles = [
        m.role if isinstance(m, ChatMessage) else m.get("role")
        for m in ollama.calls[0]["messages"]
    ]
    assert roles[0] == "system"
    assert "You are Mimir." in (
        ollama.calls[0]["messages"][0].content
        if isinstance(ollama.calls[0]["messages"][0], ChatMessage)
        else ""
    )


def test_openai_stream_501(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        resp = tc.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
    assert resp.status_code == 501


def test_openai_models(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        resp = tc.get("/v1/models")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "qwen3:8b"


def test_db_schema_version(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    assert db.ping() is True
    assert db.schema_version() == SCHEMA_VERSION
    assert SCHEMA_VERSION == 4


def test_load_system_prompt(tmp_path: Path) -> None:
    p = tmp_path / "prompt.md"
    p.write_text("Be helpful.\n", encoding="utf-8")
    text, prompt_id = load_system_prompt(p)
    assert text.startswith("Be helpful")
    assert prompt_id.startswith("sha256:")


def test_turn_timeout_in_agent() -> None:
    import time

    forever = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[_tool_call("get_server_time")],
    )
    client = ScriptedOllama([forever, forever])
    result = run_turn(
        client,  # type: ignore[arg-type]
        [ChatMessage(role="user", content="time?")],
        max_iterations=3,
        deadline_monotonic=time.monotonic() - 1,
    )
    assert result.stopped_reason == "turn_timeout"


def test_service_turn_timeout_message(settings: Settings) -> None:
    settings.timeouts.turn_s = 0
    forever = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[_tool_call("echo", {"text": "x"})],
    )
    ollama = ScriptedOllama([forever, forever])
    with _client(settings, ollama) as tc:
        resp = tc.post("/v1/chat", json={"message": "loop"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == MSG_TURN_TIMEOUT
    assert resp.json()["stopped_reason"] == "turn_timeout"
