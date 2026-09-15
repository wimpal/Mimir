"""Tests for evening wind-down brief (T-053)."""

from __future__ import annotations

import json

from brain.agent import StoppedReason, run_turn
from brain.evening_wind_down import (
    build_evening_wind_down_from_tools,
    evening_wind_down_locale,
    is_evening_wind_down,
)
from brain.morning_brief import is_morning_greeting
from brain.ollama import ChatMessage, ChatResponse
from brain.tools import Tool

TARA_TOMORROW = {
    "summary": "Verjaardag tara",
    "start": "2026-09-02T18:00:00+02:00",
    "end": "2026-09-02T21:30:00+02:00",
    "all_day": False,
    "calendar_name": "Fam Palland",
}

WEATHER_PAYLOAD = {
    "current": {"temperature_c": 12.0, "conditions": "overcast"},
    "today": {"temp_max_c": 14.0, "temp_min_c": 10.0, "conditions": "overcast"},
    "tomorrow": {"temp_max_c": 18.0, "temp_min_c": 9.0, "conditions": "partly cloudy"},
}

CAL_TOMORROW = {
    "day_offset": 1,
    "date": "2026-09-02",
    "events": [TARA_TOMORROW],
    "event_count": 1,
}

TASKS_PAYLOAD = [
    {"id": "t1", "title": "Take out bins", "done": False},
    {"id": "t2", "title": "Water plants", "done": False},
]

LIGHTS_OFF_OK = json.dumps(
    {
        "success": True,
        "on": False,
        "devices_toggled": 3,
        "house_wide": True,
        "names": ["Ballon", "Paarse lamp", "Keuken"],
    }
)


def test_evening_triggers_en_nl() -> None:
    assert is_evening_wind_down("Good night")
    assert is_evening_wind_down("goodnight")
    assert is_evening_wind_down("Welterusten")
    assert is_evening_wind_down("goedenacht")
    assert is_evening_wind_down("Slaap lekker")
    assert not is_evening_wind_down("good morning")
    assert not is_evening_wind_down("goedemorgen")
    assert not is_evening_wind_down("morgen")
    assert not is_evening_wind_down("ja")
    assert not is_evening_wind_down("what's on tomorrow?")


def test_evening_exclusive_with_morning() -> None:
    assert is_morning_greeting("goedemorgen")
    assert not is_evening_wind_down("goedemorgen")
    assert is_evening_wind_down("welterusten")
    assert not is_morning_greeting("welterusten")


def test_evening_locale() -> None:
    assert evening_wind_down_locale("Welterusten") == "nl"
    assert evening_wind_down_locale("Good night") == "en"
    assert evening_wind_down_locale("slaap lekker") == "nl"


def test_build_evening_wind_down_en() -> None:
    out = build_evening_wind_down_from_tools(
        weather=WEATHER_PAYLOAD,
        events=[TARA_TOMORROW],
        tasks=TASKS_PAYLOAD,
        locale="en",
        calendar_fetched=True,
        tasks_fetched=True,
        lights_ok=True,
        lights_attempted=True,
    )
    assert out.startswith("Good night")
    assert "lights are off" in out.lower()
    assert "tomorrow" in out.lower()
    assert "Verjaardag tara" in out
    assert "Take out bins" in out
    assert "Water plants" in out


def test_build_evening_homebase_down() -> None:
    out = build_evening_wind_down_from_tools(
        weather=WEATHER_PAYLOAD,
        events=[],
        tasks=None,
        locale="nl",
        calendar_fetched=True,
        tasks_fetched=False,
        lights_ok=None,
        lights_attempted=False,
    )
    assert "Welterusten" in out
    assert "lampen" in out.lower()
    assert "taken" in out.lower()
    assert "niets op de agenda morgen" in out.lower()


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self._responses = list(responses)

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        return ChatResponse(message=self._responses.pop(0))


def _tool(name: str, result: str) -> Tool:
    def execute(**kwargs: object) -> str:
        return result

    return Tool(
        name=name,
        description="test",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
        execute=execute,
    )


def test_agent_forces_evening_wind_down_tools() -> None:
    registry = {
        "get_weather": _tool("get_weather", json.dumps(WEATHER_PAYLOAD)),
        "get_calendar": _tool("get_calendar", json.dumps(CAL_TOMORROW)),
        "homebase.tasks.list": _tool("homebase.tasks.list", json.dumps(TASKS_PAYLOAD)),
        "homebase.lights.set_state": _tool(
            "homebase.lights.set_state", LIGHTS_OFF_OK
        ),
    }
    client = _ScriptedClient(
        [ChatMessage(role="assistant", content="Good night! Sleep well.")]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Good night")],
        tools=registry,
        max_iterations=4,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    content = result.content or ""
    assert "Good night" in content
    assert "lights are off" in content.lower()
    assert "Verjaardag tara" in content
    assert "Take out bins" in content
    assert "partly cloudy" in content.lower() or "tomorrow" in content.lower()
    anomalies = [s.anomaly for s in result.steps]
    assert "evening_wind_down_tools_forced" in anomalies
    assert "evening_wind_down_fixup" in anomalies
    tools_used = result.tools_used()
    assert "get_weather" in tools_used
    assert "get_calendar" in tools_used
    assert "homebase.tasks.list" in tools_used
    assert "homebase.lights.set_state" in tools_used


def test_agent_evening_degrades_without_homebase() -> None:
    registry = {
        "get_weather": _tool("get_weather", json.dumps(WEATHER_PAYLOAD)),
        "get_calendar": _tool("get_calendar", json.dumps(CAL_TOMORROW)),
    }
    client = _ScriptedClient(
        [ChatMessage(role="assistant", content="")]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Welterusten")],
        tools=registry,
        max_iterations=4,
    )
    content = (result.content or "").lower()
    assert "welterusten" in content
    assert "verjaardag tara" in content
    assert "taken" in content or "lampen" in content
    assert result.steps[-1].anomaly == "evening_wind_down_fixup"


def test_morning_still_exclusive() -> None:
    """Good morning must not take evening path."""
    cal = json.dumps({"events": [TARA_TOMORROW], "event_count": 1})
    registry = {
        "get_weather": _tool("get_weather", json.dumps(WEATHER_PAYLOAD)),
        "get_calendar": _tool("get_calendar", cal),
    }
    client = _ScriptedClient(
        [ChatMessage(role="assistant", content="Goedemorgen casual zonder tools.")]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="goedemorgen")],
        tools=registry,
        max_iterations=3,
    )
    anomalies = [s.anomaly for s in result.steps]
    assert "evening_wind_down_fixup" not in anomalies
    assert "morning_brief_fixup" in anomalies or "morning_brief_tools_forced" in anomalies
