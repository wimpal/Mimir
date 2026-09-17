"""ELI5 / simplify mode helpers (T-055) — intent, system append, fluff strip."""

from __future__ import annotations

import re
from typing import Protocol

from brain.ollama import ChatMessage

# Broad catch: ELI5 / explain simply / Dutch equivalents (topic may follow).
_ELI5_INTENT = re.compile(
    r"(?:"
    r"\beli5\b|"
    r"explain\s+like\s+i['’]?m\s+five|"
    r"explain\s+(?:it\s+)?(?:to\s+me\s+)?simply|"
    r"explain\s+in\s+simple\s+(?:terms|language|words)|"
    r"leg\s+(?:het\s+)?uit\s+alsof\s+ik\s+vijf|"
    r"eenvoudig\s+uitgelegd|"
    r"leg\s+(?:het\s+)?eenvoudig\s+uit|"
    r"simpel\s+uitgelegd|"
    r"in\s+eenvoudige\s+(?:taal|woorden)"
    r")",
    re.IGNORECASE,
)

# Mandatory per-turn append — stronger than the base prompt section alone.
ELI5_SYSTEM_APPEND = """ACTIVE MODE THIS TURN — ELI5 / SIMPLIFY (mandatory):
- Plain short sentences. Keep Jarvis dryness: simplify the *ideas*, not the *persona*.
- Never use emoji, emoticons, or decorative symbols (no bread, sparkles, etc.).
- Never use baby-talk, mascots ("tiny superhero"), or cheerleading ("like a cloud!").
- Prefer periods over exclamation marks. No hype.
- One calm analogy is allowed only if it clarifies; otherwise say it plainly."""

# Common emoji / pictograph blocks + dingbats / misc symbols often used as fluff.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # misc symbols & pictographs, emoticons, transport, …
    "\U00002700-\U000027BF"  # dingbats (incl. ✨)
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE0F"  # variation selector
    "\U0000200D"  # ZWJ
    "]+",
)


class _MessageLike(Protocol):
    role: str
    content: str | None


def is_eli5_intent(text: str) -> bool:
    """True when the user asks for a simplified / ELI5 explanation."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return bool(_ELI5_INTENT.search(normalized))


def apply_eli5_system_append(messages: list[ChatMessage]) -> None:
    """Append mandatory ELI5 constraints to the leading system message."""
    if not messages or messages[0].role != "system":
        return
    base = messages[0].content or ""
    messages[0] = ChatMessage(
        role="system",
        content=base.rstrip() + "\n\n" + ELI5_SYSTEM_APPEND + "\n",
    )


def strip_emoji(text: str) -> str:
    """Remove emoji / decorative symbols; keep surrounding whitespace."""
    if not text:
        return text
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def sanitize_eli5_reply(text: str) -> str:
    """Strip emoji / decorative fluff the model still emits despite the prompt."""
    return strip_emoji(text).strip()


def latest_user_text(messages: list[_MessageLike]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return (msg.content or "").strip()
    return ""
