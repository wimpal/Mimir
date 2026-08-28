"""Structured MCP tool-call log — JSONL under data_dir/logs/."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("mimir.mcp.log")


def mcp_tools_log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "mcp_tools.jsonl"


def append_mcp_tool_log(
    data_dir: Path,
    *,
    service: str,
    tool: str,
    args: dict[str, Any],
    latency_ms: float,
    outcome: str,
    error_code: str | None = None,
    detail: str | None = None,
) -> None:
    """Append one JSONL record. Never raises."""
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "service": service,
        "tool": tool,
        "args": args,
        "latency_ms": round(latency_ms, 2),
        "outcome": outcome,
    }
    if error_code is not None:
        record["error_code"] = error_code
    if detail is not None:
        record["detail"] = detail[:500]

    path = mcp_tools_log_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("failed to write MCP tool log")

    logger.info(
        "mcp tool %s.%s outcome=%s latency_ms=%.1f",
        service,
        tool,
        outcome,
        latency_ms,
    )
