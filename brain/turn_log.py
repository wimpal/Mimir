"""Structured turn traces — JSONL under data_dir/logs/."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from brain.agent import StoppedReason, TurnResult

logger = logging.getLogger("mimir.turns")

_SUMMARY_KEYS = (
    "ts",
    "turn_id",
    "prompt_id",
    "conversation_id",
    "stopped_reason",
    "success",
    "tools_used",
)


def turns_log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "turns.jsonl"


def _step_latency_rollups(steps: list[dict[str, Any]]) -> dict[str, Any]:
    ollama_ms = [
        s["ollama_latency_ms"]
        for s in steps
        if isinstance(s.get("ollama_latency_ms"), (int, float))
    ]
    tool_ms = [
        s["tool_latency_ms"]
        for s in steps
        if isinstance(s.get("tool_latency_ms"), (int, float))
    ]
    return {
        "step_count": len(steps),
        "ollama_latency_ms_sum": round(sum(ollama_ms), 2) if ollama_ms else 0.0,
        "tool_latency_ms_sum": round(sum(tool_ms), 2) if tool_ms else 0.0,
    }


def summarize_trace(record: dict[str, Any]) -> dict[str, Any]:
    """Drop non-summary fields from a Turn trace record."""
    out = {k: record.get(k) for k in _SUMMARY_KEYS}
    steps = record.get("steps")
    if isinstance(steps, list):
        out.update(_step_latency_rollups(steps))
    else:
        out["step_count"] = 0
        out["ollama_latency_ms_sum"] = 0.0
        out["tool_latency_ms_sum"] = 0.0
    return out


def read_recent_traces(path: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Return the newest ``limit`` Turn trace summaries (oldest→newest within the window)."""
    if limit <= 0:
        return []
    if not path.is_file():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    summaries: list[dict[str, Any]] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            summaries.append(summarize_trace(record))
    if len(summaries) > limit:
        summaries = summaries[-limit:]
    return summaries


def append_turn_trace(
    data_dir: Path,
    *,
    prompt_id: str,
    result: TurnResult,
    conversation_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Append one JSONL record for a completed turn. Returns turn_id."""
    turn_id = uuid4().hex
    tools_used = result.tools_used()

    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "turn_id": turn_id,
        "prompt_id": prompt_id,
        "conversation_id": conversation_id,
        "stopped_reason": str(result.stopped_reason),
        "success": result.stopped_reason == StoppedReason.FINAL,
        "error": result.error,
        "tools_used": tools_used,
        "steps": [
            {
                "ollama_latency_ms": round(s.ollama_latency_ms, 2),
                "tool_latency_ms": (
                    None if s.tool_latency_ms is None else round(s.tool_latency_ms, 2)
                ),
                "tool_names": s.tool_names,
                "success": s.success,
                "anomaly": s.anomaly,
                "ollama_load_ms": (
                    None if s.ollama_load_ms is None else round(s.ollama_load_ms, 2)
                ),
                "ollama_prompt_eval_ms": (
                    None
                    if s.ollama_prompt_eval_ms is None
                    else round(s.ollama_prompt_eval_ms, 2)
                ),
                "ollama_eval_ms": (
                    None if s.ollama_eval_ms is None else round(s.ollama_eval_ms, 2)
                ),
                "ollama_prompt_tokens": s.ollama_prompt_tokens,
                "ollama_eval_tokens": s.ollama_eval_tokens,
            }
            for s in result.steps
        ],
    }
    if extra:
        record.update(extra)

    path = turns_log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info(
        "turn %s reason=%s tools=%s",
        turn_id,
        result.stopped_reason,
        tools_used or "-",
    )
    return turn_id
