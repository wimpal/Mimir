"""Wrap discovered MCP tools as local Tool entries."""

from __future__ import annotations

from typing import Any

from mcp_types import Tool as McpTool

from brain.config import Settings
from brain.mcp.bridge import McpBridge
from brain.mcp.errors import tool_result_is_error
from brain.mcp.log import append_mcp_tool_log
from brain.mcp.tasks import (
    chore_not_found_error,
    chore_resolve_error,
    parse_tasks_list,
    present_task_complete_json,
    resolve_complete_ids,
)
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
        if name == "homebase.tasks.complete":
            raw_id = str(args.get("id", "")).strip()
            if not raw_id:
                return chore_not_found_error("?")
            list_raw = bridge.call_tool_sync(
                "homebase.tasks.list",
                {},
                timeout_s=timeout_s,
            )
            tasks = parse_tasks_list(list_raw)
            if tasks is None:
                detail = list_raw[:200] if list_raw else "empty list response"
                return chore_resolve_error(
                    "list_failed",
                    f"Could not parse active chores before complete ({detail}).",
                )
            chore_ids = resolve_complete_ids(tasks, raw_id)
            if not chore_ids:
                titles = [
                    str(t.get("title") or "")
                    for t in tasks
                    if (t.get("title") or "").strip()
                ]
                append_mcp_tool_log(
                    bridge.data_dir,
                    service=service_id,
                    tool=name,
                    args=args,
                    latency_ms=0.0,
                    outcome="error",
                    error_code="not_found",
                    detail=f"no active chore for {raw_id!r}; active={titles}",
                )
                return chore_not_found_error(raw_id, active_titles=titles)
            last_result = ""
            for chore_id in chore_ids:
                last_result = bridge.call_tool_sync(
                    name, {"id": chore_id}, timeout_s=timeout_s
                )
                if tool_result_is_error(last_result):
                    return last_result
            return present_task_complete_json(last_result)
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
