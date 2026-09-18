"""usage_stats — aggregate turn traces (T-058). Read-only; no sibling SoT."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from brain.config import Settings
from brain.tools import Tool
from brain.turn_log import aggregate_usage, count_voice_stt, turns_log_path
from brain.voice.log import voice_log_path


def _execute_usage_stats(
    *,
    data_dir: Path,
    days: int | None = None,
    now: datetime | None = None,
) -> str:
    if days is not None:
        try:
            days_int = int(days)
        except (TypeError, ValueError):
            return "error: usage stats unavailable (invalid days)"
        if days_int <= 0:
            return "error: usage stats unavailable (days must be positive)"
        days = days_int

    clock = now if now is not None else datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)

    path = turns_log_path(data_dir)
    payload: dict[str, Any] = aggregate_usage(path, days=days, now=clock)

    since = None
    if days is not None and days > 0:
        since = clock - timedelta(days=days)
    voice_path = voice_log_path(data_dir)
    if voice_path.is_file():
        payload["voice_stt_count"] = count_voice_stt(voice_path, since=since)
        payload["voice_note"] = (
            "voice_stt_count is STT operations, not chat turns"
        )

    return json.dumps(payload, separators=(",", ":"))


def make_usage_stats_tool(
    settings: Settings,
    *,
    data_dir: Path | None = None,
    now: datetime | None = None,
) -> Tool:
    resolved = (
        data_dir if data_dir is not None else Path(settings.runtime.data_dir)
    )

    def execute(*, days: int | None = None) -> str:
        return _execute_usage_stats(data_dir=resolved, days=days, now=now)

    return Tool(
        name="usage_stats",
        description=(
            "Return how busy Mimir has been from real turn traces: turn_count, "
            "tool_call_count, success/failed, top tools, optional milestones. "
            "Use when the user asks how busy you have been / hoe druk / usage "
            "stats. Cite the returned numbers in a short natural-language reply; "
            "never paste the raw JSON to the user. Optional days window "
            "(omit for all time)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Only count turns from the last N days (omit = all time)."
                    ),
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
    )


def usage_stats_tools(
    settings: Settings,
    *,
    data_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Tool]:
    tool = make_usage_stats_tool(settings, data_dir=data_dir, now=now)
    return {tool.name: tool}
