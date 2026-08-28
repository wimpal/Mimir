"""Parse CONVENTIONS-shaped errors from MCP tool result content."""

from __future__ import annotations

import json
import re
from typing import Any

_WRITE_SEGMENTS = (".add", ".update", ".remove", ".complete", ".create", ".append")


def is_write_tool(name: str) -> bool:
    """True when the tool name indicates a state-changing operation."""
    lower = name.lower()
    return any(seg in lower for seg in _WRITE_SEGMENTS)


def _find_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extract a JSON object from tool error text."""
    text = text.strip()
    if not text:
        return None
    # Raw JSON body (BudgetTracker domain errors).
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    # SDK wrapper: Error executing tool X: {...}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_conventions_error(text: str) -> tuple[str | None, bool]:
    """Return (error_code, retryable) from CONVENTIONS JSON in content, if any."""
    body = _find_json_object(text)
    if body is None:
        return None, False
    err = body.get("error")
    if not isinstance(err, dict):
        return None, False
    code = err.get("code")
    retryable = bool(err.get("retryable", False))
    if code == "unavailable":
        retryable = True
    code_str = str(code) if code is not None else None
    return code_str, retryable


def format_tool_result_text(content_blocks: list[Any]) -> str:
    """Flatten MCP content blocks to a single string for the model."""
    parts: list[str] = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
            continue
        data = getattr(block, "data", None)
        if isinstance(data, str) and data:
            parts.append(data)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "\n".join(parts)
