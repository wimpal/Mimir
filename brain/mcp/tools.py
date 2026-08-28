"""Wrap discovered MCP tools as local Tool entries."""

from __future__ import annotations

from typing import Any

from mcp_types import Tool as McpTool

from brain.config import Settings
from brain.mcp.bridge import McpBridge
from brain.tools import Tool


def _mcp_tool_timeout_s(name: str, settings: Settings) -> float:
    if name.endswith(".search"):
        return settings.timeouts.mcp_search_s
    return settings.timeouts.mcp_default_s


def _normalize_parameters(input_schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(input_schema, dict):
        return {"type": "object", "properties": {}}
    schema = dict(input_schema)
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def mcp_tool_to_local(
    service_id: str,
    mcp_tool: McpTool,
    *,
    bridge: McpBridge,
    settings: Settings,
) -> Tool:
    """Map one MCP tool definition to a dispatchable Tool."""
    name = mcp_tool.name
    description = (mcp_tool.description or "").strip() or f"MCP tool {name}"
    parameters = _normalize_parameters(mcp_tool.input_schema)
    timeout_s = _mcp_tool_timeout_s(name, settings)

    def execute(**kwargs: Any) -> str:
        args = dict(kwargs)
        return bridge.call_tool_sync(name, args, timeout_s=timeout_s)

    return Tool(
        name=name,
        description=description,
        parameters=parameters,
        execute=execute,
        timeout_s=timeout_s,
        service=service_id,
    )


def build_mcp_tools(
    bridge: McpBridge,
    settings: Settings,
) -> dict[str, Tool]:
    """All MCP-backed tools keyed by name."""
    out: dict[str, Tool] = {}
    for service_id, mcp_tools in bridge.discovered.items():
        for mcp_tool in mcp_tools:
            local = mcp_tool_to_local(service_id, mcp_tool, bridge=bridge, settings=settings)
            out[local.name] = local
    return out
