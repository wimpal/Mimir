"""Tests for shopping_list.list smoke-product filtering."""

from __future__ import annotations

import json

from brain.agent import StoppedReason, run_turn
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.shopping_list import (
    filter_shopping_list_items,
    filter_shopping_list_tool_result,
    is_shopping_list_smoke_name,
)
from brain.tools import Tool
from brain.turn_fixup import fix_weather_shopping_reply, format_shopping_list_sentence

WEATHER_PAYLOAD = {
    "current": {"temperature_c": 18.3, "conditions": "overcast"},
    "today": {"temp_max_c": 19.6, "temp_min_c": 13.5, "conditions": "overcast"},
}

RAW_LIST = [
    {"id": "1", "name": "kaas", "quantity": 1, "checked": False},
    {"id": "2", "name": "melk", "quantity": 1, "checked": False},
    {"id": "3", "name": "mcp-smoke-1788344200426", "quantity": 4, "checked": False},
    {"id": "4", "name": "mcp-smoke-revert-1788351164998", "quantity": 4, "checked": False},
    {"id": "5", "name": "brood", "quantity": 1, "checked": True},
]


def test_is_shopping_list_smoke_name() -> None:
    assert is_shopping_list_smoke_name("mcp-smoke-123")
    assert is_shopping_list_smoke_name("mcp-smoke-revert-1")
    assert not is_shopping_list_smoke_name("kaas")


def test_filter_shopping_list_items() -> None:
    kept = filter_shopping_list_items(RAW_LIST)
    names = {item["name"] for item in kept}
    assert names == {"kaas", "melk"}


def test_filter_shopping_list_tool_result_json() -> None:
    filtered = json.loads(filter_shopping_list_tool_result(json.dumps(RAW_LIST)))
    assert len(filtered) == 2
    assert filtered[0]["name"] == "kaas"


def test_format_shopping_list_sentence_excludes_smoke() -> None:
    sentence = format_shopping_list_sentence(RAW_LIST, "nl")
    assert "kaas" in sentence
    assert "melk" in sentence
    assert "mcp-smoke" not in sentence


def test_fixup_excludes_smoke_from_tool_payload() -> None:
    out = fix_weather_shopping_reply(
        "",
        "Vertel me in twee zinnen wat het weer is en wat er op de boodschappenlijst staat.",
        weather=WEATHER_PAYLOAD,
        shopping_list_fetched=True,
        shopping_items=RAW_LIST,
    )
    assert "mcp-smoke" not in out.lower()
    assert "kaas" in out.lower()
    assert "melk" in out.lower()


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self._responses = list(responses)

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        return ChatResponse(message=self._responses.pop(0))


def _read_tool(name: str, payload: str) -> Tool:
    return Tool(
        name=name,
        description="test",
        parameters={"type": "object", "properties": {}},
        execute=lambda **_kwargs: payload,
    )


def test_agent_tool_message_seen_by_model_is_filtered() -> None:
    registry = {
        "homebase.shopping_list.list": _read_tool(
            "homebase.shopping_list.list",
            json.dumps(RAW_LIST),
        ),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.shopping_list.list",
                            arguments={},
                        )
                    )
                ],
            ),
            ChatMessage(
                role="assistant",
                content="Op de boodschappenlijst staan kaas en melk.",
            ),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="wat staat er op de boodschappenlijst?")],
        tools=registry,
    )
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    data = json.loads(tool_msgs[0].content or "[]")
    assert len(data) == 2
    assert result.stopped_reason == StoppedReason.FINAL
