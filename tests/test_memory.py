"""Phase 4 memory & preferences — API + SQLite seams."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brain.config import Settings
from brain.db import Database
from brain.main import create_app
from brain.ollama import (
    ChatMessage,
    ChatResponse,
    OllamaError,
    ToolCall,
    ToolCallFunction,
)
from brain.service import MSG_OLLAMA_DOWN


class ScriptedOllama:
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
        memory={"history_pairs": 20},
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


def _db(settings: Settings) -> Database:
    return Database(settings.runtime.data_dir / "mimir.db")


def _system_text(call: dict[str, Any]) -> str:
    msg = call["messages"][0]
    if isinstance(msg, ChatMessage):
        return msg.content
    return str(msg.get("content", ""))


def test_mint_conversation_and_restart_history(settings: Settings) -> None:
    ollama = ScriptedOllama(
        [
            ChatMessage(role="assistant", content="First reply."),
            ChatMessage(role="assistant", content="Second reply."),
        ]
    )
    with _client(settings, ollama) as tc:
        r1 = tc.post("/v1/chat", json={"message": "hello"})
        assert r1.status_code == 200
        cid = r1.json()["conversation_id"]
        assert cid
        r2 = tc.post(
            "/v1/chat",
            json={"message": "again", "conversation_id": cid},
        )
        assert r2.status_code == 200

    assert len(ollama.calls) == 2
    second_msgs = ollama.calls[1]["messages"]
    roles_contents = [
        (m.role, m.content)
        for m in second_msgs
        if isinstance(m, ChatMessage) and m.role in ("user", "assistant")
    ]
    assert ("user", "hello") in roles_contents
    assert ("assistant", "First reply.") in roles_contents
    assert roles_contents[-1] == ("user", "again")

    db = _db(settings)
    assert db.message_count(cid) == 4


def test_conversation_id_without_message_400(settings: Settings) -> None:
    with _client(settings, ScriptedOllama([])) as tc:
        resp = tc.post(
            "/v1/chat",
            json={
                "conversation_id": "abc",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 400
    assert "message" in resp.json()["detail"].lower()
    assert _db(settings).message_count() == 0


def test_messages_only_stateless_no_persist(settings: Settings) -> None:
    ollama = ScriptedOllama([ChatMessage(role="assistant", content="stateless")])
    with _client(settings, ollama) as tc:
        resp = tc.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["conversation_id"] is None
    assert _db(settings).message_count() == 0


def test_create_on_write_client_id(settings: Settings) -> None:
    ollama = ScriptedOllama([ChatMessage(role="assistant", content="ok")])
    with _client(settings, ollama) as tc:
        resp = tc.post(
            "/v1/chat",
            json={"message": "hi", "conversation_id": "client-fixed-id"},
        )
    assert resp.status_code == 200
    assert resp.json()["conversation_id"] == "client-fixed-id"
    assert _db(settings).message_count("client-fixed-id") == 2


def test_prefs_set_inject_and_mid_turn_refresh(settings: Settings) -> None:
    ollama = ScriptedOllama(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    _tool_call(
                        "set_preference",
                        {"key": "favorite_genres", "value": "sci-fi"},
                    )
                ],
            ),
            ChatMessage(role="assistant", content="Noted — you like sci-fi."),
            ChatMessage(role="assistant", content="Based on your tastes."),
        ]
    )
    with _client(settings, ollama) as tc:
        r1 = tc.post("/v1/chat", json={"message": "I like sci-fi"})
        assert r1.status_code == 200
        assert "set_preference" in r1.json()["tools_used"]
        cid = r1.json()["conversation_id"]

        # Mid-turn: second Ollama call should already see prefs in system
        assert len(ollama.calls) >= 2
        assert "sci-fi" in _system_text(ollama.calls[1])

        r2 = tc.post(
            "/v1/chat",
            json={"message": "recommend something", "conversation_id": cid},
        )
        assert r2.status_code == 200

    assert "sci-fi" in _system_text(ollama.calls[2])
    assert _db(settings).get_preference("favorite_genres") == '["sci-fi"]'


def test_error_reply_persisted(settings: Settings) -> None:
    ollama = ScriptedOllama([OllamaError("down")])
    with _client(settings, ollama) as tc:
        resp = tc.post("/v1/chat", json={"message": "hello"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == MSG_OLLAMA_DOWN
    cid = resp.json()["conversation_id"]
    recent = _db(settings).list_recent_messages(cid, limit=10)
    assert recent[-1].role == "assistant"
    assert recent[-1].content == MSG_OLLAMA_DOWN


def test_openai_compat_does_not_write_messages(settings: Settings) -> None:
    ollama = ScriptedOllama([ChatMessage(role="assistant", content="Hello")])
    before = _db(settings).message_count()
    with _client(settings, ollama) as tc:
        resp = tc.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert _db(settings).message_count() == before
