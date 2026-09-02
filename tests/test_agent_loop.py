"""Unit tests for agent tool loop — fake client, no live Ollama."""

from __future__ import annotations

import time
from typing import Any

from brain.agent import StoppedReason, run_turn
from brain.ollama import (
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    OllamaError,
    ToolCall,
    ToolCallFunction,
)
from brain.tools import Tool


class ScriptedClient:
    def __init__(self, responses: list[ChatMessage | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

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

    def chat_stream(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        think: bool = False,
    ):
        self.stream_calls.append({"messages": list(messages), "tools": tools, "think": think})
        if not self._responses:
            raise AssertionError("no scripted responses left")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, list):
            for item in nxt:
                if isinstance(item, ChatStreamChunk):
                    yield item
                elif isinstance(item, str):
                    yield ChatStreamChunk(delta=item, done=False)
                else:
                    raise TypeError(f"unexpected stream item: {item!r}")
            yield ChatStreamChunk(delta="", done=True)
            return
        if nxt.tool_calls:
            yield ChatStreamChunk(
                delta="",
                done=True,
                raw={"message": nxt.to_api_dict(), "done": True},
            )
            return
        text = nxt.content or ""
        if len(text) <= 1:
            yield ChatStreamChunk(
                delta=text,
                done=True,
                raw={"message": nxt.to_api_dict(), "done": True},
            )
            return
        mid = len(text) // 2
        yield ChatStreamChunk(delta=text[:mid], done=False)
        yield ChatStreamChunk(
            delta=text[mid:],
            done=True,
            raw={"message": nxt.to_api_dict(), "done": True},
        )


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


def test_agent_injects_lights_list_before_set_state() -> None:
    """Light write: brain lists before set_state when model skips list."""
    list_result = '[{"id": "k1", "name": "Ballon", "room": "Kantoor", "isOn": false}]'
    set_result = (
        "Note: ok\n"
        '{"success": true, "device_id": "k1", "on": true, "prior_isOn": false}'
    )
    calls: list[str] = []

    def _list(**_: object) -> str:
        calls.append("homebase.lights.list")
        return list_result

    def _set(**_: object) -> str:
        calls.append("homebase.lights.set_state")
        return set_result

    registry = {
        "homebase.lights.list": Tool(
            name="homebase.lights.list",
            description="list lights",
            parameters={"type": "object", "properties": {}},
            execute=_list,
            service="homebase",
        ),
        "homebase.lights.set_state": Tool(
            name="homebase.lights.set_state",
            description="set light",
            parameters={
                "type": "object",
                "properties": {
                    "device_id": {"type": "string"},
                    "on": {"type": "boolean"},
                },
            },
            execute=_set,
            service="homebase",
        ),
    }
    client = ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    _tool_call(
                        "homebase.lights.set_state",
                        {"device_id": "kantoor", "on": True},
                    ),
                ],
            ),
            ChatMessage(role="assistant", content="The office lamp is now on, sir."),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="doe het licht aan in het kantoor")],
        tools=registry,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert calls == ["homebase.lights.list", "homebase.lights.set_state"]
    assert result.tools_used().count("homebase.lights.list") == 1
    assert "homebase.lights.set_state" in result.tools_used()


def test_agent_auto_chains_set_state_for_compound_kantoorlamp() -> None:
    """STT compound kantoorlamp: list-only step still auto-chains set_state."""
    list_result = '[{"id": "k1", "name": "Ballon", "room": "Kantoor", "isOn": true}]'
    set_result = 'Note: ok\n{"success": true, "device_id": "k1", "on": false}'
    calls: list[str] = []

    def _list(**_: object) -> str:
        calls.append("homebase.lights.list")
        return list_result

    def _set(**_: object) -> str:
        calls.append("homebase.lights.set_state")
        return set_result

    registry = {
        "homebase.lights.list": Tool(
            name="homebase.lights.list",
            description="list lights",
            parameters={"type": "object", "properties": {}},
            execute=_list,
            service="homebase",
        ),
        "homebase.lights.set_state": Tool(
            name="homebase.lights.set_state",
            description="set light",
            parameters={
                "type": "object",
                "properties": {
                    "device_id": {"type": "string"},
                    "on": {"type": "boolean"},
                },
            },
            execute=_set,
            service="homebase",
        ),
    }
    client = ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[_tool_call("homebase.lights.list", {})],
            ),
            ChatMessage(role="assistant", content="Ballon is on."),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="zet de kantoorlamp uit")],
        tools=registry,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert calls == ["homebase.lights.list", "homebase.lights.set_state"]
    assert "homebase.lights.set_state" in result.tools_used()


def test_stream_final_emits_deltas() -> None:
    client = ScriptedClient([ChatMessage(role="assistant", content="Hello world")])
    deltas: list[str] = []
    result = run_turn(
        client,
        [ChatMessage(role="user", content="hi")],
        on_assistant_delta=deltas.append,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.content == "Hello world"
    assert "".join(deltas) == "Hello world"
    assert len(client.stream_calls) == 0
    assert len(client.calls) == 1


def test_first_tool_iteration_uses_blocking_chat() -> None:
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
    deltas: list[str] = []
    result = run_turn(
        client,
        [ChatMessage(role="user", content="echo hi")],
        on_assistant_delta=deltas.append,
    )
    assert result.content == "You said: hi"
    assert "".join(deltas) == "You said: hi"
    assert len(client.calls) == 1
    assert len(client.stream_calls) == 1


def test_stream_disabled_without_callback() -> None:
    client = ScriptedClient([ChatMessage(role="assistant", content="Hi")])
    run_turn(client, [ChatMessage(role="user", content="hi")])
    assert len(client.calls) == 1
    assert len(client.stream_calls) == 0
