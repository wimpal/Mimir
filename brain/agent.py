"""Minimal tool-calling agent loop (Phase 1 proof; Phase 2 wraps with FastAPI)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from brain.mcp.errors import is_write_tool, tool_result_is_error
from brain.mcp.tasks import complete_tool_succeeded
from brain.mcp.lights import (
    build_set_state_args_from_user_message,
    light_set_state_args_from_user_message,
    set_state_tool_succeeded,
    user_message_requests_light_write,
)


def _turn_requests_light_toggle(user_message: str) -> bool:
    """True when user message specifies a lamp toggle (incl. STT compound forms)."""
    if user_message_requests_light_write(user_message):
        return True
    return light_set_state_args_from_user_message(user_message) is not None
from brain.mcp.write_guard import (
    MAX_WRITE_TOOL_NUDGES,
    check_write_allowed,
    log_blocked_write,
    user_message_requests_write,
    write_retry_nudge,
)
from brain.morning_brief import (
    calendar_events_from_payload,
    fix_morning_brief,
    is_morning_greeting,
    morning_brief_locale,
    needs_morning_brief_fixup,
)
from brain.ollama import (
    ChatMessage,
    ChatResponse,
    OllamaClient,
    OllamaError,
    ToolCall,
    ToolCallFunction,
)
from brain.tools import TOOLS, Tool, dispatch, tool_schemas

AfterToolCallback = Callable[[str, str, list[ChatMessage]], None]
OnToolStartCallback = Callable[[str, dict[str, Any] | None], None]
OnToolEndCallback = Callable[[str, bool, str], None]


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        think: bool = False,
        stream: bool = False,
    ) -> Any: ...


class StoppedReason(StrEnum):
    """Turn-level exit reasons produced by ``run_turn``."""

    FINAL = "final"
    MAX_ITERATIONS = "max_iterations"
    OLLAMA_ERROR = "ollama_error"
    EMPTY_RESPONSE = "empty_response"
    TURN_TIMEOUT = "turn_timeout"


@dataclass
class StepTrace:
    ollama_latency_ms: float
    tool_names: list[str] = field(default_factory=list)
    success: bool = True
    anomaly: str | None = None
    content_preview: str = ""
    tool_latency_ms: float | None = None
    ollama_load_ms: float | None = None
    ollama_prompt_eval_ms: float | None = None
    ollama_eval_ms: float | None = None
    ollama_prompt_tokens: int | None = None
    ollama_eval_tokens: int | None = None


@dataclass
class TurnResult:
    content: str
    messages: list[ChatMessage]
    steps: list[StepTrace]
    stopped_reason: StoppedReason
    error: str | None = None

    def tools_used(self) -> list[str]:
        names: list[str] = []
        for step in self.steps:
            names.extend(step.tool_names)
        return names


def _assistant_from_response(message: ChatMessage) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=message.content,
        tool_calls=list(message.tool_calls),
    )


def _tool_result_message(call: ToolCall, result: str) -> ChatMessage:
    return ChatMessage(
        role="tool",
        content=result,
        tool_name=call.function.name,
    )


def _remaining_s(deadline_monotonic: float | None) -> float | None:
    if deadline_monotonic is None:
        return None
    return max(0.0, deadline_monotonic - time.monotonic())


def _deadline_exceeded(deadline_monotonic: float | None) -> bool:
    return deadline_monotonic is not None and time.monotonic() >= deadline_monotonic


def _latest_user_message(messages: list[ChatMessage]) -> str:
    """Last user message in the transcript (current turn when history precedes it)."""
    for msg in reversed(messages):
        if msg.role == "user" and (msg.content or "").strip():
            return msg.content.strip()
    return ""


def _dispatch_with_timeout(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    tools: dict[str, Tool],
    timeout_s: float,
) -> str:
    """Run ``dispatch`` with a hard wall-clock cap (safety net for every tool)."""
    if timeout_s <= 0:
        return f"error: tool '{name}' timed out"

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        registry = TOOLS if tools is None else tools
        tool = registry.get(name)
        effective = timeout_s
        if tool is not None and tool.timeout_s is not None:
            effective = min(tool.timeout_s, timeout_s)

        future = pool.submit(dispatch, name, arguments, tools=tools)
        try:
            return future.result(timeout=effective)
        except FuturesTimeoutError:
            future.cancel()
            return f"error: tool '{name}' timed out"
    finally:
        # Do not wait for the timed-out worker — otherwise SSE/turns hang.
        pool.shutdown(wait=False, cancel_futures=True)


def _step_from_ollama_response(
    response: ChatResponse,
    *,
    ollama_latency_ms: float,
    **kwargs: Any,
) -> StepTrace:
    t = response.timings
    return StepTrace(
        ollama_latency_ms=ollama_latency_ms,
        ollama_load_ms=t.load_duration_ms,
        ollama_prompt_eval_ms=t.prompt_eval_duration_ms,
        ollama_eval_ms=t.eval_duration_ms,
        ollama_prompt_tokens=t.prompt_eval_count,
        ollama_eval_tokens=t.eval_count,
        **kwargs,
    )


def run_turn(
    client: ChatClient | OllamaClient,
    messages: list[ChatMessage],
    *,
    tools: dict[str, Tool] | None = None,
    max_iterations: int = 3,
    think: bool = False,
    deadline_monotonic: float | None = None,
    default_tool_timeout_s: float = 30.0,
    after_tool: AfterToolCallback | None = None,
    on_tool_start: OnToolStartCallback | None = None,
    on_tool_end: OnToolEndCallback | None = None,
    data_dir: Path | None = None,
) -> TurnResult:
    """Run one user turn through Ollama with optional tools.

    Mutates a working copy of ``messages`` (does not alter the caller's list).
    If ``deadline_monotonic`` is set (``time.monotonic()`` deadline), abort before
    the next Ollama or tool call when the budget is exhausted. Each tool call is
    also capped by ``min(default_tool_timeout_s, remaining turn budget)``.
    ``after_tool`` runs after each tool result is appended (e.g. refresh system prefs).
    ``on_tool_start`` / ``on_tool_end`` are optional observability hooks for SSE.
    """
    registry = TOOLS if tools is None else tools
    schemas = tool_schemas(registry)
    working = list(messages)
    steps: list[StepTrace] = []
    last_content = ""
    user_message = _latest_user_message(working)
    write_tool_called_this_turn = False
    lights_list_called_this_turn = False
    write_nudge_count = 0
    calendar_fallback_used = False
    calendar_events_this_turn: list[dict[str, Any]] = []
    weather_payload_this_turn: dict[str, Any] | None = None

    for _ in range(max_iterations):
        if _deadline_exceeded(deadline_monotonic):
            steps.append(
                StepTrace(
                    ollama_latency_ms=0.0,
                    success=False,
                    anomaly="turn_timeout",
                )
            )
            return TurnResult(
                content=last_content,
                messages=working,
                steps=steps,
                stopped_reason=StoppedReason.TURN_TIMEOUT,
                error="turn budget exceeded",
            )

        t0 = time.perf_counter()
        try:
            response = client.chat(working, tools=schemas, think=think, stream=False)
        except OllamaError as exc:
            latency = (time.perf_counter() - t0) * 1000
            steps.append(
                StepTrace(
                    ollama_latency_ms=latency,
                    success=False,
                    anomaly="ollama_error",
                )
            )
            return TurnResult(
                content=last_content,
                messages=working,
                steps=steps,
                stopped_reason=StoppedReason.OLLAMA_ERROR,
                error=str(exc),
            )

        ollama_latency = (time.perf_counter() - t0) * 1000
        msg = response.message
        last_content = msg.content or last_content
        tool_names = [tc.function.name for tc in msg.tool_calls]

        def _step(**kwargs: Any) -> StepTrace:
            return _step_from_ollama_response(
                response, ollama_latency_ms=ollama_latency, **kwargs
            )

        if not msg.tool_calls:
            anomaly = None
            if not (msg.content or "").strip():
                anomaly = "empty_response"
                steps.append(
                    _step(
                        tool_names=[],
                        success=False,
                        anomaly=anomaly,
                    )
                )
                working.append(_assistant_from_response(msg))
                return TurnResult(
                    content="",
                    messages=working,
                    steps=steps,
                    stopped_reason=StoppedReason.EMPTY_RESPONSE,
                )
            if (
                user_message_requests_write(user_message)
                and not write_tool_called_this_turn
                and write_nudge_count < MAX_WRITE_TOOL_NUDGES
            ):
                write_nudge_count += 1
                steps.append(
                    _step(
                        tool_names=[],
                        success=False,
                        anomaly="write_skipped",
                        content_preview=(msg.content or "")[:120],
                    )
                )
                working.append(
                    ChatMessage(role="user", content=write_retry_nudge(user_message))
                )
                continue
            if user_message_requests_write(user_message) and not write_tool_called_this_turn:
                steps.append(
                    _step(
                        tool_names=[],
                        success=False,
                        anomaly="write_skipped",
                        content_preview=(msg.content or "")[:120],
                    )
                )
                return TurnResult(
                    content=(
                        "I couldn't record that change — no write tool ran this turn. "
                        "Please try again."
                    ),
                    messages=working,
                    steps=steps,
                    stopped_reason=StoppedReason.FINAL,
                )
            if is_morning_greeting(user_message):
                events = calendar_events_this_turn
                locale = morning_brief_locale(user_message)
                reply_text = msg.content or ""
                if (
                    needs_morning_brief_fixup(
                        reply_text,
                        events,
                        locale,
                        weather=weather_payload_this_turn,
                    )
                    and not calendar_fallback_used
                ):
                    calendar_fallback_used = True
                    fixed = fix_morning_brief(
                        reply_text,
                        weather=weather_payload_this_turn,
                        events=events,
                        locale=locale,
                    )
                    steps.append(
                        _step(
                            tool_names=[],
                            success=True,
                            anomaly="morning_brief_fixup",
                            content_preview=fixed[:120],
                        )
                    )
                    working.append(ChatMessage(role="assistant", content=fixed))
                    return TurnResult(
                        content=fixed,
                        messages=working,
                        steps=steps,
                        stopped_reason=StoppedReason.FINAL,
                    )
            steps.append(
                _step(
                    tool_names=[],
                    success=True,
                    content_preview=msg.content[:120],
                )
            )
            working.append(_assistant_from_response(msg))
            return TurnResult(
                content=msg.content,
                messages=working,
                steps=steps,
                stopped_reason=StoppedReason.FINAL,
            )

        # Tool-call step
        anomaly = None
        if any(is_write_tool(name) for name in tool_names):
            write_tool_called_this_turn = True
        for tc in msg.tool_calls:
            if not isinstance(tc.function.arguments, dict):
                anomaly = "malformed_args"

        working.append(_assistant_from_response(msg))
        dispatch_failed = False
        tool_t0 = time.perf_counter()
        for idx, tc in enumerate(msg.tool_calls):
            if _deadline_exceeded(deadline_monotonic):
                dispatch_failed = True
                if anomaly is None:
                    anomaly = "turn_timeout"
                for skipped in msg.tool_calls[idx:]:
                    working.append(
                        _tool_result_message(
                            skipped,
                            f"error: tool '{skipped.function.name}' skipped (turn budget)",
                        )
                    )
                break

            remaining = _remaining_s(deadline_monotonic)
            per_tool = default_tool_timeout_s
            if remaining is not None:
                per_tool = min(per_tool, remaining)

            if on_tool_start is not None:
                on_tool_start(tc.function.name, tc.function.arguments)

            if (
                tc.function.name == "homebase.lights.set_state"
                and _turn_requests_light_toggle(user_message)
                and not lights_list_called_this_turn
                and "homebase.lights.list" in registry
            ):
                list_tc = ToolCall(
                    function=ToolCallFunction(
                        name="homebase.lights.list",
                        arguments={},
                    )
                )
                if on_tool_start is not None:
                    on_tool_start("homebase.lights.list", {})
                list_result = _dispatch_with_timeout(
                    "homebase.lights.list",
                    {},
                    tools=registry,
                    timeout_s=per_tool,
                )
                list_ok = not tool_result_is_error(list_result)
                if on_tool_end is not None:
                    preview = (
                        list_result
                        if len(list_result) <= 200
                        else list_result[:197] + "..."
                    )
                    on_tool_end("homebase.lights.list", list_ok, preview)
                working.append(_tool_result_message(list_tc, list_result))
                if after_tool is not None:
                    after_tool("homebase.lights.list", list_result, working)
                lights_list_called_this_turn = True
                tool_names.append("homebase.lights.list")
                if not list_ok:
                    dispatch_failed = True
                    if anomaly is None:
                        anomaly = "tool_error"

            write_block = check_write_allowed(tc.function.name, user_message)
            if write_block is not None:
                if data_dir is not None:
                    tool_entry = registry.get(tc.function.name)
                    log_blocked_write(
                        data_dir,
                        service=tool_entry.service if tool_entry else None,
                        tool_name=tc.function.name,
                        args=tc.function.arguments,
                    )
                result = write_block
            else:
                tool_args = dict(tc.function.arguments)
                if tc.function.name == "homebase.lights.set_state":
                    tool_args = build_set_state_args_from_user_message(
                        user_message, tool_args
                    )
                result = _dispatch_with_timeout(
                    tc.function.name,
                    tool_args,
                    tools=registry,
                    timeout_s=per_tool,
                )
            if (
                tc.function.name == "homebase.tasks.complete"
                and not tool_result_is_error(result)
                and not complete_tool_succeeded(result)
            ):
                result = (
                    "error: homebase.tasks.complete did not record a completion "
                    "(missing completion_recorded). Pass the chore title as id."
                )
            if (
                tc.function.name == "homebase.lights.set_state"
                and not tool_result_is_error(result)
                and not set_state_tool_succeeded(result)
            ):
                result = (
                    "error: homebase.lights.set_state did not succeed "
                    "(success is not true). Pass the lamp name as device_id."
                )
            ok = not tool_result_is_error(result)
            if on_tool_end is not None:
                preview = result if len(result) <= 200 else result[:197] + "..."
                on_tool_end(tc.function.name, ok, preview)

            if tool_result_is_error(result):
                dispatch_failed = True
                if anomaly is None:
                    anomaly = "turn_timeout" if "timed out" in result else "tool_error"
            working.append(_tool_result_message(tc, result))
            if after_tool is not None:
                after_tool(tc.function.name, result, working)
            if tc.function.name == "homebase.lights.list":
                lights_list_called_this_turn = True
            if tc.function.name == "get_calendar" and not tool_result_is_error(result):
                try:
                    cal_data = json.loads(result)
                    if isinstance(cal_data, dict):
                        calendar_events_this_turn = calendar_events_from_payload(cal_data)
                except (json.JSONDecodeError, TypeError):
                    pass
            if tc.function.name == "get_weather" and not tool_result_is_error(result):
                try:
                    wx_data = json.loads(result)
                    if isinstance(wx_data, dict):
                        weather_payload_this_turn = wx_data
                except (json.JSONDecodeError, TypeError):
                    pass

        if (
            _turn_requests_light_toggle(user_message)
            and not write_tool_called_this_turn
        ):
            step_tool_names = [tc.function.name for tc in msg.tool_calls]
            if (
                "homebase.lights.list" in step_tool_names
                and "homebase.lights.set_state" not in step_tool_names
            ):
                chain_args = light_set_state_args_from_user_message(user_message)
                if chain_args is not None:
                    write_tool_called_this_turn = True
                    chain_tc = ToolCall(
                        function=ToolCallFunction(
                            name="homebase.lights.set_state",
                            arguments=chain_args,
                        )
                    )
                    chain_result = _dispatch_with_timeout(
                        "homebase.lights.set_state",
                        chain_args,
                        tools=registry,
                        timeout_s=default_tool_timeout_s,
                    )
                    if (
                        not tool_result_is_error(chain_result)
                        and not set_state_tool_succeeded(chain_result)
                    ):
                        chain_result = (
                            "error: homebase.lights.set_state did not succeed "
                            "(success is not true). Pass the lamp name as device_id."
                        )
                    if on_tool_end is not None:
                        preview = (
                            chain_result
                            if len(chain_result) <= 200
                            else chain_result[:197] + "..."
                        )
                        on_tool_end("homebase.lights.set_state", not tool_result_is_error(chain_result), preview)
                    working.append(_tool_result_message(chain_tc, chain_result))
                    tool_names = [*tool_names, "homebase.lights.set_state"]

        tool_latency = (time.perf_counter() - tool_t0) * 1000
        steps.append(
            _step(
                tool_names=tool_names,
                success=anomaly is None and not dispatch_failed,
                anomaly=anomaly,
                content_preview=(msg.content or "")[:120],
                tool_latency_ms=tool_latency,
            )
        )

        if anomaly == "turn_timeout" or _deadline_exceeded(deadline_monotonic):
            return TurnResult(
                content=last_content,
                messages=working,
                steps=steps,
                stopped_reason=StoppedReason.TURN_TIMEOUT,
                error="turn budget exceeded",
            )

    return TurnResult(
        content=last_content,
        messages=working,
        steps=steps,
        stopped_reason=StoppedReason.MAX_ITERATIONS,
    )
