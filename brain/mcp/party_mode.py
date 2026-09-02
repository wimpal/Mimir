"""Party mode intent and args for homebase.lights.party_mode (T-039)."""

from __future__ import annotations

import json
import re
from typing import Any

_MAX_DURATION_S = 60
_MIN_DURATION_S = 1

_PARTY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bparty\s+mode\b",
        r"\blet'?s\s+party\b",
        r"\bstart\s+the\s+party\b",
        r"\bdisco\s+mode\b",
        r"\bparty\s+tijd\b",
        r"\bfeestmodus\b",
        r"\bfeest\b",
        r"\bdisco\b",
        r"\b\d+\s*(?:second|seconds|sec|secs|seconde|seconden)\s+party\b",
        r"\bparty\b.*\b\d+\s*(?:second|seconds|sec|secs|seconde|seconden)\b",
    )
)

_DURATION_RE = re.compile(
    r"(\d+)\s*(?:second|seconds|sec|secs|seconde|seconden)\b",
    re.IGNORECASE,
)


def user_message_requests_party_mode(text: str) -> bool:
    """True when the user asked for party mode this turn."""
    if not (text or "").strip():
        return False
    normalized = (text or "").strip()
    return any(p.search(normalized) for p in _PARTY_PATTERNS)


def extract_party_duration_seconds(text: str) -> int | None:
    """Parse optional duration from user message; clamp 1–60."""
    match = _DURATION_RE.search(text or "")
    if not match:
        return None
    value = int(match.group(1))
    return max(_MIN_DURATION_S, min(_MAX_DURATION_S, value))


def build_party_mode_args_from_user_message(
    user_message: str,
    model_args: dict[str, Any],
) -> dict[str, Any]:
    """Merge model args with duration extracted from user message."""
    out = dict(model_args)
    extracted = extract_party_duration_seconds(user_message)
    if extracted is not None:
        out["duration_seconds"] = extracted
    elif "duration_seconds" in out:
        try:
            raw = int(float(out["duration_seconds"]))
            out["duration_seconds"] = max(_MIN_DURATION_S, min(_MAX_DURATION_S, raw))
        except (TypeError, ValueError):
            out.pop("duration_seconds", None)
    return out


def _parse_tool_json(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if not body:
        return None
    if "\n" in body and body.split("\n", 1)[0].startswith("Note:"):
        body = body.split("\n", 1)[1].strip()
    if not body.startswith("{"):
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def party_mode_tool_succeeded(result: str) -> bool:
    """True when party_mode tool output reports success."""
    parsed = _parse_tool_json(result)
    if parsed is None:
        return False
    return parsed.get("success") is True
