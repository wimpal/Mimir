"""Promote strict tool-call markup in assistant content into native tool_calls.

Granite and similar tags sometimes emit XML-ish tool envelopes in ``content``
instead of (or as well as) Ollama ``tool_calls``. Only a tight allowlisted
envelope is accepted — never arbitrary prose.
"""

from __future__ import annotations

import json
import re
from typing import Any

from brain.ollama import ChatMessage, ToolCall, ToolCallFunction

# Granite / common local envelopes: <tool_call>…</tool_call> or <|tool_call|>…
_TOOL_CALL_BLOCK = re.compile(
    r"(?is)<(?:tool_call|\|tool_call\|)>\s*(.*?)\s*</(?:tool_call|\|tool_call\|)>"
)
_NAME_ARG = re.compile(
    r"(?is)<name>\s*(?P<name>[^<]+?)\s*</name>\s*"
    r"<arguments?\s*>\s*(?P<args>.*?)\s*</arguments?>"
)
_FUNCTION_EQ = re.compile(
    r"(?is)<function\s*=\s*(?P<name>[A-Za-z0-9_.]+)\s*>(?P<args>.*?)</function>"
)
_FN_JSON = re.compile(
    r"(?is)\{\s*\"(?:name|function)\"\s*:\s*\"(?P<name>[^\"]+)\"\s*,\s*"
    r"\"(?:arguments|parameters|args)\"\s*:\s*(?P<args>\{.*?\})\s*\}"
)


def content_looks_like_tool_markup(content: str) -> bool:
    text = content or ""
    lowered = text.lower()
    return (
        "<tool_call>" in lowered
        or "<|tool_call|>" in lowered
        or "</tool_call>" in lowered
        or "<function=" in lowered
    )


def _parse_args(raw: str) -> dict[str, Any] | None:
    cleaned = (raw or "").strip()
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def extract_tool_calls_from_content(
    content: str,
    *,
    allowed_names: set[str] | frozenset[str] | None,
) -> list[ToolCall]:
    """Parse strict envelopes; drop calls whose names are not in ``allowed_names``.

    When ``allowed_names`` is empty or None, return no promotions (refuse to
    invent calls when the turn offered no tools).
    """
    if not allowed_names:
        return []
    text = content or ""
    if not content_looks_like_tool_markup(text):
        return []

    found: list[ToolCall] = []

    def _append(name: str | None, args: dict[str, Any] | None) -> None:
        if not name or args is None:
            return
        if name not in allowed_names:
            return
        found.append(ToolCall(function=ToolCallFunction(name=name, arguments=args)))

    for block in _TOOL_CALL_BLOCK.finditer(text):
        inner = block.group(1)
        name = None
        args = None
        m = _NAME_ARG.search(inner)
        if m:
            name = m.group("name").strip()
            args = _parse_args(m.group("args"))
        else:
            m_fn = _FUNCTION_EQ.search(inner)
            if m_fn:
                name = m_fn.group("name").strip()
                args = _parse_args(m_fn.group("args") or "{}") or {}
            else:
                m2 = _FN_JSON.search(inner)
                if m2:
                    name = m2.group("name").strip()
                    args = _parse_args(m2.group("args"))
        _append(name, args)

    # Bare <function=name>…</function> outside a tool_call wrapper
    if not found:
        for m_fn in _FUNCTION_EQ.finditer(text):
            name = m_fn.group("name").strip()
            args = _parse_args(m_fn.group("args") or "{}") or {}
            _append(name, args)
    return found


def strip_tool_markup(content: str) -> str:
    """Remove recognized tool-call envelopes from assistant content."""
    text = _TOOL_CALL_BLOCK.sub("", content or "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_message_tool_calls(
    message: ChatMessage,
    *,
    allowed_names: set[str] | frozenset[str] | None,
) -> ChatMessage:
    """If ``tool_calls`` empty, promote allowlisted markup from content."""
    if message.tool_calls:
        if content_looks_like_tool_markup(message.content or ""):
            return ChatMessage(
                role=message.role,
                content=strip_tool_markup(message.content),
                tool_calls=list(message.tool_calls),
                tool_name=message.tool_name,
            )
        return message
    promoted = extract_tool_calls_from_content(
        message.content or "", allowed_names=allowed_names
    )
    if not promoted:
        return message
    return ChatMessage(
        role=message.role,
        content=strip_tool_markup(message.content),
        tool_calls=promoted,
        tool_name=message.tool_name,
    )
