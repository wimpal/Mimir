"""T-115 Cinema Protocol write-guard + immediate run (no M3 confirm)."""

from __future__ import annotations

import json

from brain.agent import StoppedReason, run_turn
from brain.mcp.errors import is_write_tool
from brain.mcp.protocols import (
    CINEMA_CANONICAL_NAME,
    PROTOCOL_RUN_TOOL,
    PendingProtocolStore,
    may_stage_protocol_run,
    protocol_dispatch_args,
    protocol_run_succeeded,
    resolve_protocol_name_from_message,
    user_message_requests_protocol_run,
)
from brain.mcp.write_guard import check_write_allowed, user_message_requests_write
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.tools import Tool

_PROTOCOL_PARAMS = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


def test_is_write_tool_protocol_run() -> None:
    assert is_write_tool(PROTOCOL_RUN_TOOL)
    assert not is_write_tool("homebase.protocols.list")


def test_protocol_phrases() -> None:
    assert user_message_requests_protocol_run("cinema protocol")
    assert user_message_requests_protocol_run("Cinema Protocol please")
    assert user_message_requests_protocol_run("bioscoop protocol")
    assert user_message_requests_protocol_run("start the bioscoop protocol")
    assert user_message_requests_write("cinema protocol")
    assert not user_message_requests_protocol_run("cinema")
    assert not user_message_requests_protocol_run("bioscoop")
    assert not user_message_requests_protocol_run("start jellyfin on the tv")


def test_protocol_questions_and_negations() -> None:
    assert not user_message_requests_protocol_run("what is the cinema protocol?")
    assert not user_message_requests_protocol_run("wat is het bioscoop protocol?")
    assert not user_message_requests_protocol_run("don't run the cinema protocol")
    assert not user_message_requests_protocol_run("niet het bioscoop protocol")
    assert not user_message_requests_protocol_run("never run the cinema protocol")
    assert not user_message_requests_protocol_run("no cinema protocol")
    assert not user_message_requests_protocol_run("not the bioscoop protocol")


def test_unrelated_write_blocked_on_protocol_phrase() -> None:
    err = check_write_allowed("homebase.shopping_list.add_item", "cinema protocol")
    assert err is not None
    assert "protocols.run" in err
    assert check_write_allowed(PROTOCOL_RUN_TOOL, "cinema protocol") is None


def test_resolve_canonical_name() -> None:
    assert resolve_protocol_name_from_message("cinema protocol") == CINEMA_CANONICAL_NAME
    assert resolve_protocol_name_from_message("bioscoop protocol") == CINEMA_CANONICAL_NAME
    assert resolve_protocol_name_from_message("cinema") is None


def test_may_stage_helpers_still_phrase_gated() -> None:
    assert may_stage_protocol_run("cinema protocol", has_pending=False)
    assert not may_stage_protocol_run("yes", has_pending=True)
    assert not may_stage_protocol_run("cinema", has_pending=False)


def test_protocol_run_allowed_without_pending() -> None:
    """Phrase alone unlocks protocols.run — no staged confirm required."""
    assert check_write_allowed(PROTOCOL_RUN_TOOL, "hello") is not None
    assert check_write_allowed(PROTOCOL_RUN_TOOL, "cinema protocol") is None


def test_freestyle_blocked_on_protocol_phrase() -> None:
    msg = "cinema protocol"
    for tool in (
        "homebase.devices.launch_app",
        "homebase.lights.set_state",
        "homebase.lights.party_mode",
    ):
        err = check_write_allowed(tool, msg)
        assert err is not None
        assert "protocols.run" in err


def test_protocol_run_succeeded_parse() -> None:
    assert protocol_run_succeeded(json.dumps({"success": True, "name": "Cinema"}))
    assert not protocol_run_succeeded(
        json.dumps({"success": False, "error": "already_running"})
    )
    assert not protocol_run_succeeded(
        json.dumps({"error": {"code": "already_running"}})
    )
    assert not protocol_run_succeeded("error: timeout")


def test_dispatch_args_canonical() -> None:
    assert protocol_dispatch_args({"name": "bioscoop", "extra": 1}) == {
        "name": "bioscoop"
    }
    assert protocol_dispatch_args({}) == {"name": CINEMA_CANONICAL_NAME}


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage] | None = None) -> None:
        self._responses = list(responses or [])
        self.chat_calls = 0

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        self.chat_calls += 1
        if not self._responses:
            raise AssertionError("Ollama should not be called for Protocol short-circuit")
        return ChatResponse(message=self._responses.pop(0))


def test_phrase_runs_immediately_no_confirm() -> None:
    run_calls: list[dict] = []

    def run_execute(**kwargs: object) -> str:
        run_calls.append(dict(kwargs))
        return json.dumps({"success": True, "name": "Cinema"})

    client = _ScriptedClient()
    result = run_turn(
        client,
        [ChatMessage(role="user", content="cinema protocol")],
        tools={
            PROTOCOL_RUN_TOOL: Tool(
                name=PROTOCOL_RUN_TOOL,
                description="run",
                parameters=_PROTOCOL_PARAMS,
                execute=run_execute,
            )
        },
        conversation_id="conv-protocol-immediate",
        pending_protocols=PendingProtocolStore(),
    )
    assert client.chat_calls == 0
    assert run_calls == [{"name": "Cinema"}]
    assert result.stopped_reason == StoppedReason.FINAL
    assert "Cinema" in result.content
    assert "not done yet" not in result.content.lower()
    assert "Confirm" not in result.content
    assert result.tools_used() == [PROTOCOL_RUN_TOOL]


def test_bioscoop_runs_immediately() -> None:
    run_calls: list[dict] = []

    def run_execute(**kwargs: object) -> str:
        run_calls.append(dict(kwargs))
        return json.dumps({"success": True, "name": "Cinema", "status": "ok"})

    result = run_turn(
        _ScriptedClient(),
        [ChatMessage(role="user", content="bioscoop protocol")],
        tools={
            PROTOCOL_RUN_TOOL: Tool(
                name=PROTOCOL_RUN_TOOL,
                description="run",
                parameters=_PROTOCOL_PARAMS,
                execute=run_execute,
            )
        },
        conversation_id="conv-bioscoop",
        pending_protocols=PendingProtocolStore(),
    )
    assert run_calls == [{"name": "Cinema"}]
    assert "Cinema" in result.content or "protocol" in result.content.lower()


def test_unknown_name_refused_without_phrase() -> None:
    run_calls: list[dict] = []

    def run_execute(**kwargs: object) -> str:
        run_calls.append(dict(kwargs))
        return json.dumps({"success": True})

    store = PendingProtocolStore()
    cid = "conv-protocol-unknown"
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name=PROTOCOL_RUN_TOOL,
                            arguments={"name": "Kattenbak"},
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="ok"),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="run kattenbak")],
        tools={
            PROTOCOL_RUN_TOOL: Tool(
                name=PROTOCOL_RUN_TOOL,
                description="run",
                parameters=_PROTOCOL_PARAMS,
                execute=run_execute,
            )
        },
        conversation_id=cid,
        pending_protocols=store,
        max_iterations=2,
    )
    assert run_calls == []
    assert not store.has(cid)


def test_protocol_unavailable_when_tool_missing() -> None:
    result = run_turn(
        _ScriptedClient([]),
        [ChatMessage(role="user", content="cinema protocol")],
        tools={},
        conversation_id="conv-protocol-down",
        pending_protocols=PendingProtocolStore(),
    )
    assert "Protocols" in result.content or "protocols.run" in result.content
    assert any(s.anomaly == "protocol_unavailable" for s in result.steps)


def test_failed_run_surfaces_error() -> None:
    run_calls: list[dict] = []

    def run_execute(**kwargs: object) -> str:
        run_calls.append(dict(kwargs))
        return json.dumps(
            {"success": False, "error": "TV unpaired — pair in Home network UI"}
        )

    result = run_turn(
        _ScriptedClient(),
        [ChatMessage(role="user", content="cinema protocol")],
        tools={
            PROTOCOL_RUN_TOOL: Tool(
                name=PROTOCOL_RUN_TOOL,
                description="run",
                parameters=_PROTOCOL_PARAMS,
                execute=run_execute,
            )
        },
        conversation_id="conv-protocol-fail",
        pending_protocols=PendingProtocolStore(),
    )
    assert run_calls == [{"name": "Cinema"}]
    assert "unpaired" in result.content.lower()
    assert "Protocol failed" in result.content
    assert any(s.success is False for s in result.steps)
