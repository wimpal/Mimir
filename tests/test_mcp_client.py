"""Tests for MCP client discovery, dispatch, logging, and degradation."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from brain.agent import StoppedReason, run_turn
from brain.config import Settings
from brain.mcp.bridge import McpBridge
from brain.mcp.errors import is_write_tool, parse_conventions_error
from brain.mcp.tasks import parse_tasks_list
from brain.mcp.log import append_mcp_tool_log, mcp_tools_log_path
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.prefs import build_system_prompt
from brain.tools import Tool, build_registry, dispatch


class _BridgeRunner:
    """Background event loop — mirrors production (sync chat, async MCP)."""

    def __init__(self, settings: Settings, servers: dict[str, MCPServer]) -> None:
        self.settings = settings
        self.servers = servers
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self.bridge: McpBridge | None = None
        self.registry: dict[str, Tool] = {}

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def __enter__(self) -> _BridgeRunner:
        self._thread.start()

        async def setup() -> McpBridge:
            bridge = await McpBridge.from_memory_servers(
                self.servers,
                settings=self.settings,
                data_dir=self.settings.runtime.data_dir,
                loop=self._loop,
            )
            self.registry = build_registry(self.settings, mcp=bridge)
            return bridge

        self.bridge = asyncio.run_coroutine_threadsafe(setup(), self._loop).result(timeout=15)
        return self

    def __exit__(self, *args: object) -> None:
        if self.bridge is not None:
            asyncio.run_coroutine_threadsafe(self.bridge.close(), self._loop).result(timeout=15)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        return dispatch(name, arguments, tools=self.registry)


def _settings(tmp_path: Path, **services) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        location={
            "latitude": 52.09,
            "longitude": 5.12,
            "timezone": "Europe/Amsterdam",
        },
        ollama={"url": "http://test", "model": "qwen3:8b"},
        runtime={"data_dir": data_dir},
        timeouts={
            "ollama_s": 30,
            "tool_s": 30,
            "turn_s": 60,
            "mcp_default_s": 10,
            "mcp_search_s": 30,
        },
        services=services,
    )


def _make_budget_server() -> MCPServer:
    mcp = MCPServer("BudgetTracker-test")

    @mcp.tool(name="budgettracker.transactions.search")
    def search(query: str = "", category: str = "") -> dict:
        """Search household expenses."""
        return {"items": [{"amount": 4200, "currency": "EUR", "category": category or query}]}

    @mcp.tool(name="budgettracker.summary.by_category")
    def summary(from_: str = "", to: str = "") -> dict:
        """Totals by category."""
        return {"rows": [{"category": "groceries", "spent": 4200, "currency": "EUR"}]}

    @mcp.tool(name="budgettracker.transactions.add")
    def add_expense(description: str, amount: int) -> dict:
        """Record expense — write tool."""
        return {"id": "tx-1", "description": description, "amount": amount}

    return mcp


def _make_retry_server() -> tuple[MCPServer, dict]:
    mcp = MCPServer("Retry-test")
    state = {"calls": 0}

    @mcp.tool(name="budgettracker.retry.probe")
    def retry_probe() -> str:
        state["calls"] += 1
        if state["calls"] == 1:
            raise ToolError(
                json.dumps(
                    {
                        "error": {
                            "code": "unavailable",
                            "message": "busy",
                            "retryable": True,
                        }
                    }
                )
            )
        return "ok"

    return mcp, state


def test_homebase_list_not_money_annotated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("Homebase-test")

    @mcp.tool(name="homebase.tasks.list")
    def tasks_list() -> list[dict[str, str]]:
        return [{"id": "c1", "title": "dweilen"}]

    with _BridgeRunner(settings, {"homebase": mcp}) as runner:
        out = runner.call("homebase.tasks.list", {})
        assert not out.startswith("Note: integer fields")
        tasks = parse_tasks_list(out)
        assert tasks is not None and len(tasks) == 1
    settings = _settings(tmp_path)
    server = _make_budget_server()
    with _BridgeRunner(settings, {"budgettracker": server}) as runner:
        registry = runner.registry
        names = set(registry) - {"get_server_time", "echo", "get_weather", "get_calendar"}
        assert "budgettracker.transactions.search" in registry
        assert "budgettracker.summary.by_category" in registry
        tool = registry["budgettracker.transactions.search"]
        assert tool.timeout_s == 30.0
        assert "Search household expenses" in tool.description
        assert tool.parameters["type"] == "object"
        assert names >= {
            "budgettracker.transactions.search",
            "budgettracker.summary.by_category",
            "budgettracker.transactions.add",
        }


def test_call_tool_logs_success(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server = _make_budget_server()
    with _BridgeRunner(settings, {"budgettracker": server}) as runner:
        out = runner.call(
            "budgettracker.transactions.search",
            {"query": "groceries", "category": "groceries"},
        )
        assert "4200" in out
        log_path = mcp_tools_log_path(settings.runtime.data_dir)
        assert log_path.is_file()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        record = json.loads(lines[-1])
        assert record["service"] == "budgettracker"
        assert record["tool"] == "budgettracker.transactions.search"
        assert record["outcome"] == "success"
        assert record["latency_ms"] >= 0


def test_domain_error_returns_error_string(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("err")

    @mcp.tool()
    def bad_tool() -> str:
        raise ToolError(
            json.dumps({"error": {"code": "not_found", "message": "missing", "retryable": False}})
        )

    with _BridgeRunner(settings, {"budgettracker": mcp}) as runner:
        out = runner.call("bad_tool", {})
        assert out.startswith("error:")
        assert "not_found" in out or "missing" in out


def test_unavailable_retries_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, _state = _make_retry_server()
    with _BridgeRunner(settings, {"budgettracker": server}) as runner:
        out = runner.call("budgettracker.retry.probe", {})
        assert out == "ok"
        log_path = mcp_tools_log_path(settings.runtime.data_dir)
        lines = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        probe_lines = [line for line in lines if line["tool"] == "budgettracker.retry.probe"]
        assert len(probe_lines) == 2
        assert probe_lines[0]["outcome"] == "error"
        assert probe_lines[0]["error_code"] == "unavailable"
        assert probe_lines[1]["outcome"] == "success"


def test_write_tool_not_retried() -> None:
    assert is_write_tool("budgettracker.transactions.add")
    assert not is_write_tool("budgettracker.transactions.search")
    assert is_write_tool("homebase.lights.set_state")
    assert not is_write_tool("homebase.lights.list")


def test_parse_conventions_error() -> None:
    text = json.dumps({"error": {"code": "unavailable", "message": "x", "retryable": True}})
    code, retry = parse_conventions_error(text)
    assert code == "unavailable"
    assert retry is True


def test_mcp_request_host_header_defaults_for_lan() -> None:
    from brain.config import McpServiceSettings, mcp_request_host_header

    lan = McpServiceSettings(host="192.168.1.142", port=8080)
    assert mcp_request_host_header(lan) == "localhost"
    loopback = McpServiceSettings(host="127.0.0.1", port=8080)
    assert mcp_request_host_header(loopback) is None


def test_connect_failure_marks_unavailable(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        budgettracker={
            "host": "127.0.0.1",
            "port": 1,
            "path": "/mcp",
            "enabled": True,
            "token": "secret",
        },
    )
    loop = asyncio.new_event_loop()

    async def _connect() -> McpBridge:
        return await McpBridge.connect(settings, data_dir=settings.runtime.data_dir, loop=loop)

    try:
        bridge = loop.run_until_complete(_connect())
        assert "budgettracker" in bridge.unavailable
        registry = build_registry(settings, mcp=bridge)
        assert "budgettracker.transactions.search" not in registry
        loop.run_until_complete(bridge.close())
    finally:
        loop.close()


def test_unavailable_services_in_system_prompt() -> None:
    prompt = build_system_prompt(
        "Base prompt.",
        {},
        unavailable_services=["budgettracker"],
    )
    assert "BudgetTracker is unreachable" in prompt
    assert "do not invent" in prompt


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self._responses = list(responses)

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        msg = self._responses.pop(0)
        return ChatResponse(message=msg)


def test_agent_loop_calls_mcp_tool(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server = _make_budget_server()
    with _BridgeRunner(settings, {"budgettracker": server}) as runner:
        client = _ScriptedClient(
            [
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(
                            function=ToolCallFunction(
                                name="budgettracker.transactions.search",
                                arguments={"query": "groceries"},
                            )
                        )
                    ],
                ),
                ChatMessage(role="assistant", content="You spent 42 on groceries."),
            ]
        )
        result = run_turn(
            client,
            [ChatMessage(role="user", content="groceries last month?")],
            tools=runner.registry,
            default_tool_timeout_s=30.0,
        )
        assert result.stopped_reason == StoppedReason.FINAL
        assert "budgettracker.transactions.search" in result.tools_used()


def test_agent_blocks_mcp_write_without_mutation_intent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server = _make_budget_server()
    with _BridgeRunner(settings, {"budgettracker": server}) as runner:
        client = _ScriptedClient(
            [
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(
                            function=ToolCallFunction(
                                name="budgettracker.transactions.add",
                                arguments={"description": "groceries", "amount": 6200},
                            )
                        )
                    ],
                ),
                ChatMessage(role="assistant", content="I did not record that."),
            ]
        )
        data_dir = settings.runtime.data_dir
        result = run_turn(
            client,
            [ChatMessage(role="user", content="what did we spend on groceries?")],
            tools=runner.registry,
            default_tool_timeout_s=30.0,
            data_dir=data_dir,
        )
        assert result.stopped_reason == StoppedReason.FINAL
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert any("write blocked" in (m.content or "") for m in tool_msgs)
        log_path = mcp_tools_log_path(data_dir)
        blocked = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(line.get("outcome") == "blocked" for line in blocked)


def test_agent_allows_mcp_write_with_mutation_intent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server = _make_budget_server()
    with _BridgeRunner(settings, {"budgettracker": server}) as runner:
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
                        )
                    ],
                ),
                ChatMessage(role="assistant", content="Recorded €62."),
            ]
        )
        data_dir = settings.runtime.data_dir
        result = run_turn(
            client,
            [ChatMessage(role="user", content="We spent €62 at the supermarket")],
            tools=runner.registry,
            default_tool_timeout_s=30.0,
            data_dir=data_dir,
        )
        assert result.stopped_reason == StoppedReason.FINAL
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert any("tx-1" in (m.content or "") for m in tool_msgs)
        assert not any("write blocked" in (m.content or "") for m in tool_msgs)


def test_append_mcp_tool_log_never_raises(tmp_path: Path) -> None:
    append_mcp_tool_log(
        tmp_path / "nope" / "deep",
        service="budgettracker",
        tool="t",
        args={},
        latency_ms=1.0,
        outcome="success",
    )
