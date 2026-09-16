"""Tests for explain-yourself short-circuit and turn-log helpers (T-055)."""

from __future__ import annotations

import json
from pathlib import Path

from brain.agent import StoppedReason, TurnResult, run_turn
from brain.explain_yourself import (
    build_explain_reply,
    explain_locale,
    is_explain_yourself_intent,
    no_tools_reply,
    previous_user_message,
)
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.turn_log import (
    append_turn_trace,
    latest_trace_for_conversation,
    tool_calls_summary,
    turns_log_path,
)


def test_explain_triggers_en_nl() -> None:
    assert is_explain_yourself_intent("Why did you do that?")
    assert is_explain_yourself_intent("explain yourself")
    assert is_explain_yourself_intent("Why those tools")
    assert is_explain_yourself_intent("why did you call those tools")
    assert is_explain_yourself_intent("Waarom deed je dat?")
    assert is_explain_yourself_intent("waarom die tools")
    assert is_explain_yourself_intent("Leg uit")
    assert is_explain_yourself_intent("leg uit.")
    assert not is_explain_yourself_intent("Leg uit fotosynthese")
    assert not is_explain_yourself_intent("explain like I'm five")
    assert not is_explain_yourself_intent("ELI5 gravity")
    assert not is_explain_yourself_intent("give me both sides")
    assert not is_explain_yourself_intent("Repeat that")
    assert not is_explain_yourself_intent("")


def test_explain_locale() -> None:
    assert explain_locale("Leg uit") == "nl"
    assert explain_locale("waarom deed je dat") == "nl"
    assert explain_locale("Why did you do that?") == "en"
    assert explain_locale("explain yourself") == "en"


def test_previous_user_message() -> None:
    msgs = [
        ChatMessage(role="user", content="what's on the list?"),
        ChatMessage(role="assistant", content="Milk and eggs."),
        ChatMessage(role="user", content="Why did you do that?"),
    ]
    assert previous_user_message(msgs) == "what's on the list?"
    assert previous_user_message([ChatMessage(role="user", content="Leg uit")]) is None


def test_build_explain_reply_cites_tools() -> None:
    reply = build_explain_reply(
        locale="en",
        tool_labels=["shopping_list.list"],
        prior_user="what's on the shopping list?",
    )
    assert "shopping_list.list" in reply
    assert "shopping list" in reply.lower()
    empty = build_explain_reply(locale="en", tool_labels=[], prior_user=None)
    assert empty == no_tools_reply("en")
    nl = build_explain_reply(
        locale="nl",
        tool_labels=["get_weather"],
        prior_user="hoe is het weer?",
    )
    assert "get_weather" in nl
    assert "vorige" in nl.lower() or "vraag" in nl.lower()


class _ScriptedClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        self.calls += 1
        raise AssertionError("Ollama should not be called for explain-yourself turns")


def test_agent_explain_cites_planted_trace(tmp_path: Path) -> None:
    cid = "conv-explain-1"
    log_path = turns_log_path(tmp_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": "2026-09-16T10:00:00+00:00",
        "turn_id": "abc",
        "prompt_id": "sha256:test",
        "conversation_id": cid,
        "stopped_reason": "final",
        "success": True,
        "tools_used": ["shopping_list.list"],
        "tool_calls": [{"name": "shopping_list.list", "args": {}}],
        "steps": [],
    }
    log_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    client = _ScriptedClient()
    result = run_turn(
        client,
        [
            ChatMessage(role="user", content="what's on the shopping list?"),
            ChatMessage(role="assistant", content="Milk and eggs."),
            ChatMessage(role="user", content="Why did you do that?"),
        ],
        tools={},
        data_dir=tmp_path,
        conversation_id=cid,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert "shopping_list.list" in result.content
    assert client.calls == 0
    assert result.tools_used() == []
    assert any(s.anomaly == "explain_yourself" for s in result.steps)


def test_agent_explain_no_trace_nl(tmp_path: Path) -> None:
    client = _ScriptedClient()
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Leg uit")],
        tools={},
        data_dir=tmp_path,
        conversation_id="empty-conv",
    )
    assert result.content == no_tools_reply("nl")
    assert client.calls == 0


def test_tool_calls_summary_and_latest_trace(tmp_path: Path) -> None:
    result = TurnResult(
        content="done",
        messages=[
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="get_weather", arguments={}
                        )
                    )
                ],
            ),
            ChatMessage(role="tool", content="{}", tool_name="get_weather"),
            ChatMessage(role="assistant", content="Overcast."),
        ],
        steps=[],
        stopped_reason=StoppedReason.FINAL,
    )
    summary = tool_calls_summary(result)
    assert summary == [{"name": "get_weather", "args": {}}]

    turn_id = append_turn_trace(
        tmp_path,
        prompt_id="sha256:x",
        result=result,
        conversation_id="c1",
    )
    assert turn_id
    found = latest_trace_for_conversation(turns_log_path(tmp_path), "c1")
    assert found is not None
    assert found["tools_used"] == []  # steps empty → tools_used empty from steps
    assert found["tool_calls"] == [{"name": "get_weather", "args": {}}]
    assert latest_trace_for_conversation(turns_log_path(tmp_path), "missing") is None

    # Newer empty explain-yourself turn must not hide the prior tool turn.
    empty = TurnResult(
        content="no tools",
        messages=[ChatMessage(role="assistant", content="no tools")],
        steps=[],
        stopped_reason=StoppedReason.FINAL,
    )
    append_turn_trace(
        tmp_path, prompt_id="sha256:y", result=empty, conversation_id="c1"
    )
    again = latest_trace_for_conversation(turns_log_path(tmp_path), "c1")
    assert again is not None
    assert again["tool_calls"] == [{"name": "get_weather", "args": {}}]
