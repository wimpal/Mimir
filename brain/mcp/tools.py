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
    DEVICE_NO_COLOUR_ERROR,
    DEVICE_NO_COLOR_TEMP_ERROR,
    args_request_color_temp,
    args_request_colour,
    capability_error_for_light,
    format_capability_failure,
    is_house_all_phrase,
    is_room_all_phrase,
    is_stale_device_id_error,
    light_ambiguous_error,
    light_not_found_error,
    light_resolve_error,
    looks_like_dirigera_device_id,
    parse_lights_list,
    present_lights_list_json,
    present_lights_set_state_batch,
    present_lights_set_state_json,
    resolve_set_state_device_ids,
    room_phrase_from_target,
    set_state_tool_succeeded,
)
from brain.tools import Tool


def _mcp_tool_timeout_s(name: str, settings: Settings) -> float:
    if name == "homebase.lights.party_mode":
        return settings.timeouts.mcp_party_s
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
        if name == "homebase.lights.list":
            list_raw = bridge.call_tool_sync(name, args, timeout_s=timeout_s)
            if tool_result_is_error(list_raw):
                return list_raw
            return present_lights_list_json(list_raw)
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
            for key in (
                "brightness",
                "color_temp_kelvin",
                "color_preset",
                "color_hex",
            ):
                if key in args and args[key] is not None:
                    call_base[key] = args[key]
            # Prefer color_preset over color_hex when both present.
            if "color_preset" in call_base and "color_hex" in call_base:
                call_base.pop("color_hex", None)
            by_id = {str(light["id"]): light for light in lights if light.get("id")}
            house_wide = is_house_all_phrase(raw_device_id)
            wants_colour = args_request_colour(call_base)
            wants_ct = args_request_color_temp(call_base)
            if len(device_ids) == 1 and (wants_colour or wants_ct):
                target_light = by_id.get(device_ids[0], {})
                cap_err = capability_error_for_light(target_light, call_base)
                if cap_err:
                    return format_capability_failure(cap_err, light=target_light)
            skipped_pre: list[dict[str, Any]] = []
            if house_wide:
                for light in lights:
                    if light.get("id") and light.get("reachable") is False:
                        skipped_pre.append(
                            {
                                "device_id": str(light["id"]),
                                "name": light.get("name"),
                                "room": light.get("room"),
                            }
                        )
            batch_results: list[dict[str, Any]] = []
            skipped_runtime: list[dict[str, Any]] = []
            last_result = ""
            stale_retried = False
            for device_id in device_ids:
                light = by_id.get(device_id, {})
                # Batch colour/CT: skip lamps that explicitly lack the capability.
                if len(device_ids) > 1 and (wants_colour or wants_ct):
                    cap_err = capability_error_for_light(light, call_base)
                    if cap_err:
                        skipped_runtime.append(
                            {
                                "device_id": device_id,
                                "name": light.get("name"),
                                "room": light.get("room"),
                                "reason": cap_err,
                            }
                        )
                        continue
                call_args = {"device_id": device_id, **call_base}
                last_result = bridge.call_tool_sync(name, call_args, timeout_s=timeout_s)
                light = by_id.get(device_id, {})
                failed = tool_result_is_error(last_result) or not set_state_tool_succeeded(
                    last_result
                )
                # Homebase: Unknown or stale device_id → fresh list + re-resolve once
                # (prefer name/room phrase; never invent an id).
                if (
                    failed
                    and not stale_retried
                    and is_stale_device_id_error(last_result)
                    and not looks_like_dirigera_device_id(raw_device_id)
                ):
                    stale_retried = True
                    list_raw = bridge.call_tool_sync(
                        "homebase.lights.list",
                        {},
                        timeout_s=timeout_s,
                    )
                    refreshed = parse_lights_list(list_raw)
                    if refreshed is not None:
                        lights = refreshed
                        by_id = {
                            str(light["id"]): light
                            for light in lights
                            if light.get("id")
                        }
                        new_ids, new_err = resolve_set_state_device_ids(
                            lights, raw_device_id
                        )
                        if not new_err and new_ids:
                            # Retry once with a freshly resolved id for this phrase.
                            retry_id = (
                                new_ids[0]
                                if len(device_ids) == 1
                                else (device_id if device_id in new_ids else new_ids[0])
                            )
                            call_args = {"device_id": retry_id, **call_base}
                            last_result = bridge.call_tool_sync(
                                name, call_args, timeout_s=timeout_s
                            )
                            device_id = retry_id
                            light = by_id.get(device_id, {})
                            failed = tool_result_is_error(
                                last_result
                            ) or not set_state_tool_succeeded(last_result)
                if failed:
                    # Hub capability errors in a batch: skip that lamp, continue others.
                    if len(device_ids) > 1 and (
                        DEVICE_NO_COLOUR_ERROR in last_result
                        or DEVICE_NO_COLOR_TEMP_ERROR in last_result
                    ):
                        skipped_runtime.append(
                            {
                                "device_id": device_id,
                                "name": light.get("name"),
                                "room": light.get("room"),
                            }
                        )
                        continue
                    if house_wide and (
                        "unreachable" in last_result.lower()
                        or "Device unreachable" in last_result
                    ):
                        skipped_runtime.append(
                            {
                                "device_id": device_id,
                                "name": light.get("name"),
                                "room": light.get("room"),
                            }
                        )
                        continue
                    # Stop the batch — do not claim success for remaining lamps.
                    return present_lights_set_state_json(last_result, light=light)
                batch_results.append(
                    {
                        "device_id": device_id,
                        "name": light.get("name"),
                        "room": light.get("room"),
                    }
                )
            skipped_all = skipped_pre + skipped_runtime
            if not batch_results and skipped_all and (wants_colour or wants_ct):
                # Every target lacked colour/CT support.
                sample = by_id.get(str(skipped_all[0].get("device_id") or ""), {})
                err = (
                    DEVICE_NO_COLOUR_ERROR
                    if wants_colour
                    else DEVICE_NO_COLOR_TEMP_ERROR
                )
                return format_capability_failure(err, light=sample or None)
            if house_wide:
                if not batch_results and skipped_all:
                    return light_resolve_error(
                        "not_found",
                        "no includable lights for house-wide set_state",
                    )
                return present_lights_set_state_batch(
                    batch_results,
                    on=bool(on_value),
                    skipped=skipped_all,
                    house_wide=True,
                )
            if len(batch_results) > 1 or (
                len(device_ids) > 1 and batch_results
            ):
                room_label = (
                    room_phrase_from_target(raw_device_id)
                    if is_room_all_phrase(raw_device_id)
                    else None
                )
                return present_lights_set_state_batch(
                    batch_results, on=bool(on_value), room=room_label, skipped=skipped_all
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
