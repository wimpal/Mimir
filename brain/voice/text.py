"""Speech-oriented text prep before TTS synthesis."""

from __future__ import annotations

import re
from typing import Literal

Locale = Literal["nl", "en"]

# Piper [[ipa]] blocks for words espeak mangles in Dutch sentences.
_PRONUNCIATION: dict[str, dict[str, str]] = {
    "nl": {
        "sir": "[[sɜː]]",
        "Sir": "[[sɜː]]",
    },
    "en": {},
}

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)
_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MARKDOWN_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MARKDOWN_CODE = re.compile(r"`([^`]+)`")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BULLET = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_CURRENCY = re.compile(r"€\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)")
_TEMP_C = re.compile(r"(-?\d+)\s*°\s*C\b", re.IGNORECASE)
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_WHITESPACE = re.compile(r"\s+")


def _apply_pronunciation(text: str, locale: Locale) -> str:
    lex = _PRONUNCIATION.get(locale, {})
    for word, ipa in lex.items():
        text = re.sub(rf"\b{re.escape(word)}\b", ipa, text)
    return text


def _expand_currency(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        raw = match.group(1).replace(".", "").replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            return match.group(0)
        euros = int(value)
        cents = int(round((value - euros) * 100))
        if cents:
            return f"{euros} euro en {cents} cent"
        return f"{euros} euro"

    return _CURRENCY.sub(_repl, text)


def _expand_temperature(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(1)} graden Celsius"

    return _TEMP_C.sub(_repl, text)


def _expand_time(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if minute == 0:
            return f"{hour} uur"
        return f"{hour} uur {minute}"

    return _TIME.sub(_repl, text)


def prepare_text_for_speech(text: str, *, locale: Locale) -> str:
    """Normalize assistant reply text for speech synthesis."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned

    cleaned = _EMOJI_RE.sub("", cleaned)
    cleaned = _MARKDOWN_HEADING.sub("", cleaned)
    cleaned = _MARKDOWN_LINK.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_BOLD.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_ITALIC.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_CODE.sub(r"\1", cleaned)
    cleaned = cleaned.replace("_", " ")
    cleaned = _BULLET.sub("", cleaned)
    cleaned = _NUMBERED.sub("", cleaned)
    cleaned = cleaned.replace("\n\n", ". ")
    cleaned = cleaned.replace("\n", ", ")
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()

    if locale == "nl":
        cleaned = _expand_currency(cleaned)
        cleaned = _expand_temperature(cleaned)
        cleaned = _expand_time(cleaned)

    cleaned = _apply_pronunciation(cleaned, locale)

    # Ensure sentences end with punctuation for Piper sentence chunking.
    if cleaned and cleaned[-1] not in ".!?":
        cleaned += "."

    return cleaned
