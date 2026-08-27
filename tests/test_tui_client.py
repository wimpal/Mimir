"""Unit tests for the TUI brain client and state helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from clients.tui.brain_client import (
    BrainClient,
    BrainClientError,
    normalize_brain_url,
    parse_sse_chunk,
)
from clients.tui.brain_launcher import (
    brain_reachable,
    find_repo_root,
    host_port_from_url,
)
from clients.tui.state import ChatState, load_state, save_state


def test_normalize_brain_url_strips_slash_and_v1() -> None:
    assert normalize_brain_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert normalize_brain_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000"
    assert normalize_brain_url("http://127.0.0.1:8000/v1/") == "http://127.0.0.1:8000"


def test_normalize_brain_url_rejects_empty() -> None:
    with pytest.raises(ValueError):
        normalize_brain_url("  ")


def test_parse_sse_chunk_fragmented() -> None:
    events, rest = parse_sse_chunk('data: {"type":"meta","conversation_id":"a"}\n\n')
    assert events == [{"type": "meta", "conversation_id": "a"}]
    assert rest == ""

    events, rest = parse_sse_chunk('data: {"type":"tok')
    assert events == []
    assert rest.startswith("data:")

    events, rest = parse_sse_chunk(rest + 'en","text":"hi"}\n\n')
    assert events == [{"type": "token", "text": "hi"}]


def test_parse_sse_ignores_malformed_and_unknown_ok() -> None:
    buf = 'data: not-json\n\ndata: {"type":"done","stopped_reason":"final"}\n\n'
    events, rest = parse_sse_chunk(buf)
    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert rest == ""


def test_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "chat_state.json"
    save_state(ChatState(conversation_id="abc-123"), path)
    loaded = load_state(path)
    assert loaded.conversation_id == "abc-123"


def test_state_corrupt_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "chat_state.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path).conversation_id is None


def test_state_missing_file(tmp_path: Path) -> None:
    assert load_state(tmp_path / "nope.json").conversation_id is None


def test_health_degraded_still_reachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "status": "degraded",
                "ollama": {"reachable": False},
            },
        )

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as raw:
            client = BrainClient("http://test", client=raw)
            info = await client.health()
        assert info.status == "degraded"
        assert info.reachable is True
        assert "ollama" in info.detail

    asyncio.run(_run())


def test_stream_chat_happy_path() -> None:
    sse = (
        'data: {"type":"meta","conversation_id":"c1"}\n\n'
        'data: {"type":"token","text":"Hi"}\n\n'
        'data: {"type":"done","stopped_reason":"final","conversation_id":"c1"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat"
        body = json.loads(request.content.decode())
        assert body["stream"] is True
        assert body["message"] == "hello"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse.encode(),
        )

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as raw:
            client = BrainClient("http://test", client=raw)
            events = [e async for e in client.stream_chat("hello")]
        assert [e["type"] for e in events] == ["meta", "token", "done"]
        assert events[1]["text"] == "Hi"

    asyncio.run(_run())


def test_stream_chat_json_400() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"detail": "conversation_id requires message"},
        )

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as raw:
            client = BrainClient("http://test", client=raw)
            with pytest.raises(BrainClientError, match="conversation_id requires"):
                async for _ in client.stream_chat(""):
                    pass

    asyncio.run(_run())


def test_stream_chat_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as raw:
            client = BrainClient("http://test", client=raw)
            with pytest.raises(BrainClientError, match="chat failed"):
                async for _ in client.stream_chat("hi"):
                    pass

    asyncio.run(_run())


def test_client_uses_long_read_timeout() -> None:
    client = BrainClient("http://127.0.0.1:8000", turn_timeout_s=180)
    assert client.turn_timeout_s == 180.0
    assert client._client.timeout.read == 180.0


def test_client_sets_authorization_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIMIR_AUTH_TOKEN", "tok-from-env")
    client = BrainClient("http://127.0.0.1:8000")
    assert client._client.headers["Authorization"] == "Bearer tok-from-env"


def test_client_auth_token_arg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIMIR_AUTH_TOKEN", "env-tok")
    client = BrainClient("http://127.0.0.1:8000", auth_token="arg-tok")
    assert client._client.headers["Authorization"] == "Bearer arg-tok"


def test_list_conversations_and_encoded_messages() -> None:
    from clients.tui.history import format_history_option

    rows_payload = {
        "conversations": [
            {
                "id": "abc-123",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "preview": "hello world",
                "message_count": 2,
            }
        ],
        "limit": 50,
        "count": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/conversations":
            assert request.url.params.get("limit") == "50"
            return httpx.Response(200, json=rows_payload)
        # Path is percent-encoded for unsafe characters (space → %20).
        if request.url.path == "/v1/conversations/id%20with%20space/messages":
            return httpx.Response(
                200,
                json={
                    "conversation_id": "id with space",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        # httpx may present a decoded path; accept that too if raw matched encode.
        raw = request.url.raw_path.decode("ascii", errors="replace")
        if "id%20with%20space" in raw or request.url.path.endswith(
            "/id with space/messages"
        ):
            return httpx.Response(
                200,
                json={
                    "conversation_id": "id with space",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        return httpx.Response(404, json={"detail": request.url.path})

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as raw:
            client = BrainClient("http://test", client=raw)
            rows = await client.list_conversations()
            msgs = await client.list_messages("id with space")
        assert len(rows) == 1
        assert rows[0]["id"] == "abc-123"
        assert msgs == [{"role": "user", "content": "hi"}]

    asyncio.run(_run())
    label = format_history_option(rows_payload["conversations"][0])
    assert "hello world" in label
    assert "abc-123"[:8] in label


def test_list_conversations_bad_shape_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"conversations": "nope"})

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as raw:
            client = BrainClient("http://test", client=raw)
            with pytest.raises(BrainClientError, match="unexpected JSON"):
                await client.list_conversations()

    asyncio.run(_run())


def test_preferences_client_and_display() -> None:
    from clients.tui.settings import (
        edit_seed_value,
        format_preference_display,
    )

    assert format_preference_display("tone", None) == "tone  ·  (unset)"
    assert format_preference_display("tone", "dry") == "tone  ·  dry"
    assert (
        format_preference_display("favorite_genres", '["sci-fi","drama"]')
        == "favorite_genres  ·  sci-fi, drama"
    )
    assert edit_seed_value("favorite_genres", '["sci-fi"]') == "sci-fi"
    assert edit_seed_value("tone", None) == ""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/preferences":
            return httpx.Response(
                200,
                json={
                    "preferences": [
                        {"key": "favorite_genres", "value": None},
                        {"key": "tone", "value": "dry"},
                    ]
                },
            )
        if request.method == "PUT" and request.url.path == "/v1/preferences/tone":
            body = json.loads(request.content.decode("utf-8"))
            assert body == {"value": "wry"}
            return httpx.Response(
                200, json={"key": "tone", "value": "wry"}
            )
        return httpx.Response(404, json={"detail": request.url.path})

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as raw:
            client = BrainClient("http://test", client=raw)
            rows = await client.get_preferences()
            assert [r["key"] for r in rows] == ["favorite_genres", "tone"]
            saved = await client.put_preference("tone", "wry")
            assert saved["value"] == "wry"

    asyncio.run(_run())


def test_host_port_from_url() -> None:
    assert host_port_from_url("http://127.0.0.1:8000") == ("127.0.0.1", 8000)
    assert host_port_from_url("http://localhost:9000/v1") == ("localhost", 9000)


def test_find_repo_root_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "brain").mkdir()
    monkeypatch.setenv("MIMIR_REPO_ROOT", str(tmp_path))
    assert find_repo_root() == tmp_path.resolve()


def test_brain_reachable_false_on_connect_error() -> None:
    assert brain_reachable("http://127.0.0.1:1", timeout_s=0.2) is False


def test_ctrl_c_not_bound_to_quit() -> None:
    from clients.tui.app import MimirApp

    quit_keys = {
        b.key for b in MimirApp.BINDINGS if b.action == "quit"
    }
    assert "ctrl+c" not in quit_keys
    assert "ctrl+d" in quit_keys


def test_host_clipboard_copy_empty_is_false() -> None:
    from clients.tui.app import host_clipboard_copy

    assert host_clipboard_copy("") is False
