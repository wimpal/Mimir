"""Tests for confirmation-before-write guard."""

from __future__ import annotations

import json
from pathlib import Path

from brain.agent import StoppedReason, run_turn
from brain.mcp.log import mcp_tools_log_path
from brain.mcp.write_guard import check_write_allowed, user_message_requests_write
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.tools import Tool


def test_read_only_questions_do_not_request_write() -> None:
    assert not user_message_requests_write("what's low on stock?")
    assert not user_message_requests_write("What's on the shopping list?")
    assert not user_message_requests_write("Hoeveel hebben we uitgegeven aan boodschappen?")


def test_mutation_phrases_request_write() -> None:
    assert user_message_requests_write("Add coffee to the shopping list")
    assert user_message_requests_write("We spent €62 at the supermarket, and we're out of coffee.")
    assert user_message_requests_write("Show the list and add milk")
    assert user_message_requests_write("We used two eggs")
    assert user_message_requests_write("Set milk to 1")
    assert user_message_requests_write("Voeg koffie toe aan de boodschappenlijst")
    assert user_message_requests_write(
        "voeg een uitgave toe voor wim: boodschappen jumbo voor €19,23"
    )


def test_check_write_allowed_blocks_without_intent() -> None:
    err = check_write_allowed(
        "budgettracker.transactions.add",
        "what's low on stock?",
    )
    assert err is not None
    assert "write blocked" in err


def test_check_write_allowed_permits_with_intent() -> None:
    assert check_write_allowed(
        "homebase.shopping_list.add_item",
        "Add coffee to the shopping list",
    ) is None


def test_check_write_allowed_ignores_read_tools() -> None:
    assert check_write_allowed(
        "homebase.inventory.list",
        "what's low on stock?",
    ) is None


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self._responses = list(responses)

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        return ChatResponse(message=self._responses.pop(0))


def _write_tool(name: str, result: str) -> Tool:
    def execute(**kwargs: object) -> str:
        return result

    return Tool(
        name=name,
        description="test write",
        parameters={"type": "object", "properties": {}},
        execute=execute,
        service="homebase" if name.startswith("homebase.") else "budgettracker",
    )


def test_agent_blocks_write_on_read_only_turn(tmp_path: Path) -> None:
    registry = {
        "homebase.inventory.list": Tool(
            name="homebase.inventory.list",
            description="list inventory",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: '{"items": []}',
            service="homebase",
        ),
        "homebase.shopping_list.add_item": _write_tool(
            "homebase.shopping_list.add_item",
            '{"id": "1", "name": "coffee"}',
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
                            name="homebase.inventory.list",
                            arguments={"low_stock_only": True},
                        )
                    ),
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.shopping_list.add_item",
                            arguments={"name": "coffee"},
                        )
                    ),
                ],
            ),
            ChatMessage(role="assistant", content="Coffee is low but I did not add it."),
        ]
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result = run_turn(
        client,
        [ChatMessage(role="user", content="what's low on stock?")],
        tools=registry,
        data_dir=data_dir,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert any("write blocked" in (m.content or "") for m in tool_msgs)
    log_path = mcp_tools_log_path(data_dir)
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    blocked = [line for line in lines if line.get("outcome") == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["tool"] == "homebase.shopping_list.add_item"


def test_agent_allows_write_when_user_requested_mutation(tmp_path: Path) -> None:
    registry = {
        "budgettracker.transactions.add": _write_tool(
            "budgettracker.transactions.add",
            '{"id": "tx-1", "amount": 6200}',
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
                            name="budgettracker.transactions.add",
                            arguments={"description": "supermarket", "amount": 6200},
                        )
                    ),
                ],
            ),
            ChatMessage(role="assistant", content="Recorded €62 at the supermarket."),
        ]
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result = run_turn(
        client,
        [ChatMessage(role="user", content="We spent €62 at the supermarket")],
        tools=registry,
        data_dir=data_dir,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    tool_msgs = [m for m in result.messages if m.role == "tool"]
    assert any("tx-1" in (m.content or "") for m in tool_msgs)
    assert not any("write blocked" in (m.content or "") for m in tool_msgs)
