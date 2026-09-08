"""Minimal tool-calling agent loop (Phase 1 proof; Phase 2 wraps with FastAPI)."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from brain.mcp.errors import is_write_tool, tool_result_is_error
from brain.mcp.lights import (
    build_set_state_args_from_user_message,
    format_set_state_failure_for_model,
    house_wide_set_state_args_from_user_message,
    light_set_state_args_from_user_message,
    set_state_tool_succeeded,
    user_message_requests_light_write,
)
from brain.mcp.party_mode import (
    build_party_mode_args_from_user_message,
    party_mode_tool_succeeded,
    should_reroute_party_to_house_wide,
)
from brain.mcp.tasks import complete_tool_succeeded
from brain.mcp.write_guard import (
    MAX_WRITE_TOOL_NUDGES,
    check_write_allowed,
    log_blocked_write,
    user_message_requests_write,
    write_retry_nudge,
)
from brain.morning_brief import (
    build_morning_brief_from_tools,
    calendar_events_from_payload,
    fix_morning_brief,
    is_morning_greeting,
    morning_brief_locale,
    morning_brief_tools_incomplete,
    needs_morning_brief_fixup,
)
from brain.ollama import (
    ChatMessage,
    ChatResponse,
    OllamaClient,
    OllamaError,
    OllamaTimings,
    ToolCall,
    ToolCallFunction,
    parse_message,
)
from brain.recipe_import import (
    RECIPE_ADD_TOOL,
    PendingRecipeStore,
    awaiting_confirmation_result,
    build_duplicate_rename_confirm_reply,
    build_recipe_confirm_reply,
    duplicate_title_error,
    is_bare_cancel,
    is_bare_confirm,
    may_stage_recipe_add,
    normalize_recipe_payload,
    restage_after_duplicate_title,
    should_keep_pending_recipe,
    user_message_requests_recipe_save,
)
from brain.shopping_list import filter_shopping_list_tool_result
from brain.tools import TOOLS, Tool, dispatch, tool_schemas
from brain.turn_fixup import (
    can_tool_backed_weather_shopping_reply,
    fix_weather_shopping_reply,
    needs_weather_shopping_fixup,
)


def _turn_requests_light_toggle(user_message: str) -> bool:
    """True when user message specifies a lamp toggle (incl. STT compound forms)."""
    if user_message_requests_light_write(user_message):
        return True
    return light_set_state_args_from_user_message(user_message) is not None


# Tools that may run mid-turn without clearing Ollama schemas (T-021 fetch→stage).
_READ_ONLY_TOOL_NAMES = frozenset(
    {
        "web.fetch",
        "get_weather",
        "get_calendar",
        "get_server_time",
        "echo",
        "homebase.recipes.search",
        "homebase.recipes.get",
        "homebase.inventory.list",
        "homebase.shopping_list.list",
        "homebase.tasks.list",
        "homebase.lights.list",
        "homebase.changes.list",
        "homebase.changes.get",
        "budgettracker.transactions.search",
        "budgettracker.transactions.summarize",
        "budgettracker.categories.list",
        "get_preference",
        "list_preferences",
        "recommend",
        "recently_watched",
    }
)


def _schema_tool_name(schema: dict[str, Any]) -> str:
    fn = schema.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return str(schema.get("name") or "")


def _schemas_after_tools(
    schemas: list[dict[str, Any]],
    *,
    tools_used_this_turn: list[str],
    has_tool_results: bool,
    recipe_page_fetched: bool = False,
) -> list[dict[str, Any]]:
    """Keep tools available when only read-only tools have run this turn.

    After ``web.fetch`` on a recipe-save turn, expose only ``homebase.recipes.add``
    so duplicate ``recipes.search`` cannot burn the iteration budget.
    """
    if recipe_page_fetched:
        return [s for s in schemas if _schema_tool_name(s) == RECIPE_ADD_TOOL]
    if not has_tool_results:
        return schemas
    if tools_used_this_turn and all(
        name in _READ_ONLY_TOOL_NAMES for name in tools_used_this_turn
    ):
        return schemas
    return []


AfterToolCallback = Callable[[str, str, list[ChatMessage]], None]
OnToolStartCallback = Callable[[str, dict[str, Any] | None], None]
OnToolEndCallback = Callable[[str, bool, str], None]
OnAssistantDelta = Callable[[str], None]


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


def _has_tool_results_this_turn(messages: list[ChatMessage]) -> bool:
    """True when tool results for the current user turn are already in ``messages``."""
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user" and (messages[i].content or "").strip():
            last_user_idx = i
            break
    if last_user_idx is None:
        return False
    return any(m.role == "tool" for m in messages[last_user_idx + 1 :])


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


def _step_from_timings(
    timings: OllamaTimings,
    *,
    ollama_latency_ms: float,
    **kwargs: Any,
) -> StepTrace:
    return StepTrace(
        ollama_latency_ms=ollama_latency_ms,
        ollama_load_ms=timings.load_duration_ms,
        ollama_prompt_eval_ms=timings.prompt_eval_duration_ms,
        ollama_eval_ms=timings.eval_duration_ms,
        ollama_prompt_tokens=timings.prompt_eval_count,
        ollama_eval_tokens=timings.eval_count,
        **kwargs,
    )


def _call_ollama(
    client: ChatClient | OllamaClient,
    messages: list[ChatMessage],
    schemas: list[dict[str, Any]],
    *,
    think: bool,
    stream_final: bool,
    on_assistant_delta: OnAssistantDelta | None,
    user_message: str,
    write_tool_called_this_turn: bool,
) -> ChatResponse:
    """Blocking or streaming Ollama call for one agent iteration."""
    write_pending = user_message_requests_write(user_message) and not write_tool_called_this_turn
    tools_available = bool(schemas)
    after_tools = _has_tool_results_this_turn(messages)
    can_stream = (
        stream_final
        and on_assistant_delta is not None
        and not is_morning_greeting(user_message)
        and not write_pending
        and hasattr(client, "chat_stream")
        # Tool calls are unreliable over Ollama stream — block until tools have run.
        and (not tools_available or after_tools)
    )

    if not can_stream:
        response = client.chat(messages, tools=schemas, think=think, stream=False)
        msg = response.message
        # Do not stream drafts that write_skipped / morning-brief may discard.
        if (
            on_assistant_delta is not None
            and stream_final
            and not msg.tool_calls
            and (msg.content or "").strip()
            and not is_morning_greeting(user_message)
            and not write_pending
        ):
            on_assistant_delta(msg.content)
        return response

    accumulated = ""
    final_raw: dict[str, Any] = {}
    timings = OllamaTimings()
    for chunk in client.chat_stream(messages, tools=schemas, think=think):  # type: ignore[attr-defined]
        if chunk.delta:
            accumulated += chunk.delta
            if on_assistant_delta is not None:
                on_assistant_delta(chunk.delta)
        if chunk.done:
            final_raw = chunk.raw
            timings = chunk.timings

    raw_msg = final_raw.get("message")
    if isinstance(raw_msg, dict):
        msg = parse_message(raw_msg)
    else:
        msg = ChatMessage(role="assistant", content=accumulated)

    if msg.tool_calls:
        return ChatResponse(message=msg, raw=final_raw, timings=timings)

    if not (msg.content or "").strip() and accumulated:
        msg = ChatMessage(role="assistant", content=accumulated)

    return ChatResponse(message=msg, raw=final_raw, timings=timings)


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
    on_assistant_delta: OnAssistantDelta | None = None,
    stream_final: bool = True,
    data_dir: Path | None = None,
    conversation_id: str | None = None,
    pending_recipes: PendingRecipeStore | None = None,
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
    recipe_staged_this_turn = False
    recipe_gate_handled_this_turn = False
    lights_list_called_this_turn = False
    write_nudge_count = 0
    calendar_fallback_used = False
    calendar_events_this_turn: list[dict[str, Any]] = []
    calendar_fetched_this_turn = False
    weather_payload_this_turn: dict[str, Any] | None = None
    shopping_list_fetched_this_turn = False
    shopping_list_items_this_turn: list[dict[str, Any]] = []
    tools_used_this_turn: list[str] = []
    recipe_save_turn = user_message_requests_recipe_save(user_message)
    # Greeting / search detours burn the default 3 rounds; URL import needs headroom.
    iteration_budget = (
        max(max_iterations, 5) if recipe_save_turn else max_iterations
    )

    # T-021: drop stale pending on unrelated turns (bare yes must not commit later).
    if (
        pending_recipes is not None
        and conversation_id
        and pending_recipes.has(conversation_id)
        and not should_keep_pending_recipe(user_message)
    ):
        pending_recipes.clear(conversation_id)

    # T-021: cancel pending recipe import on bare no/nee.
    if (
        pending_recipes is not None
        and conversation_id
        and is_bare_cancel(user_message)
        and pending_recipes.has(conversation_id)
    ):
        pending_recipes.clear(conversation_id)
        cancel_reply = "Ok, I won't save that recipe."
        working.append(ChatMessage(role="assistant", content=cancel_reply))
        return TurnResult(
            content=cancel_reply,
            messages=working,
            steps=steps,
            stopped_reason=StoppedReason.FINAL,
        )

    # T-021: bare yes/ja with confirmable staged candidate → auto-dispatch.
    if (
        pending_recipes is not None
        and conversation_id
        and is_bare_confirm(user_message)
        and pending_recipes.is_confirmable(conversation_id)
        and RECIPE_ADD_TOOL in registry
    ):
        payload = pending_recipes.get(conversation_id)
        assert payload is not None
        add_tc = ToolCall(
            function=ToolCallFunction(name=RECIPE_ADD_TOOL, arguments=payload)
        )
        working.append(
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[add_tc],
            )
        )
        if on_tool_start is not None:
            on_tool_start(RECIPE_ADD_TOOL, payload)
        per_tool = default_tool_timeout_s
        tool_entry = registry.get(RECIPE_ADD_TOOL)
        if tool_entry is not None and tool_entry.timeout_s is not None:
            per_tool = max(per_tool, tool_entry.timeout_s)
        remaining = _remaining_s(deadline_monotonic)
        if remaining is not None:
            per_tool = min(per_tool, remaining)
        result = _dispatch_with_timeout(
            RECIPE_ADD_TOOL,
            payload,
            tools=registry,
            timeout_s=per_tool,
        )
        # Clear pending on success; on duplicate, restage with Title (N) and
        # force a rename confirm (bare ja must work next).
        duplicate_rename_reply: str | None = None
        if not tool_result_is_error(result):
            pending_recipes.clear(conversation_id)
        elif duplicate_title_error(result):
            original_title, restaged = restage_after_duplicate_title(
                pending_recipes, conversation_id
            )
            duplicate_rename_reply = build_duplicate_rename_confirm_reply(
                original_title=original_title,
                proposed_title=str(restaged.get("title") or ""),
                user_message=user_message,
            )
        else:
            pending_recipes.clear(conversation_id)
        ok = not tool_result_is_error(result)
        if on_tool_end is not None:
            preview = result if len(result) <= 200 else result[:197] + "..."
            on_tool_end(RECIPE_ADD_TOOL, ok, preview)
        working.append(_tool_result_message(add_tc, result))
        if after_tool is not None:
            after_tool(RECIPE_ADD_TOOL, result, working)
        write_tool_called_this_turn = True
        tools_used_this_turn.append(RECIPE_ADD_TOOL)
        steps.append(
            StepTrace(
                ollama_latency_ms=0.0,
                tool_names=[RECIPE_ADD_TOOL],
                success=ok,
                anomaly=None if ok else "tool_error",
                content_preview=(result[:120] if result else ""),
            )
        )
        if duplicate_rename_reply is not None:
            working.append(
                ChatMessage(role="assistant", content=duplicate_rename_reply)
            )
            steps.append(
                StepTrace(
                    ollama_latency_ms=0.0,
                    tool_names=[],
                    success=True,
                    anomaly="recipe_duplicate_rename_forced",
                    content_preview=duplicate_rename_reply[:120],
                )
            )
            return TurnResult(
                content=duplicate_rename_reply,
                messages=working,
                steps=steps,
                stopped_reason=StoppedReason.FINAL,
            )

    for _ in range(iteration_budget):
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
        recipe_page_fetched = (
            recipe_save_turn
            and "web.fetch" in tools_used_this_turn
            and not recipe_staged_this_turn
        )
        ollama_schemas = _schemas_after_tools(
            schemas,
            tools_used_this_turn=tools_used_this_turn,
            has_tool_results=_has_tool_results_this_turn(working),
            recipe_page_fetched=recipe_page_fetched,
        )
        try:
            response = _call_ollama(
                client,
                working,
                ollama_schemas,
                think=think,
                # Avoid streaming a hallucinated "saved" draft before we force confirm copy.
                stream_final=stream_final and not recipe_staged_this_turn,
                on_assistant_delta=on_assistant_delta,
                user_message=user_message,
                write_tool_called_this_turn=write_tool_called_this_turn,
            )
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
                # T-021: empty after web.fetch — nudge extract+stage instead of dying.
                if (
                    user_message_requests_recipe_save(user_message)
                    and "web.fetch" in tools_used_this_turn
                    and not recipe_staged_this_turn
                    and not recipe_gate_handled_this_turn
                    and write_nudge_count < MAX_WRITE_TOOL_NUDGES
                ):
                    write_nudge_count += 1
                    steps.append(
                        _step(
                            tool_names=[],
                            success=False,
                            anomaly="recipe_extract_empty",
                        )
                    )
                    working.append(
                        ChatMessage(
                            role="user",
                            content=write_retry_nudge(user_message),
                        )
                    )
                    continue
                if can_tool_backed_weather_shopping_reply(
                    user_message,
                    weather=weather_payload_this_turn,
                    shopping_list_fetched=shopping_list_fetched_this_turn,
                ):
                    fixed = fix_weather_shopping_reply(
                        "",
                        user_message,
                        weather=weather_payload_this_turn,
                        shopping_list_fetched=shopping_list_fetched_this_turn,
                        shopping_items=shopping_list_items_this_turn,
                    )
                    if fixed.strip():
                        steps.append(
                            _step(
                                tool_names=[],
                                success=True,
                                anomaly="weather_shopping_fixup",
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
                # Morning greetings with empty text still need the brief tools.
                if not is_morning_greeting(user_message):
                    # After staging a recipe, never die on empty — emit confirm copy.
                    if (
                        recipe_staged_this_turn
                        and pending_recipes is not None
                        and conversation_id
                        and pending_recipes.has(conversation_id)
                    ):
                        payload = pending_recipes.get(conversation_id)
                        assert payload is not None
                        confirm = build_recipe_confirm_reply(
                            payload, user_message=user_message
                        )
                        steps.append(
                            _step(
                                tool_names=[],
                                success=True,
                                anomaly="recipe_confirm_forced",
                                content_preview=confirm[:120],
                            )
                        )
                        working.append(
                            ChatMessage(role="assistant", content=confirm)
                        )
                        return TurnResult(
                            content=confirm,
                            messages=working,
                            steps=steps,
                            stopped_reason=StoppedReason.FINAL,
                        )
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
            # T-021: after staging / gating a recipe, confirm or refuse text is expected.
            recipe_confirm_pause = (
                recipe_staged_this_turn or recipe_gate_handled_this_turn
            )
            if (
                user_message_requests_write(user_message)
                and not write_tool_called_this_turn
                and not recipe_confirm_pause
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
            if (
                user_message_requests_write(user_message)
                and not write_tool_called_this_turn
                and not recipe_confirm_pause
            ):
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
                locale = morning_brief_locale(user_message)
                reply_text = msg.content or ""
                tools_incomplete = morning_brief_tools_incomplete(
                    weather=weather_payload_this_turn,
                    calendar_fetched=calendar_fetched_this_turn,
                )
                if tools_incomplete and not calendar_fallback_used:
                    steps.append(
                        _step(
                            tool_names=[],
                            success=False,
                            anomaly="morning_brief_tools_skipped",
                            content_preview=reply_text[:120],
                        )
                    )
                    forced_names: list[str] = []
                    forced_calls: list[ToolCall] = []
                    need_weather = (
                        weather_payload_this_turn is None and "get_weather" in registry
                    )
                    need_calendar = (
                        not calendar_fetched_this_turn and "get_calendar" in registry
                    )
                    if need_weather:
                        forced_calls.append(
                            ToolCall(
                                function=ToolCallFunction(
                                    name="get_weather", arguments={}
                                )
                            )
                        )
                    if need_calendar:
                        forced_calls.append(
                            ToolCall(
                                function=ToolCallFunction(
                                    name="get_calendar", arguments={}
                                )
                            )
                        )
                    forced_dispatch_failed = False
                    if forced_calls:
                        working.append(
                            ChatMessage(
                                role="assistant",
                                content="",
                                tool_calls=forced_calls,
                            )
                        )
                        tool_t0 = time.perf_counter()
                        for idx, tc in enumerate(forced_calls):
                            if _deadline_exceeded(deadline_monotonic):
                                forced_dispatch_failed = True
                                for skipped in forced_calls[idx:]:
                                    working.append(
                                        _tool_result_message(
                                            skipped,
                                            (
                                                f"error: tool '{skipped.function.name}' "
                                                "skipped (turn budget)"
                                            ),
                                        )
                                    )
                                    forced_names.append(skipped.function.name)
                                steps.append(
                                    _step(
                                        tool_names=forced_names,
                                        success=False,
                                        anomaly="turn_timeout",
                                        tool_latency_ms=(
                                            time.perf_counter() - tool_t0
                                        )
                                        * 1000,
                                    )
                                )
                                calendar_fallback_used = True
                                fixed = build_morning_brief_from_tools(
                                    weather=weather_payload_this_turn,
                                    events=calendar_events_this_turn,
                                    locale=locale,
                                    calendar_fetched=calendar_fetched_this_turn,
                                )
                                working.append(
                                    ChatMessage(role="assistant", content=fixed)
                                )
                                return TurnResult(
                                    content=fixed,
                                    messages=working,
                                    steps=steps,
                                    stopped_reason=StoppedReason.TURN_TIMEOUT,
                                    error="turn budget exceeded",
                                )
                            name = tc.function.name
                            remaining = _remaining_s(deadline_monotonic)
                            per_tool = default_tool_timeout_s
                            tool_entry = registry.get(name)
                            if (
                                tool_entry is not None
                                and tool_entry.timeout_s is not None
                            ):
                                per_tool = max(per_tool, tool_entry.timeout_s)
                            if remaining is not None:
                                per_tool = min(per_tool, remaining)
                            if on_tool_start is not None:
                                on_tool_start(name, {})
                            result = _dispatch_with_timeout(
                                name,
                                {},
                                tools=registry,
                                timeout_s=per_tool,
                            )
                            ok = not tool_result_is_error(result)
                            if not ok:
                                forced_dispatch_failed = True
                            if on_tool_end is not None:
                                preview = (
                                    result
                                    if len(result) <= 200
                                    else result[:197] + "..."
                                )
                                on_tool_end(name, ok, preview)
                            working.append(_tool_result_message(tc, result))
                            if after_tool is not None:
                                after_tool(name, result, working)
                            forced_names.append(name)
                            if name == "get_calendar" and ok:
                                try:
                                    cal_data = json.loads(result)
                                    if isinstance(cal_data, dict):
                                        calendar_events_this_turn = (
                                            calendar_events_from_payload(cal_data)
                                        )
                                        calendar_fetched_this_turn = True
                                except (json.JSONDecodeError, TypeError):
                                    forced_dispatch_failed = True
                            if name == "get_weather" and ok:
                                try:
                                    wx_data = json.loads(result)
                                    if isinstance(wx_data, dict):
                                        weather_payload_this_turn = wx_data
                                    else:
                                        forced_dispatch_failed = True
                                except (json.JSONDecodeError, TypeError):
                                    forced_dispatch_failed = True
                        tool_latency = (time.perf_counter() - tool_t0) * 1000
                        steps.append(
                            _step(
                                tool_names=forced_names,
                                success=not forced_dispatch_failed,
                                anomaly="morning_brief_tools_forced",
                                tool_latency_ms=tool_latency,
                            )
                        )
                    calendar_fallback_used = True
                    fixed = build_morning_brief_from_tools(
                        weather=weather_payload_this_turn,
                        events=calendar_events_this_turn,
                        locale=locale,
                        calendar_fetched=calendar_fetched_this_turn,
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
                if (
                    needs_morning_brief_fixup(
                        reply_text,
                        calendar_events_this_turn,
                        locale,
                        weather=weather_payload_this_turn,
                        calendar_fetched=calendar_fetched_this_turn,
                    )
                    and not calendar_fallback_used
                ):
                    calendar_fallback_used = True
                    fixed = fix_morning_brief(
                        reply_text,
                        weather=weather_payload_this_turn,
                        events=calendar_events_this_turn,
                        locale=locale,
                        calendar_fetched=calendar_fetched_this_turn,
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
            reply_text = msg.content or ""
            if needs_weather_shopping_fixup(
                user_message,
                reply_text,
                weather=weather_payload_this_turn,
                shopping_list_fetched=shopping_list_fetched_this_turn,
                shopping_items=shopping_list_items_this_turn,
            ):
                fixed = fix_weather_shopping_reply(
                    reply_text,
                    user_message,
                    weather=weather_payload_this_turn,
                    shopping_list_fetched=shopping_list_fetched_this_turn,
                    shopping_items=shopping_list_items_this_turn,
                )
                steps.append(
                    _step(
                        tool_names=[],
                        success=True,
                        anomaly="weather_shopping_fixup",
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
            # T-021: after staging, always ask confirm — never trust "saved" hallucination.
            if (
                recipe_staged_this_turn
                and pending_recipes is not None
                and conversation_id
                and pending_recipes.has(conversation_id)
            ):
                payload = pending_recipes.get(conversation_id)
                assert payload is not None
                confirm = build_recipe_confirm_reply(
                    payload, user_message=user_message
                )
                steps.append(
                    _step(
                        tool_names=[],
                        success=True,
                        anomaly="recipe_confirm_forced",
                        content_preview=confirm[:120],
                    )
                )
                working.append(ChatMessage(role="assistant", content=confirm))
                return TurnResult(
                    content=confirm,
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
            tool_entry = registry.get(tc.function.name)
            if tool_entry is not None and tool_entry.timeout_s is not None:
                per_tool = max(per_tool, tool_entry.timeout_s)
            if remaining is not None:
                per_tool = min(per_tool, remaining)

            dispatch_name = tc.function.name
            dispatch_args: dict[str, Any] = (
                dict(tc.function.arguments)
                if isinstance(tc.function.arguments, dict)
                else {}
            )
            result_tc = tc
            # T-041: misrouted party_mode → house-wide set_state (never call party_mode).
            if (
                dispatch_name == "homebase.lights.party_mode"
                and should_reroute_party_to_house_wide(user_message)
            ):
                house_args = house_wide_set_state_args_from_user_message(user_message)
                if house_args is not None:
                    dispatch_name = "homebase.lights.set_state"
                    dispatch_args = house_args
                    result_tc = ToolCall(
                        function=ToolCallFunction(
                            name=dispatch_name,
                            arguments=dispatch_args,
                        )
                    )

            if on_tool_start is not None:
                on_tool_start(dispatch_name, dispatch_args)

            if (
                dispatch_name == "homebase.lights.set_state"
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
                tools_used_this_turn.append("homebase.lights.list")
                if not list_ok:
                    dispatch_failed = True
                    if anomaly is None:
                        anomaly = "tool_error"

            write_block = check_write_allowed(
                dispatch_name,
                user_message,
                recipe_pending=bool(
                    pending_recipes is not None
                    and conversation_id
                    and pending_recipes.is_confirmable(conversation_id)
                ),
            )
            if (
                write_block is None
                and dispatch_name == "homebase.recipes.search"
                and recipe_save_turn
                and "web.fetch" in tools_used_this_turn
                and not recipe_staged_this_turn
            ):
                # Mirror schema restriction — don't burn rounds on duplicate search.
                result = (
                    "error: skip recipes.search during URL import — call "
                    f"{RECIPE_ADD_TOOL} now with extracted title, ingredients[], "
                    "and steps[]"
                )
            elif write_block is not None:
                if data_dir is not None:
                    tool_entry = registry.get(dispatch_name)
                    log_blocked_write(
                        data_dir,
                        service=tool_entry.service if tool_entry else None,
                        tool_name=dispatch_name,
                        args=dispatch_args,
                    )
                result = write_block
            elif dispatch_name == RECIPE_ADD_TOOL and (
                pending_recipes is None or not conversation_id
            ):
                # Confirm gate requires a conversation-scoped pending store.
                result = (
                    "error: write blocked — recipe save requires a conversation "
                    f"({dispatch_name})"
                )
                recipe_gate_handled_this_turn = True
            elif (
                dispatch_name == RECIPE_ADD_TOOL
                and pending_recipes is not None
                and conversation_id
                and not is_bare_confirm(user_message)
            ):
                has_pending = pending_recipes.has(conversation_id)
                if not may_stage_recipe_add(user_message, has_pending=has_pending):
                    result = (
                        "error: write blocked — need explicit recipe save intent "
                        f"({dispatch_name})"
                    )
                    recipe_gate_handled_this_turn = True
                else:
                    # Stage for M3 confirm — do not call Homebase yet.
                    normalized, norm_err = normalize_recipe_payload(dispatch_args)
                    if norm_err is not None:
                        result = norm_err
                        recipe_gate_handled_this_turn = True
                    else:
                        assert normalized is not None
                        pending_recipes.set(conversation_id, normalized)
                        result = awaiting_confirmation_result(normalized)
                        recipe_staged_this_turn = True
                        recipe_gate_handled_this_turn = True
            else:
                tool_args = dict(dispatch_args)
                if dispatch_name == RECIPE_ADD_TOOL and is_bare_confirm(user_message):
                    if pending_recipes is not None and conversation_id:
                        stashed = pending_recipes.get(conversation_id)
                        if stashed is not None:
                            tool_args = dict(stashed)
                if dispatch_name == "homebase.lights.set_state":
                    tool_args = build_set_state_args_from_user_message(
                        user_message, tool_args
                    )
                elif dispatch_name == "homebase.lights.party_mode":
                    tool_args = build_party_mode_args_from_user_message(
                        user_message, tool_args
                    )
                result = _dispatch_with_timeout(
                    dispatch_name,
                    tool_args,
                    tools=registry,
                    timeout_s=per_tool,
                )
                if is_write_tool(dispatch_name):
                    write_tool_called_this_turn = True
                if (
                    dispatch_name == RECIPE_ADD_TOOL
                    and pending_recipes is not None
                    and conversation_id
                    and not tool_result_is_error(result)
                ):
                    pending_recipes.clear(conversation_id)
            tools_used_this_turn.append(dispatch_name)
            if (
                dispatch_name == "homebase.tasks.complete"
                and not tool_result_is_error(result)
                and not complete_tool_succeeded(result)
            ):
                result = (
                    "error: homebase.tasks.complete did not record a completion "
                    "(missing completion_recorded). Pass the chore title as id."
                )
            if (
                dispatch_name == "homebase.lights.set_state"
                and not tool_result_is_error(result)
                and not set_state_tool_succeeded(result)
            ):
                result = format_set_state_failure_for_model(result)
            if (
                dispatch_name == "homebase.lights.party_mode"
                and not tool_result_is_error(result)
                and not party_mode_tool_succeeded(result)
            ):
                result = (
                    "error: homebase.lights.party_mode did not succeed "
                    "(success is not true). Check error in tool JSON."
                )
            ok = not tool_result_is_error(result)
            if on_tool_end is not None:
                preview = result if len(result) <= 200 else result[:197] + "..."
                on_tool_end(dispatch_name, ok, preview)

            if (
                dispatch_name == "homebase.shopping_list.list"
                and not tool_result_is_error(result)
            ):
                result = filter_shopping_list_tool_result(result)

            if tool_result_is_error(result):
                dispatch_failed = True
                if anomaly is None:
                    anomaly = "turn_timeout" if "timed out" in result else "tool_error"
            working.append(_tool_result_message(result_tc, result))
            if after_tool is not None:
                after_tool(dispatch_name, result, working)
            if dispatch_name == "homebase.lights.list":
                lights_list_called_this_turn = True
            if dispatch_name != tc.function.name:
                tool_names.append(dispatch_name)
            if dispatch_name == "get_calendar" and not tool_result_is_error(result):
                try:
                    cal_data = json.loads(result)
                    if isinstance(cal_data, dict):
                        calendar_events_this_turn = calendar_events_from_payload(cal_data)
                        calendar_fetched_this_turn = True
                except (json.JSONDecodeError, TypeError):
                    pass
            if dispatch_name == "get_weather" and not tool_result_is_error(result):
                try:
                    wx_data = json.loads(result)
                    if isinstance(wx_data, dict):
                        weather_payload_this_turn = wx_data
                except (json.JSONDecodeError, TypeError):
                    pass
            if (
                dispatch_name == "homebase.shopping_list.list"
                and not tool_result_is_error(result)
            ):
                shopping_list_fetched_this_turn = True
                try:
                    sl_data = json.loads(result)
                    if isinstance(sl_data, list):
                        shopping_list_items_this_turn = [
                            item for item in sl_data if isinstance(item, dict)
                        ]
                    else:
                        shopping_list_items_this_turn = []
                except (json.JSONDecodeError, TypeError):
                    shopping_list_items_this_turn = []

        if (
            _turn_requests_light_toggle(user_message)
            and not write_tool_called_this_turn
        ):
            step_tool_names = [tc.function.name for tc in msg.tool_calls]
            dispatched_names = list(tool_names)
            if (
                (
                    "homebase.lights.list" in step_tool_names
                    or "homebase.lights.list" in dispatched_names
                )
                and "homebase.lights.set_state" not in step_tool_names
                and "homebase.lights.set_state" not in dispatched_names
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
                        chain_result = format_set_state_failure_for_model(chain_result)
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

        # T-021: after staging, skip another Ollama round (max_iterations is often 3:
        # fetch → search → add would otherwise die with MAX_ITERATIONS).
        if (
            recipe_staged_this_turn
            and pending_recipes is not None
            and conversation_id
            and pending_recipes.has(conversation_id)
        ):
            payload = pending_recipes.get(conversation_id)
            assert payload is not None
            confirm = build_recipe_confirm_reply(
                payload, user_message=user_message
            )
            working.append(ChatMessage(role="assistant", content=confirm))
            steps.append(
                StepTrace(
                    ollama_latency_ms=0.0,
                    tool_names=[],
                    success=True,
                    anomaly="recipe_confirm_forced",
                    content_preview=confirm[:120],
                )
            )
            return TurnResult(
                content=confirm,
                messages=working,
                steps=steps,
                stopped_reason=StoppedReason.FINAL,
            )

    # Exhausted iterations — still salvage a staged recipe confirm if we have one.
    if (
        recipe_staged_this_turn
        and pending_recipes is not None
        and conversation_id
        and pending_recipes.has(conversation_id)
    ):
        payload = pending_recipes.get(conversation_id)
        assert payload is not None
        confirm = build_recipe_confirm_reply(payload, user_message=user_message)
        working.append(ChatMessage(role="assistant", content=confirm))
        return TurnResult(
            content=confirm,
            messages=working,
            steps=steps,
            stopped_reason=StoppedReason.FINAL,
        )
    if (
        user_message_requests_recipe_save(user_message)
        and "web.fetch" in tools_used_this_turn
        and not recipe_staged_this_turn
    ):
        salvage = (
            "I fetched the page but ran out of room to finish extracting the recipe. "
            "Please try again — or paste the ingredients and steps and say "
            "*voeg dit recept toe*."
        )
        if re.search(
            r"\b(importeer|bewaar|recept|voeg|opslaan)\b",
            user_message,
            re.IGNORECASE,
        ):
            salvage = (
                "Ik heb de pagina opgehaald, maar kon het recept niet afronden "
                "(te veel stappen in één beurt). Probeer opnieuw, of plak de "
                "ingrediënten en stappen en zeg *voeg dit recept toe*."
            )
        working.append(ChatMessage(role="assistant", content=salvage))
        return TurnResult(
            content=salvage,
            messages=working,
            steps=steps,
            stopped_reason=StoppedReason.FINAL,
        )

    return TurnResult(
        content=last_content,
        messages=working,
        steps=steps,
        stopped_reason=StoppedReason.MAX_ITERATIONS,
    )
