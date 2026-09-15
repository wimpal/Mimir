"""Repeat last assistant reply (T-054) — no Ollama / no tools."""

from __future__ import annotations

import re
from typing import Literal, Protocol

Locale = Literal["en", "nl"]

_REPEAT_INTENT = re.compile(
    r"^(?:"
    r"repeat\s+that|say\s+that\s+again|say\s+it\s+again|one\s+more\s+time|"
    r"zeg\s+dat\s+nog\s+eens|herhaal\s+dat|nog\s+een\s+keer"
    r")(?:[\s,.!?—-]|$)",
    re.IGNORECASE,
)

_NL_REPEAT = re.compile(
    r"^(?:zeg\s+dat\s+nog\s+eens|herhaal\s+dat|nog\s+een\s+keer)",
    re.IGNORECASE,
)


class _MessageLike(Protocol):
    role: str
    content: str | None


def is_repeat_intent(text: str) -> bool:
    """True when the user asks to replay the last final assistant reply."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return bool(_REPEAT_INTENT.match(normalized))


def repeat_locale(text: str) -> Locale:
    """Infer reply locale from the repeat phrase (Dutch vs English)."""
    normalized = (text or "").strip()
    if _NL_REPEAT.match(normalized):
        return "nl"
    return "en"


def last_final_assistant(messages: list[_MessageLike]) -> str | None:
    """Last non-empty assistant content in chronological message list."""
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        content = (msg.content or "").strip()
        if content:
            return msg.content or content
    return None


def nothing_to_repeat_reply(locale: Locale) -> str:
    if locale == "nl":
        return "Er is nog niets om te herhalen."
    return "There's nothing to repeat yet."
