"""Async MCP session for one remote (or in-memory) service."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, Tool

from brain.config import McpServiceSettings, Settings, mcp_request_host_header, mcp_service_url

logger = logging.getLogger("mimir.mcp.client")


class McpServiceSession:
    """One connected MCP client session."""

    def __init__(self, service_id: str, client: Client, stack: AsyncExitStack) -> None:
        self.service_id = service_id
        self._client = client
        self._stack = stack

    async def list_tools_all(self) -> list[Tool]:
        """Paginate tools/list until next_cursor is exhausted."""
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            page = await self._client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if cursor is None:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        return await self._client.call_tool(name, arguments)

    async def close(self) -> None:
        await self._stack.aclose()


async def open_http_session(
    service_id: str,
    svc: McpServiceSettings,
    *,
    connect_timeout_s: float = 10.0,
) -> McpServiceSession:
    """Connect over streamable HTTP with bearer auth."""
    url = mcp_service_url(service_id, svc)
    token = (svc.token or "").strip()
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    host_header = mcp_request_host_header(svc)
    if host_header is not None:
        headers["Host"] = host_header

    stack = AsyncExitStack()
    http_client = httpx2.AsyncClient(
        headers=headers,
        timeout=httpx2.Timeout(connect_timeout_s, read=connect_timeout_s + 20.0),
        follow_redirects=True,
    )
    await stack.enter_async_context(http_client)
    transport = streamable_http_client(url, http_client=http_client, terminate_on_close=True)
    client = Client(transport)
    await stack.enter_async_context(client)
    logger.info("MCP connected service=%s url=%s tools=%s", service_id, url, "pending")
    return McpServiceSession(service_id, client, stack)


async def open_memory_session(service_id: str, server: MCPServer) -> McpServiceSession:
    """In-process MCP session for tests."""
    stack = AsyncExitStack()
    client = Client(server)
    await stack.enter_async_context(client)
    return McpServiceSession(service_id, client, stack)


async def connect_configured_services(settings: Settings) -> tuple[list[McpServiceSession], list[str]]:
    """Connect every enabled service; return (sessions, unavailable_ids)."""
    sessions: list[McpServiceSession] = []
    unavailable: list[str] = []

    for service_id, svc in settings.services.items():
        if not svc.enabled:
            continue
        if not (svc.token or "").strip():
            logger.warning(
                "MCP service %s skipped: %s not set",
                service_id,
                f"{service_id.upper()}_TOKEN",
            )
            unavailable.append(service_id)
            continue
        try:
            session = await open_http_session(
                service_id,
                svc,
                connect_timeout_s=settings.timeouts.mcp_default_s,
            )
            # Prove the session works by listing tools once.
            await session.list_tools_all()
            sessions.append(session)
            logger.info("MCP service %s ready", service_id)
        except Exception as exc:  # noqa: BLE001 — degrade, never crash brain
            logger.warning("MCP service %s unavailable: %s", service_id, exc)
            unavailable.append(service_id)

    return sessions, unavailable
