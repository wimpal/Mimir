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


def turns_log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "turns.jsonl"


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
