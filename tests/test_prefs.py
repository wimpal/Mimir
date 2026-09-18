"""Unit tests for preference helpers and tools."""

from __future__ import annotations

import json
from pathlib import Path

from brain.config import Settings
from brain.db import Database
from brain.prefs import (
    ALLOWED_KEYS,
    PREFERENCE_KEYS,
    build_system_prompt,
    format_prefs_block,
    normalize_preference_value,
)
from brain.tools import build_registry, dispatch


def test_preference_keys_order() -> None:
    assert PREFERENCE_KEYS == ("favorite_genres", "tone")
    assert ALLOWED_KEYS == frozenset(PREFERENCE_KEYS)


def test_normalize_favorite_genres() -> None:
    assert normalize_preference_value("favorite_genres", "sci-fi, drama") == json.dumps(
        ["sci-fi", "drama"], separators=(",", ":")
    )
    assert normalize_preference_value("favorite_genres", ["sci-fi"]) == '["sci-fi"]'
    assert normalize_preference_value("bogus", "x") is None
    assert normalize_preference_value("tone", "  dry  ") == "dry"
    assert normalize_preference_value("tone", "") is None


def test_format_clock_block() -> None:
    from brain.prefs import format_clock_block

    block = format_clock_block(timezone="Europe/Amsterdam")
    assert "Current date and time" in block
    assert "today:" in block
    assert "vorige maand" in block


def test_birthday_constant_default() -> None:
    from brain.config import AgentSettings
    from brain.prefs import DEFAULT_BIRTHDAY

    assert AgentSettings().birthday == "2026-08-26"
    assert DEFAULT_BIRTHDAY == "2026-08-26"


def test_birthday_inject_on_matching_month_day() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from brain.prefs import build_system_prompt, format_clock_block

    tz = "Europe/Amsterdam"
    on_day = datetime(2027, 8, 26, 10, 0, tzinfo=ZoneInfo(tz))
    off_day = datetime(2026, 9, 17, 10, 0, tzinfo=ZoneInfo(tz))

    on_block = format_clock_block(
        timezone=tz, birthday="2026-08-26", now=on_day
    )
    assert "Mimir's birthday" in on_block
    assert "2026-08-26" in on_block

    off_block = format_clock_block(
        timezone=tz, birthday="2026-08-26", now=off_day
    )
    assert "Mimir's birthday" not in off_block

    prompt = build_system_prompt(
        "You are Mimir.",
        {},
        timezone=tz,
        birthday="2026-08-26",
        now=on_day,
    )
    assert "Mimir's birthday" in prompt


def test_build_system_prompt_includes_clock() -> None:
    prompt = build_system_prompt(
        "You are Mimir.",
        {},
        timezone="Europe/Amsterdam",
    )
    assert "Current date and time" in prompt
    assert "2026-" in prompt or "today:" in prompt


def test_format_prefs_block() -> None:
    assert format_prefs_block({}) == ""
    block = format_prefs_block({"favorite_genres": '["sci-fi"]', "tone": "dry"})
    assert "Known preferences" in block
    assert "sci-fi" in block
    assert "tone: dry" in block
    prompt = build_system_prompt("You are Mimir.", {"tone": "dry"})
    assert prompt.startswith("You are Mimir.")
    assert "tone: dry" in prompt


def test_preference_tools_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    settings = Settings(location={"latitude": 1.0, "longitude": 2.0})
    tools = build_registry(settings, db=db)
    assert "get_preference" in tools
    assert "set_preference" in tools
    assert set(ALLOWED_KEYS) <= {"favorite_genres", "tone"}

    err = dispatch(
        "set_preference",
        {"key": "nope", "value": "x"},
        tools=tools,
    )
    assert err.startswith("error:")

    ok = dispatch(
        "set_preference",
        {"key": "favorite_genres", "value": "sci-fi"},
        tools=tools,
    )
    assert '"ok":true' in ok.replace(" ", "")
    got = dispatch("get_preference", {"key": "favorite_genres"}, tools=tools)
    assert "sci-fi" in got
    assert db.get_preference("favorite_genres") == '["sci-fi"]'
