"""Unit tests for agent tool loop — fake client, no live Ollama."""

from __future__ import annotations

import time
from typing import Any

from brain.agent import StoppedReason, run_turn
from brain.ollama import ChatMessage, ChatResponse, OllamaError, ToolCall, ToolCallFunction
from brain.tools import Tool


class ScriptedClient:
    def __init__(self, responses: list[ChatMessage | Exception]) -> None:
        self._responses = list(responses)
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


def _tool_call(name: str, arguments: dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(function=ToolCallFunction(name=name, arguments=arguments or {}))


def test_final_text_no_tools() -> None:
    client = ScriptedClient(
        [ChatMessage(role="assistant", content="Paris")]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Capital of France?")],
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.content == "Paris"
    assert len(result.steps) == 1
    assert result.steps[0].tool_names == []
    assert result.steps[0].tool_latency_ms is None


def test_tool_then_final() -> None:
    client = ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[_tool_call("echo", {"text": "hi"})],
            ),
            ChatMessage(role="assistant", content="You said: hi"),
        ]
    )
    starts: list[str] = []
    ends: list[tuple[str, bool]] = []
    result = run_turn(
        client,
        [ChatMessage(role="user", content="echo hi")],
        on_tool_start=lambda name, args: starts.append(name),
        on_tool_end=lambda name, ok, preview: ends.append((name, ok)),
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.content == "You said: hi"
    assert result.steps[0].tool_names == ["echo"]
    assert result.steps[0].tool_latency_ms is not None
    assert result.tools_used() == ["echo"]
    assert starts == ["echo"]
    assert ends == [("echo", True)]
    # Assistant tool-call + tool result + final assistant
    roles = [m.role for m in result.messages]
    assert roles.count("tool") == 1
    tool_msg = next(m for m in result.messages if m.role == "tool")
    assert tool_msg.content == "hi"
    assert tool_msg.tool_name == "echo"


def test_max_iterations() -> None:
    forever = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[_tool_call("get_server_time")],
    )
    client = ScriptedClient([forever, forever, forever, forever])
    result = run_turn(
        client,
        [ChatMessage(role="user", content="time?")],
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.MAX_ITERATIONS
    assert len(result.steps) == 3
    assert len(client.calls) == 3


def test_ollama_error_stops() -> None:
    client = ScriptedClient([OllamaError("down")])
    result = run_turn(
        client,
        [ChatMessage(role="user", content="hi")],
    )
    assert result.stopped_reason == StoppedReason.OLLAMA_ERROR
    assert result.error == "down"


def test_empty_response() -> None:
    client = ScriptedClient([ChatMessage(role="assistant", content="")])
    result = run_turn(
        client,
        [ChatMessage(role="user", content="hi")],
    )
    assert result.stopped_reason == StoppedReason.EMPTY_RESPONSE


def test_tool_dispatch_error_marks_step() -> None:
    client = ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[_tool_call("echo", {})],  # missing required text
            ),
            ChatMessage(role="assistant", content="failed"),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="echo")],
    )
    assert result.steps[0].success is False
    assert result.steps[0].anomaly == "tool_error"
    tool_msg = next(m for m in result.messages if m.role == "tool")
    assert tool_msg.content.startswith("error:")


def test_turn_timeout_before_first_call() -> None:
    client = ScriptedClient(
        [ChatMessage(role="assistant", content="should not run")]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="hi")],
        deadline_monotonic=time.monotonic() - 0.1,
    )
    assert result.stopped_reason == StoppedReason.TURN_TIMEOUT
    assert len(client.calls) == 0


def test_slow_tool_hits_tool_timeout() -> None:
    def _slow() -> str:
        time.sleep(2.0)
        return "done"

    slow = Tool(
        name="slow_tool",
        description="Sleeps",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute=_slow,
    )
    client = ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[_tool_call("slow_tool")],
            ),
            ChatMessage(role="assistant", content="should not matter"),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="go")],
        tools={"slow_tool": slow},
        default_tool_timeout_s=0.2,
    )
    # Tool error string; loop may continue to final if scripted — first step fails
    assert result.steps[0].success is False
    assert result.steps[0].anomaly in ("tool_error", "turn_timeout")
    tool_msg = next(m for m in result.messages if m.role == "tool")
    assert "timed out" in tool_msg.content


def test_turn_budget_skips_tool_after_deadline() -> None:
    def _slow() -> str:
        time.sleep(0.3)
        return "done"

    slow = Tool(
        name="slow_tool",
        description="Sleeps",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute=_slow,
    )
    client = ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[_tool_call("slow_tool")],
            ),
        ]
    )
    # Deadline expires during the tool call (tool timeout capped by remaining budget)
    result = run_turn(
        client,
        [ChatMessage(role="user", content="go")],
        tools={"slow_tool": slow},
        deadline_monotonic=time.monotonic() + 0.15,
        default_tool_timeout_s=5.0,
    )
    assert result.stopped_reason == StoppedReason.TURN_TIMEOUT
    tool_msg = next(m for m in result.messages if m.role == "tool")
    assert "timed out" in tool_msg.content or "skipped" in tool_msg.content
