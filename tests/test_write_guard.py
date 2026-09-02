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
    assert not user_message_requests_write("wat staat er op de boodschappenlijst?")
    assert not user_message_requests_write("wat is er op de boodschappenlijst?")
    assert not user_message_requests_write("toon de boodschappenlijst")
    assert not user_message_requests_write(
        "Vertel me in twee zinnen wat het weer is en wat er op de boodschappenlijst staat."
    )
    assert not user_message_requests_write(
        "Vertel me in twee zinnen het weer morgen en wat er op de lijst staat."
    )


def test_mutation_phrases_request_write() -> None:
    assert user_message_requests_write("Add coffee to the shopping list")
    assert user_message_requests_write("We spent €62 at the supermarket, and we're out of coffee.")
    assert user_message_requests_write("Show the list and add milk")
    assert user_message_requests_write("We used two eggs")
    assert user_message_requests_write("Set milk to 1")
    assert user_message_requests_write("Voeg koffie toe aan de boodschappenlijst")
    assert user_message_requests_write("zet melk op de boodschappenlijst")
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


def test_check_write_allowed_blocks_add_item_for_nl_list_read() -> None:
    err = check_write_allowed(
        "homebase.shopping_list.add_item",
        "wat staat er op de boodschappenlijst?",
    )
    assert err is not None
    assert "write blocked" in err


def test_check_write_allowed_ignores_read_tools() -> None:
    assert check_write_allowed(
        "homebase.inventory.list",
        "what's low on stock?",
    ) is None


def test_task_mutation_phrases_request_write() -> None:
    assert user_message_requests_write("Add a task: take out bins, due tomorrow")
    assert user_message_requests_write("Mark task abc done")
    assert user_message_requests_write("taak afvinken")
    assert user_message_requests_write("markeer stofzuigen als compleet")
    assert check_write_allowed("homebase.tasks.add", "Add a task: take out bins") is None
    assert check_write_allowed("homebase.tasks.complete", "Mark task abc done") is None
    assert check_write_allowed(
        "homebase.tasks.complete", "markeer stofzuigen als compleet"
    ) is None


def test_task_read_only_does_not_request_write() -> None:
    assert not user_message_requests_write("What tasks are due this week?")
    assert not user_message_requests_write("Welke taken zijn deze week?")


def test_lights_read_only_does_not_request_write() -> None:
    assert not user_message_requests_write("Which IKEA lights are on right now?")
    assert not user_message_requests_write("Welke lampen staan aan?")
    assert not user_message_requests_write("What lights are in the office?")


def test_lights_mutation_phrases_request_write() -> None:
    assert user_message_requests_write("Turn off Ballon")
    assert user_message_requests_write("Doe het licht uit in kantoor")
    assert user_message_requests_write("doe het licht aan in het kantoor")
    assert user_message_requests_write("zet de kantorlamp aan")
    assert user_message_requests_write("zet de kantoorlamp uit")
    assert user_message_requests_write("Show lights and turn off Ballon")
    assert check_write_allowed("homebase.lights.set_state", "Turn off Ballon") is None
    assert check_write_allowed(
        "homebase.lights.set_state", "Doe het licht uit in kantoor"
    ) is None


def test_check_write_allowed_blocks_lights_set_state_for_read() -> None:
    err = check_write_allowed(
        "homebase.lights.set_state",
        "Which IKEA lights are on right now?",
    )
    assert err is not None
    assert "write blocked" in err
    err_nl = check_write_allowed(
        "homebase.lights.set_state",
        "Welke lampen staan aan?",
    )
    assert err_nl is not None
    assert "write blocked" in err_nl


def test_party_mode_mutation_phrases_request_write() -> None:
    assert user_message_requests_write("Party mode!")
    assert user_message_requests_write("Let's party")
    assert user_message_requests_write("30 second party")
    assert user_message_requests_write("feest")
    assert user_message_requests_write("party tijd")
    assert user_message_requests_write("feestmodus")
    assert user_message_requests_write("disco")
    assert check_write_allowed("homebase.lights.party_mode", "Party mode!") is None


def test_check_write_allowed_blocks_party_mode_for_read() -> None:
    err = check_write_allowed(
        "homebase.lights.party_mode",
        "Which IKEA lights are on right now?",
    )
    assert err is not None
    assert "write blocked" in err


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


def test_agent_nudges_when_write_skipped_then_completes() -> None:
    """Follow-up complete: model must not confirm without calling the write tool."""
    complete_result = (
        "Note: ok\n"
        '{"id": "c1", "title": "stofzuigen", "completion_recorded": true}'
    )
    registry = {
        "homebase.tasks.complete": _write_tool(
            "homebase.tasks.complete",
            complete_result,
        ),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="Stofzuigen is gemarkeerd als compleet, sir.",
            ),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.tasks.complete",
                            arguments={"id": "stofzuigen"},
                        )
                    )
                ],
            ),
            ChatMessage(
                role="assistant",
                content="Stofzuigen is now marked complete, sir.",
            ),
        ]
    )
    result = run_turn(
        client,
        [
            ChatMessage(
                role="user",
                content="Dweilen is gemarkeerd als compleet, sir.",
            ),
            ChatMessage(role="assistant", content="Certainly, sir."),
            ChatMessage(role="user", content="markeer stofzuigen als compleet"),
        ],
        tools=registry,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert "homebase.tasks.complete" in result.tools_used()
    assert len(client._responses) == 0
    assert result.steps[0].anomaly == "write_skipped"
    assert result.steps[1].tool_names == ["homebase.tasks.complete"]


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
