"""Tests for weather+shopping reply fixups and locale detection."""

from __future__ import annotations

import json

from brain.agent import StoppedReason, run_turn
from brain.morning_brief import morning_brief_lacks_weather
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.tools import Tool
from brain.turn_fixup import (
    can_tool_backed_weather_shopping_reply,
    fix_weather_shopping_reply,
    format_shopping_list_sentence,
    needs_weather_shopping_fixup,
    reply_grounded_in_shopping_list,
    reply_locale_mismatch,
    user_asked_about_shopping_list,
    user_asked_about_weather,
    user_message_locale,
)

WEATHER_PAYLOAD = {
    "current": {"temperature_c": 16.7, "conditions": "overcast"},
    "today": {"temp_max_c": 21.2, "temp_min_c": 15.0, "conditions": "overcast"},
}

SHOPPING_ITEMS = [
    {"id": "1", "name": "kaas", "quantity": 1, "checked": False},
    {"id": "2", "name": "melk", "quantity": 1, "checked": False},
]

BAD_ENGLISH_REPLY = (
    'The shopping list includes: kaas (1), melk (1), and several items labeled '
    '"mcp-smoke" (each with a quantity of 4). Some of these items are already '
    "marked as checked. Would you like to adjust any of them or add more to the list?"
)

USER_NL_COMPOUND = (
    "Vertel me in twee zinnen wat het weer is en wat er op de boodschappelijst staat."
)

USER_NL_STT_TYPO = (
    "Vertel me in twee zinnen wat het weer is en wat er op de boodschappelij staat."
)

BAD_DUTCH_HALLUCINATION = (
    "Het is nu bewolkt met een temperatuur van 18,3 °C, en de rest van de dag is licht "
    "neerslaan met een temperatuur tussen 13,5 °C en 19,6 °C. Op de boodschappelij "
    "staan kaas, melk en diverse mcp-smoke-producten, waarvan de meeste al aangemarkeerd zijn."
)


def test_user_message_locale_dutch_compound() -> None:
    assert user_message_locale(USER_NL_COMPOUND) == "nl"
    assert user_message_locale("What's on the shopping list?") == "en"


def test_user_asked_weather_and_shopping() -> None:
    assert user_asked_about_weather(USER_NL_COMPOUND)
    assert user_asked_about_shopping_list(USER_NL_COMPOUND)
    assert user_asked_about_shopping_list(USER_NL_STT_TYPO)


def test_dutch_hallucination_reply_needs_fixup() -> None:
    assert needs_weather_shopping_fixup(
        USER_NL_STT_TYPO,
        BAD_DUTCH_HALLUCINATION,
        weather=WEATHER_PAYLOAD,
        shopping_list_fetched=True,
        shopping_items=SHOPPING_ITEMS,
    )


def test_fix_dutch_hallucination_two_sentences() -> None:
    out = fix_weather_shopping_reply(
        BAD_DUTCH_HALLUCINATION,
        USER_NL_STT_TYPO,
        weather=WEATHER_PAYLOAD,
        shopping_list_fetched=True,
        shopping_items=SHOPPING_ITEMS,
    )
    assert out.count(".") >= 2
    assert "mcp" not in out.lower()
    assert "aangemarkeerd" not in out.lower()
    assert "kaas" in out.lower()
    assert "melk" in out.lower()
    assert "18" in out or "17" in out or "graden" in out.lower()


def test_reply_locale_mismatch_english_reply_to_dutch() -> None:
    assert reply_locale_mismatch("nl", BAD_ENGLISH_REPLY)
    assert not reply_locale_mismatch("nl", "Op de boodschappenlijst staan kaas en melk.")


def test_reply_not_grounded_in_shopping_list() -> None:
    assert not reply_grounded_in_shopping_list(BAD_ENGLISH_REPLY, SHOPPING_ITEMS)
    grounded = format_shopping_list_sentence(SHOPPING_ITEMS, "nl")
    assert reply_grounded_in_shopping_list(grounded, SHOPPING_ITEMS)


def test_needs_fixup_for_operator_failure_case() -> None:
    assert needs_weather_shopping_fixup(
        USER_NL_COMPOUND,
        BAD_ENGLISH_REPLY,
        weather=WEATHER_PAYLOAD,
        shopping_list_fetched=True,
        shopping_items=SHOPPING_ITEMS,
    )


def test_fix_weather_shopping_reply_dutch() -> None:
    out = fix_weather_shopping_reply(
        BAD_ENGLISH_REPLY,
        USER_NL_COMPOUND,
        weather=WEATHER_PAYLOAD,
        shopping_list_fetched=True,
        shopping_items=SHOPPING_ITEMS,
    )
    assert "graden" in out.lower() or "bewolkt" in out.lower()
    assert "kaas" in out.lower()
    assert "melk" in out.lower()
    assert "mcp" not in out.lower()
    assert "shopping list" not in out.lower()
    assert morning_brief_lacks_weather(out) is False


def _read_tool(name: str, payload: str) -> Tool:
    return Tool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        execute=lambda _args=None: payload,
    )


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self._responses = list(responses)

    def chat(
        self,
        messages: list[ChatMessage | dict],
        tools: list[dict] | None = None,
        *,
        think: bool = False,
        stream: bool = False,
    ) -> ChatResponse:
        if not self._responses:
            raise AssertionError("no scripted responses left")
        nxt = self._responses.pop(0)
        return ChatResponse(message=nxt)


def test_agent_fixes_weather_shopping_hallucination() -> None:
    registry = {
        "get_weather": _read_tool("get_weather", json.dumps(WEATHER_PAYLOAD)),
        "homebase.shopping_list.list": _read_tool(
            "homebase.shopping_list.list",
            json.dumps(SHOPPING_ITEMS),
        ),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(name="get_weather", arguments={})
                    ),
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.shopping_list.list",
                            arguments={},
                        )
                    ),
                ],
            ),
            ChatMessage(role="assistant", content=BAD_ENGLISH_REPLY),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content=USER_NL_COMPOUND)],
        tools=registry,
        max_iterations=3,
    )
    content = result.content or ""
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.steps[-1].anomaly == "weather_shopping_fixup"
    assert "graden" in content.lower() or "bewolkt" in content.lower()
    assert "kaas" in content.lower()
    assert "melk" in content.lower()
    assert "mcp" not in content.lower()
    assert "would you like" not in content.lower()


def test_agent_fixes_dutch_hallucination_with_stt_typo() -> None:
    registry = {
        "get_weather": _read_tool("get_weather", json.dumps(WEATHER_PAYLOAD)),
        "homebase.shopping_list.list": _read_tool(
            "homebase.shopping_list.list",
            json.dumps(SHOPPING_ITEMS),
        ),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(name="get_weather", arguments={})
                    ),
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.shopping_list.list",
                            arguments={},
                        )
                    ),
                ],
            ),
            ChatMessage(role="assistant", content=BAD_DUTCH_HALLUCINATION),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content=USER_NL_STT_TYPO)],
        tools=registry,
        max_iterations=3,
    )
    content = result.content or ""
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.steps[-1].anomaly == "weather_shopping_fixup"
    assert "mcp" not in content.lower()
    assert "kaas" in content.lower()
    assert "melk" in content.lower()


def test_agent_empty_final_reply_uses_tool_fixup() -> None:
    registry = {
        "get_weather": _read_tool("get_weather", json.dumps(WEATHER_PAYLOAD)),
        "homebase.shopping_list.list": _read_tool(
            "homebase.shopping_list.list",
            json.dumps(SHOPPING_ITEMS),
        ),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(name="get_weather", arguments={})
                    ),
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.shopping_list.list",
                            arguments={},
                        )
                    ),
                ],
            ),
            ChatMessage(role="assistant", content=""),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content=USER_NL_COMPOUND)],
        tools=registry,
        max_iterations=3,
    )
    content = result.content or ""
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.steps[-1].anomaly == "weather_shopping_fixup"
    assert "graden" in content.lower() or "bewolkt" in content.lower()
    assert "kaas" in content.lower()
    assert "melk" in content.lower()
