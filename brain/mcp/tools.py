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
from brain.mcp.lights import (
    light_ambiguous_error,
    light_not_found_error,
    light_resolve_error,
    parse_lights_list,
    present_lights_set_state_batch,
    present_lights_set_state_json,
    resolve_set_state_device_ids,
    room_phrase_from_target,
    is_room_all_phrase,
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
        if name == "homebase.lights.set_state":
            raw_device_id = str(args.get("device_id", "")).strip()
            if not raw_device_id:
                return light_not_found_error("?")
            list_raw = bridge.call_tool_sync(
                "homebase.lights.list",
                {},
                timeout_s=timeout_s,
            )
            lights = parse_lights_list(list_raw)
            if lights is None:
                detail = list_raw[:200] if list_raw else "empty list response"
                return light_resolve_error(
                    "list_failed",
                    f"Could not parse lights before set_state ({detail}).",
                )
            device_ids, resolve_err = resolve_set_state_device_ids(lights, raw_device_id)
            if resolve_err:
                if resolve_err.startswith("ambiguous:"):
                    append_mcp_tool_log(
                        bridge.data_dir,
                        service=service_id,
                        tool=name,
                        args=args,
                        latency_ms=0.0,
                        outcome="error",
                        error_code="ambiguous",
                        detail=resolve_err,
                    )
                    return light_resolve_error("ambiguous", resolve_err.removeprefix("ambiguous: "))
                labels = [
                    f"{light.get('name') or '?'} ({light.get('room') or '?'})"
                    for light in lights
                ]
                append_mcp_tool_log(
                    bridge.data_dir,
                    service=service_id,
                    tool=name,
                    args=args,
                    latency_ms=0.0,
                    outcome="error",
                    error_code="not_found",
                    detail=f"{resolve_err}; known={labels}",
                )
                return light_not_found_error(raw_device_id, known=labels)
            on_value = args.get("on")
            call_base: dict[str, Any] = {"on": on_value}
            if "brightness" in args:
                call_base["brightness"] = args["brightness"]
            by_id = {str(light["id"]): light for light in lights if light.get("id")}
            batch_results: list[dict[str, Any]] = []
            last_result = ""
            for device_id in device_ids:
                call_args = {"device_id": device_id, **call_base}
                last_result = bridge.call_tool_sync(name, call_args, timeout_s=timeout_s)
                if tool_result_is_error(last_result):
                    return last_result
                light = by_id.get(device_id, {})
                batch_results.append(
                    {
                        "device_id": device_id,
                        "name": light.get("name"),
                        "room": light.get("room"),
                    }
                )
            if len(batch_results) > 1:
                room_label = (
                    room_phrase_from_target(raw_device_id)
                    if is_room_all_phrase(raw_device_id)
                    else None
                )
                return present_lights_set_state_batch(
                    batch_results, on=bool(on_value), room=room_label
                )
            single_light = by_id.get(device_ids[0], {}) if device_ids else {}
            return present_lights_set_state_json(last_result, light=single_light)
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
