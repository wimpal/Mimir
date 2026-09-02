"""Sentence boundary detection for streaming TTS (T-029)."""

from __future__ import annotations

import re

_ABBREVIATIONS = (
    "dhr",
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "e.g",
    "i.e",
    "bijv",
    "nr",
    "etc",
    "b.v",
    "vgl",
    "o.a",
)

_PUNCT_BOUNDARY = re.compile(r"[.!?…]+|\.\.\.")
_DECIMAL_COMMA = re.compile(r"\d,\d")


class SentenceBuffer:
    """Accumulate streaming text; emit completed speakable sentences."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        if not text:
            return []
        self._buffer += text
        return self._extract_complete()

    def flush(self) -> str | None:
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None

    def _extract_complete(self) -> list[str]:
        sentences: list[str] = []
        while self._buffer:
            boundary = self._next_boundary_index(self._buffer)
            if boundary is None:
                break
            sentence = self._buffer[: boundary + 1].strip()
            self._buffer = self._buffer[boundary + 1 :].lstrip()
            if sentence:
                sentences.append(sentence)
        return sentences

    def _next_boundary_index(self, text: str) -> int | None:
        for match in _PUNCT_BOUNDARY.finditer(text):
            end = match.end()
            start = match.start()
            # Boundary must be followed by whitespace or end of buffer.
            if end < len(text) and text[end] not in " \n\t":
                continue
            before = text[:start]
            token_before = before.rsplit(None, 1)[-1] if before.strip() else ""
            if self._token_is_abbreviation(token_before):
                continue
            return end - 1
        return None

    @staticmethod
    def _token_is_abbreviation(token: str) -> bool:
        lowered = token.lower().rstrip(".")
        for abbr in _ABBREVIATIONS:
            if lowered == abbr or lowered.endswith(abbr):
                return True
        return False
