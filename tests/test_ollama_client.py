"""Unit tests for OllamaClient — mocked HTTP, no live Ollama."""

from __future__ import annotations

import json

import httpx
import pytest

from brain.ollama import ChatMessage, OllamaClient, OllamaError, ToolCall, ToolCallFunction


def _handler(response_body: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(status, json=response_body)

    return handler


def test_chat_parses_content_only() -> None:
    transport = httpx.MockTransport(
        _handler({"message": {"role": "assistant", "content": "Hello"}})
    )
    with OllamaClient("http://test", "qwen3:8b", transport=transport) as client:
        resp = client.chat([ChatMessage(role="user", content="hi")])
    assert resp.message.content == "Hello"
    assert resp.message.tool_calls == []


def test_chat_parses_tool_calls() -> None:
    body = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "index": 0,
                        "name": "echo",
                        "arguments": {"text": "ping"},
                    },
                }
            ],
        }
    }
    transport = httpx.MockTransport(_handler(body))
    with OllamaClient("http://test", "qwen3:8b", transport=transport) as client:
        resp = client.chat([{"role": "user", "content": "echo ping"}])
    assert len(resp.message.tool_calls) == 1
    tc = resp.message.tool_calls[0]
    assert tc.function.name == "echo"
    assert tc.function.arguments == {"text": "ping"}


def test_chat_parses_string_arguments_json() -> None:
    body = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "echo",
                        "arguments": '{"text": "x"}',
                    }
                }
            ],
        }
    }
    transport = httpx.MockTransport(_handler(body))
    with OllamaClient("http://test", "m", transport=transport) as client:
        resp = client.chat([{"role": "user", "content": "x"}])
    assert resp.message.tool_calls[0].function.arguments == {"text": "x"}


def test_chat_sends_think_false_and_tools() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "ok"}}
        )

    tools = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]
    with OllamaClient(
        "http://t", "m", num_ctx=8192, keep_alive="45m", transport=httpx.MockTransport(handler)
    ) as client:
        client.chat([{"role": "user", "content": "hi"}], tools=tools, think=False)
    assert captured["body"]["think"] is False
    assert captured["body"]["stream"] is False
    assert captured["body"]["tools"] == tools
    assert captured["body"]["options"]["num_ctx"] == 8192
    assert captured["body"]["keep_alive"] == "45m"


def test_chat_parses_ollama_timings() -> None:
    body = {
        "message": {"role": "assistant", "content": "Hello"},
        "load_duration": 5_000_000_000,
        "prompt_eval_count": 100,
        "prompt_eval_duration": 200_000_000,
        "eval_count": 10,
        "eval_duration": 50_000_000,
    }
    transport = httpx.MockTransport(_handler(body))
    with OllamaClient("http://test", "qwen3:8b", transport=transport) as client:
        resp = client.chat([ChatMessage(role="user", content="hi")])
    assert resp.timings.load_duration_ms == 5000.0
    assert resp.timings.prompt_eval_duration_ms == 200.0
    assert resp.timings.eval_duration_ms == 50.0
    assert resp.timings.prompt_eval_count == 100
    assert resp.timings.eval_count == 10


def test_chat_http_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    with OllamaClient("http://t", "m", transport=transport) as client:
        with pytest.raises(OllamaError, match="HTTP 500"):
            client.chat([{"role": "user", "content": "hi"}])


def test_chat_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with OllamaClient("http://t", "m", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OllamaError, match="timed out"):
            client.chat([{"role": "user", "content": "hi"}])


def test_message_to_api_dict_with_tool_calls() -> None:
    msg = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            ToolCall(function=ToolCallFunction(name="echo", arguments={"text": "a"}))
        ],
    )
    d = msg.to_api_dict()
    assert d["tool_calls"][0]["function"]["name"] == "echo"
    assert d["tool_calls"][0]["function"]["arguments"] == {"text": "a"}


def test_stream_rejected() -> None:
    transport = httpx.MockTransport(
        _handler({"message": {"role": "assistant", "content": "x"}})
    )
    with OllamaClient("http://t", "m", transport=transport) as client:
        with pytest.raises(OllamaError, match="streaming"):
            client.chat([{"role": "user", "content": "hi"}], stream=True)
