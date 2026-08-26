"""Allowlisted user Preferences — normalize, format for system inject."""

from __future__ import annotations

import json
from typing import Any

ALLOWED_KEYS = frozenset({"favorite_genres", "tone"})


def normalize_preference_value(key: str, value: Any) -> str | None:
    """Return stored string form, or None if invalid for the key."""
    if key not in ALLOWED_KEYS:
        return None

    if key == "tone":
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text if text else None

    # favorite_genres — JSON array of non-empty strings
    genres: list[str]
    if isinstance(value, list):
        genres = [str(g).strip() for g in value if str(g).strip()]
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, list):
                return None
            genres = [str(g).strip() for g in parsed if str(g).strip()]
        else:
            genres = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        return None

    if not genres:
        return None
    return json.dumps(genres, separators=(",", ":"))


def format_prefs_block(prefs: dict[str, str]) -> str:
    """Short markdown block for system prompt; omit unset / unknown keys."""
    lines: list[str] = []
    if "favorite_genres" in prefs:
        try:
            genres = json.loads(prefs["favorite_genres"])
        except json.JSONDecodeError:
            genres = None
        if isinstance(genres, list) and genres:
            lines.append(f"- favorite_genres: {', '.join(str(g) for g in genres)}")
    if "tone" in prefs and prefs["tone"].strip():
        lines.append(f"- tone: {prefs['tone'].strip()}")
    if not lines:
        return ""
    return "## Known preferences\n\n" + "\n".join(lines)


def build_system_prompt(base: str, prefs: dict[str, str]) -> str:
    block = format_prefs_block(prefs)
    if not block:
        return base
    return f"{base.rstrip()}\n\n{block}\n"
