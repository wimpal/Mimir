"""Preference get/set tools — allowlisted keys, SQLite-backed."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from brain.prefs import ALLOWED_KEYS, PREFERENCE_KEYS, normalize_preference_value

if TYPE_CHECKING:
    from brain.db import Database
    from brain.tools import Tool


def preference_tools(db: Database) -> dict[str, Tool]:
    from brain.tools import Tool

    keys_help = ", ".join(PREFERENCE_KEYS)

    def get_preference(*, key: str) -> str:
        if key not in ALLOWED_KEYS:
            return f"error: unknown preference key '{key}' (allowed: {keys_help})"
        value = db.get_preference(key)
        if value is None:
            return json.dumps({"key": key, "value": None}, separators=(",", ":"))
        if key == "favorite_genres":
            try:
                parsed: Any = json.loads(value)
            except json.JSONDecodeError:
                parsed = value
            return json.dumps({"key": key, "value": parsed}, separators=(",", ":"))
        return json.dumps({"key": key, "value": value}, separators=(",", ":"))

    def set_preference(*, key: str, value: str) -> str:
        if key not in ALLOWED_KEYS:
            return f"error: unknown preference key '{key}' (allowed: {keys_help})"
        stored = normalize_preference_value(key, value)
        if stored is None:
            return f"error: invalid value for preference '{key}'"
        db.set_preference(key, stored)
        if key == "favorite_genres":
            display: Any = json.loads(stored)
        else:
            display = stored
        return json.dumps(
            {"ok": True, "key": key, "value": display},
            separators=(",", ":"),
        )

    get_tool = Tool(
        name="get_preference",
        description=(
            "Read a stored user preference by key. Allowed keys: "
            f"{keys_help}. Use when you need a preference that may not be "
            "in the system prompt yet."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": f"Preference key ({keys_help})",
                },
            },
            "required": ["key"],
            "additionalProperties": False,
        },
        execute=get_preference,
    )

    set_tool = Tool(
        name="set_preference",
        description=(
            "Store a user preference. Allowed keys: favorite_genres "
            "(comma-separated or JSON list of genres), tone (short string). "
            "Use when the user states a lasting preference (e.g. 'I like sci-fi')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": f"Preference key ({keys_help})",
                },
                "value": {
                    "type": "string",
                    "description": (
                        "Value to store. For favorite_genres: comma-separated "
                        "genres or a JSON array string."
                    ),
                },
            },
            "required": ["key", "value"],
            "additionalProperties": False,
        },
        execute=set_preference,
    )

    return {get_tool.name: get_tool, set_tool.name: set_tool}
