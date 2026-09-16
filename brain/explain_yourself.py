"""Explain-yourself short-circuit (T-055) — tool-trace summary, no CoT / no Ollama."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Protocol

Locale = Literal["en", "nl"]

# Leading/standalone only — "Leg uit fotosynthese" stays prompt ELI5, not this path.
_EXPLAIN_INTENT = re.compile(
    r"^(?:"
    r"why\s+did\s+you\s+do\s+that|"
    r"explain\s+yourself|"
    r"why\s+those\s+tools|"
    r"why\s+did\s+you\s+(?:call|use)\s+(?:those\s+)?tools|"
    r"waarom\s+deed\s+je\s+dat|"
    r"waarom\s+die\s+tools|"
    r"leg\s+uit"
    r")(?:[\s,.!?—-]*)$",
    re.IGNORECASE,
)

_NL_EXPLAIN = re.compile(
    r"^(?:waarom\s+deed\s+je\s+dat|waarom\s+die\s+tools|leg\s+uit)",
    re.IGNORECASE,
)


class _MessageLike(Protocol):
    role: str
    content: str | None


def is_explain_yourself_intent(text: str) -> bool:
    """True when the user asks why the last tool turn happened (standalone)."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return bool(_EXPLAIN_INTENT.match(normalized))


def explain_locale(text: str) -> Locale:
    """Infer reply locale from the explain phrase (Dutch vs English)."""
    normalized = (text or "").strip()
    if _NL_EXPLAIN.match(normalized):
        return "nl"
    return "en"


def previous_user_message(messages: list[_MessageLike]) -> str | None:
    """User message before the current explain ask (chronological list)."""
    users: list[str] = []
    for msg in messages:
        if msg.role != "user":
            continue
        content = (msg.content or "").strip()
        if content:
            users.append(content)
    if len(users) < 2:
        return None
    return users[-2]


def _truncate(text: str, *, limit: int = 80) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _format_tool_label(name: str, args: dict[str, Any] | None) -> str:
    if not args:
        return name
    # Compact args for a one-line cite — skip empty values.
    parts: list[str] = []
    for key, value in args.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        parts.append(f"{key}={value!r}")
        if len(parts) >= 2:
            break
    if not parts:
        return name
    return f"{name}({', '.join(parts)})"


def _tool_labels_from_trace(trace: dict[str, Any] | None) -> list[str]:
    if not trace:
        return []
    calls = trace.get("tool_calls")
    labels: list[str] = []
    if isinstance(calls, list):
        for item in calls:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            raw_args = item.get("args")
            args = raw_args if isinstance(raw_args, dict) else None
            labels.append(_format_tool_label(name, args))
        if labels:
            return labels
    used = trace.get("tools_used")
    if isinstance(used, list):
        return [str(n) for n in used if isinstance(n, str) and n]
    return []


def no_tools_reply(locale: Locale) -> str:
    if locale == "nl":
        return "Ik heb bij de vorige beurt geen tools gebruikt."
    return "I didn't use any tools on the last turn."


def build_explain_reply(
    *,
    locale: Locale,
    tool_labels: list[str],
    prior_user: str | None,
) -> str:
    """Short rationale from tool-trace metadata — never a CoT dump."""
    if not tool_labels:
        return no_tools_reply(locale)

    joined = ", ".join(tool_labels)
    topic = _truncate(prior_user) if prior_user else None

    if locale == "nl":
        if topic:
            return (
                f"Ik gebruikte {joined} voor je vorige vraag over \"{topic}\"."
            )
        return f"Ik gebruikte {joined} bij de vorige beurt."

    if topic:
        return f"I used {joined} for your last request about \"{topic}\"."
    return f"I used {joined} on the last turn."


def explain_yourself_reply(
    *,
    user_text: str,
    messages: list[_MessageLike],
    data_dir: Path | None,
    conversation_id: str | None,
) -> str:
    """Resolve last-turn tools from JSONL and format the short-circuit reply."""
    from brain.turn_log import latest_trace_for_conversation, turns_log_path

    locale = explain_locale(user_text)
    prior_user = previous_user_message(messages)
    trace: dict[str, Any] | None = None
    if data_dir is not None and conversation_id:
        trace = latest_trace_for_conversation(
            turns_log_path(data_dir), conversation_id
        )
    labels = _tool_labels_from_trace(trace)
    return build_explain_reply(
        locale=locale, tool_labels=labels, prior_user=prior_user
    )
