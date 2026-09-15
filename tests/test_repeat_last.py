"""Tests for repeat-last-response short-circuit (T-054)."""

from __future__ import annotations

from brain.agent import StoppedReason, run_turn
from brain.ollama import ChatMessage, ChatResponse
from brain.recipe_import import PendingRecipeStore
from brain.repeat_last import (
    is_repeat_intent,
    last_final_assistant,
    nothing_to_repeat_reply,
    repeat_locale,
)
from brain.tools import Tool


def test_repeat_triggers_en_nl() -> None:
    assert is_repeat_intent("Repeat that")
    assert is_repeat_intent("say that again")
    assert is_repeat_intent("Say it again!")
    assert is_repeat_intent("one more time")
    assert is_repeat_intent("Zeg dat nog eens")
    assert is_repeat_intent("herhaal dat")
    assert is_repeat_intent("Nog een keer?")
    assert not is_repeat_intent("repeat this string verbatim")
    assert not is_repeat_intent("what did you say")
    assert not is_repeat_intent("ja")
    assert not is_repeat_intent("good night")
    assert not is_repeat_intent("")


def test_repeat_locale() -> None:
    assert repeat_locale("Zeg dat nog eens") == "nl"
    assert repeat_locale("herhaal dat") == "nl"
    assert repeat_locale("nog een keer") == "nl"
    assert repeat_locale("Repeat that") == "en"
    assert repeat_locale("say that again") == "en"
    assert repeat_locale("one more time") == "en"


def test_last_final_assistant_skips_empty() -> None:
    msgs = [
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content=""),
        ChatMessage(role="assistant", content="Hello there."),
        ChatMessage(role="user", content="Repeat that"),
    ]
    assert last_final_assistant(msgs) == "Hello there."
    assert last_final_assistant([ChatMessage(role="user", content="hi")]) is None


def test_nothing_to_repeat_copy() -> None:
    assert "nothing" in nothing_to_repeat_reply("en").lower()
    assert "niets" in nothing_to_repeat_reply("nl").lower()


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls = 0

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("Ollama should not be called for repeat-only turns")
        return ChatResponse(message=self._responses.pop(0))


def _echo_tool(calls: list[str]) -> Tool:
    def execute(**kwargs: object) -> str:
        calls.append(str(kwargs.get("text", "")))
        return str(kwargs.get("text", ""))

    return Tool(
        name="echo",
        description="echo",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=execute,
    )


def test_agent_repeats_prior_assistant_without_tools() -> None:
    prior = "Ballon is off."
    client = _ScriptedClient()
    tool_calls: list[str] = []
    result = run_turn(
        client,
        [
            ChatMessage(role="user", content="turn off Ballon"),
            ChatMessage(role="assistant", content=prior),
            ChatMessage(role="user", content="Repeat that"),
        ],
        tools={"echo": _echo_tool(tool_calls)},
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.content == prior
    assert client.calls == 0
    assert tool_calls == []
    assert result.tools_used() == []
    assert any(s.anomaly == "repeat_last" for s in result.steps)


def test_agent_repeat_empty_history_nl() -> None:
    client = _ScriptedClient()
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Zeg dat nog eens")],
        tools={},
        max_iterations=2,
    )
    assert result.content == nothing_to_repeat_reply("nl")
    assert client.calls == 0
    assert result.tools_used() == []


def test_agent_double_repeat_same_text() -> None:
    """After a repeat turn is persisted in working, another repeat keeps substance."""
    client = _ScriptedClient()
    original = "It will rain tomorrow."
    result1 = run_turn(
        client,
        [
            ChatMessage(role="assistant", content=original),
            ChatMessage(role="user", content="Say that again"),
        ],
        tools={},
    )
    assert result1.content == original
    # Simulate persisted history after repeat (user + assistant copy).
    result2 = run_turn(
        client,
        [
            ChatMessage(role="assistant", content=original),
            ChatMessage(role="user", content="Say that again"),
            ChatMessage(role="assistant", content=original),
            ChatMessage(role="user", content="one more time"),
        ],
        tools={},
    )
    assert result2.content == original
    assert client.calls == 0


def test_repeat_keeps_pending_recipe() -> None:
    store = PendingRecipeStore()
    cid = "conv-repeat-pending"
    store.set(
        cid,
        {
            "title": "Pasta",
            "ingredients": [{"name": "pasta", "quantity": "200 g"}],
            "steps": ["Boil."],
        },
        dutch=True,
    )
    confirm = "Wil je Pasta opslaan?"
    client = _ScriptedClient()
    result = run_turn(
        client,
        [
            ChatMessage(role="assistant", content=confirm),
            ChatMessage(role="user", content="herhaal dat"),
        ],
        tools={},
        conversation_id=cid,
        pending_recipes=store,
    )
    assert result.content == confirm
    assert store.has(cid)
    assert store.is_confirmable(cid)
    assert client.calls == 0
