"""MCP bridge — holds sessions, sync/async tool dispatch, discovery state."""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_types import Tool as McpTool

from brain.config import Settings
from brain.mcp.client import (
    McpServiceSession,
    connect_configured_services,
    open_memory_session,
)
from brain.mcp.errors import (
    format_tool_result_text,
    is_write_tool,
    parse_conventions_error,
)
from brain.mcp.log import append_mcp_tool_log
from brain.mcp.money import present_money_json
from brain.mcp.names import display_service_name

logger = logging.getLogger("mimir.mcp.bridge")


class McpBridge:
    """Long-lived MCP connections for the brain process."""

    def __init__(
        self,
        *,
        settings: Settings,
        data_dir: Path,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.settings = settings
        self.data_dir = data_dir
        self._loop = loop
        self._sessions: dict[str, McpServiceSession] = {}
        self._tool_service: dict[str, str] = {}
        self.discovered: dict[str, list[McpTool]] = {}
        self.unavailable: list[str] = []

    @classmethod
    async def connect(
        cls,
        settings: Settings,
        *,
        data_dir: Path,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> McpBridge:
        """Connect all configured MCP services at startup."""
        resolved_loop = loop or asyncio.get_running_loop()
        bridge = cls(settings=settings, data_dir=data_dir, loop=resolved_loop)
        sessions, unavailable = await connect_configured_services(settings)
        bridge.unavailable = unavailable
        for session in sessions:
            bridge._sessions[session.service_id] = session
            try:
                tools = await session.list_tools_all()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MCP tool discovery failed for %s: %s",
                    session.service_id,
                    exc,
                )
                bridge.unavailable.append(session.service_id)
                await session.close()
                bridge._sessions.pop(session.service_id, None)
                continue
            bridge.discovered[session.service_id] = tools
            for tool in tools:
                bridge._tool_service[tool.name] = session.service_id
            logger.info(
                "MCP discovered service=%s tool_count=%d",
                session.service_id,
                len(tools),
            )
        return bridge

    @classmethod
    async def from_memory_servers(
        cls,
        servers: dict[str, MCPServer],
        *,
        settings: Settings,
        data_dir: Path,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> McpBridge:
        """Test helper: connect to in-process MCPServer instances."""
        resolved_loop = loop or asyncio.get_running_loop()
        bridge = cls(settings=settings, data_dir=data_dir, loop=resolved_loop)
        for service_id, server in servers.items():
            session = await open_memory_session(service_id, server)
            bridge._sessions[service_id] = session
            tools = await session.list_tools_all()
            bridge.discovered[service_id] = tools
            for tool in tools:
                bridge._tool_service[tool.name] = service_id
        return bridge

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                logger.exception("closing MCP session %s", session.service_id)
        self._sessions.clear()
        self._tool_service.clear()
        self.discovered.clear()

    def call_tool_sync(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
    ) -> str:
        """Execute an MCP tool from the sync agent loop."""
        service_id = self._tool_service.get(tool_name)
        if service_id is None:
            return f"error: unknown MCP tool '{tool_name}'"

        coro = self._call_tool_async(service_id, tool_name, arguments, timeout_s=timeout_s)
        wall_timeout = timeout_s + 5.0

        def _on_loop_thread() -> str:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=wall_timeout)

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            try:
                return pool.submit(_on_loop_thread).result(timeout=wall_timeout + 1.0)
            except FuturesTimeoutError:
                append_mcp_tool_log(
                    self.data_dir,
                    service=service_id,
                    tool=tool_name,
                    args=arguments,
                    latency_ms=timeout_s * 1000,
                    outcome="timeout",
                )
                return f"error: tool '{tool_name}' timed out"
            except Exception as exc:  # noqa: BLE001
                append_mcp_tool_log(
                    self.data_dir,
                    service=service_id,
                    tool=tool_name,
                    args=arguments,
                    latency_ms=0.0,
                    outcome="error",
                    detail=str(exc),
                )
                return f"error: tool '{tool_name}' failed: {exc}"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    async def _call_tool_async(
        self,
        service_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
    ) -> str:
        session = self._sessions.get(service_id)
        if session is None:
            return f"error: service '{service_id}' is not connected"

        allow_retry = not is_write_tool(tool_name)
        attempts = 2 if allow_retry else 1
        last_text = ""

        for attempt in range(attempts):
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=timeout_s,
                )
            except TimeoutError:
                latency_ms = (time.perf_counter() - t0) * 1000
                append_mcp_tool_log(
                    self.data_dir,
                    service=service_id,
                    tool=tool_name,
                    args=arguments,
                    latency_ms=latency_ms,
                    outcome="timeout",
                )
                return f"error: tool '{tool_name}' timed out"
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - t0) * 1000
                append_mcp_tool_log(
                    self.data_dir,
                    service=service_id,
                    tool=tool_name,
                    args=arguments,
                    latency_ms=latency_ms,
                    outcome="error",
                    detail=str(exc),
                )
                return f"error: tool '{tool_name}' failed: {exc}"

            latency_ms = (time.perf_counter() - t0) * 1000
            text = format_tool_result_text(result.content)
            last_text = text

            if not result.is_error:
                append_mcp_tool_log(
                    self.data_dir,
                    service=service_id,
                    tool=tool_name,
                    args=arguments,
                    latency_ms=latency_ms,
                    outcome="success",
                )
                return present_money_json(text) if text else "{}"

            error_code, retryable = parse_conventions_error(text)
            append_mcp_tool_log(
                self.data_dir,
                service=service_id,
                tool=tool_name,
                args=arguments,
                latency_ms=latency_ms,
                outcome="error",
                error_code=error_code,
                detail=text[:200] if text else None,
            )

            if (
                attempt == 0
                and allow_retry
                and error_code == "unavailable"
                and retryable
            ):
                logger.info("retrying MCP tool %s after unavailable", tool_name)
                continue

            return f"error: {text}" if text else f"error: tool '{tool_name}' failed"

        return f"error: {last_text}" if last_text else f"error: tool '{tool_name}' failed"
