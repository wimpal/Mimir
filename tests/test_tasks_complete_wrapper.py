"""Integration tests: tasks.complete wrapper must reach Homebase or fail loud."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from brain.mcp.errors import tool_result_is_error
from brain.mcp.tasks import complete_tool_succeeded
from tests.test_mcp_client import _BridgeRunner, _settings


def _make_homebase_tasks_server() -> tuple[MCPServer, dict[str, Any]]:
    mcp = MCPServer("Homebase-test")
    state: dict[str, Any] = {
        "chores": [
            {"id": "clxyz123456789012345678901", "title": "dweilen", "done": False},
        ],
        "complete_calls": [],
    }

    @mcp.tool(name="homebase.tasks.list")
    def tasks_list() -> list[dict[str, Any]]:
        return list(state["chores"])

    @mcp.tool(name="homebase.tasks.complete")
    def tasks_complete(id: str) -> dict[str, Any]:  # noqa: A002
        state["complete_calls"].append(id)
        for chore in state["chores"]:
            if chore["id"] == id:
                return {**chore, "done": False}
        raise ValueError(json.dumps({"error": {"code": "not_found", "message": "missing"}}))

    @mcp.tool(name="homebase.tasks.add")
    def tasks_add(title: str) -> dict[str, Any]:
        chore = {"id": f"c-new-{title}", "title": title, "done": False}
        state["chores"].append(chore)
        return chore

    return mcp, state


def test_complete_by_title_calls_homebase_and_succeeds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, state = _make_homebase_tasks_server()
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        out = runner.call("homebase.tasks.complete", {"id": "dweilen"})
        assert state["complete_calls"] == ["clxyz123456789012345678901"]
        assert complete_tool_succeeded(out)
        assert not tool_result_is_error(out)


def test_stale_cuid_does_not_call_homebase_complete(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, state = _make_homebase_tasks_server()
    stale = "clxyz999999999999999999999999"
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        out = runner.call("homebase.tasks.complete", {"id": stale})
        assert state["complete_calls"] == []
        assert tool_result_is_error(out)
        assert not complete_tool_succeeded(out)
        assert "dweilen" in out


def test_chore_list_json_is_not_false_success(tmp_path: Path) -> None:
    """Regression: returning the raw list body must not count as complete."""
    raw_list = json.dumps([{"id": "c1", "title": "dweilen"}])
    assert not complete_tool_succeeded(raw_list)
    assert not tool_result_is_error(raw_list)


def test_agent_marks_stale_cuid_complete_as_tool_error(tmp_path: Path) -> None:
    from brain.agent import StoppedReason, run_turn
    from brain.ollama import ChatMessage, ToolCall, ToolCallFunction
    from tests.test_mcp_client import _ScriptedClient

    settings = _settings(tmp_path)
    server, state = _make_homebase_tasks_server()
    stale = "clxyz999999999999999999999999"
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        client = _ScriptedClient(
            [
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(
                            function=ToolCallFunction(
                                name="homebase.tasks.complete",
                                arguments={"id": stale},
                            )
                        )
                    ],
                ),
                ChatMessage(role="assistant", content="Could not complete that chore."),
            ]
        )
        result = run_turn(
            client,
            [ChatMessage(role="user", content="markeer dweilen als voltooid")],
            tools=runner.registry,
            default_tool_timeout_s=30.0,
        )
        assert state["complete_calls"] == []
        assert result.stopped_reason == StoppedReason.FINAL
        assert result.steps[0].success is False
        assert result.steps[0].anomaly == "tool_error"
