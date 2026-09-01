"""Tests for morning-brief calendar grounding guard."""

from __future__ import annotations

import json

from brain.agent import StoppedReason, run_turn
from brain.morning_brief import (
    append_schedule_fallback,
    calendar_events_from_payload,
    calendar_payload_from_turn,
    fix_morning_brief,
    format_schedule_sentence,
    is_morning_greeting,
    merge_schedule_into_reply,
    morning_brief_locale,
    morning_brief_lacks_greeting,
    morning_brief_lacks_weather,
    needs_calendar_grounding_fix,
    needs_morning_brief_fixup,
    reply_falsely_claims_empty,
    reply_grounded_in_calendar,
)
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.tools import Tool

TARA_EVENT = {
    "summary": "Verjaardag tara",
    "start": "2026-09-01T18:00:00+02:00",
    "end": "2026-09-01T21:30:00+02:00",
    "all_day": False,
    "calendar_name": "Fam Palland",
}


def test_is_morning_greeting_en_nl_typos() -> None:
    assert is_morning_greeting("goodmorning")
    assert is_morning_greeting("Good morning")
    assert is_morning_greeting("mornin")
    assert is_morning_greeting("goedemorgen")
    assert is_morning_greeting("goemorge")
    assert is_morning_greeting("morge")
    assert is_morning_greeting("morgen")
    assert not is_morning_greeting("what's on my calendar?")


def test_morning_brief_locale() -> None:
    assert morning_brief_locale("goedemorgen") == "nl"
    assert morning_brief_locale("goemorge") == "nl"
    assert morning_brief_locale("morge") == "nl"
    assert morning_brief_locale("morgen") == "nl"
    assert morning_brief_locale("goodmorning") == "en"


def test_reply_falsely_claims_empty() -> None:
    assert reply_falsely_claims_empty("Nothing on the calendar today, sir.", "en")
    assert reply_falsely_claims_empty("Niets op de agenda vandaag.", "nl")
    assert not reply_falsely_claims_empty("Verjaardag tara at 18:00.", "en")


def test_reply_grounded_in_calendar() -> None:
    events = [TARA_EVENT]
    assert reply_grounded_in_calendar("Verjaardag tara vanavond om 18:00.", events)
    assert not reply_grounded_in_calendar("Nothing on the calendar today.", events)


WEATHER_PAYLOAD = {
    "current": {"temperature_c": 16.7, "conditions": "overcast"},
    "today": {"temp_max_c": 21.2, "temp_min_c": 15.0, "conditions": "overcast"},
}


def test_schedule_only_reply_needs_fixup() -> None:
    schedule_only = "Vandaag op je agenda: Verjaardag tara, van 18:00 tot 21:30 (Fam Palland)."
    assert needs_morning_brief_fixup(
        schedule_only, [TARA_EVENT], "nl", weather=WEATHER_PAYLOAD
    )
    assert morning_brief_lacks_greeting(schedule_only, "nl")
    assert morning_brief_lacks_weather(schedule_only)


def test_fix_morning_brief_from_schedule_only() -> None:
    schedule_only = "Vandaag op je agenda: Verjaardag tara, van 18:00 tot 21:30 (Fam Palland)."
    out = fix_morning_brief(
        schedule_only,
        weather=WEATHER_PAYLOAD,
        events=[TARA_EVENT],
        locale="nl",
    )
    assert "Goedemorgen" in out
    assert "bewolkt" in out.lower() or "graden" in out.lower()
    assert "Verjaardag tara" in out


def test_fix_morning_brief_english_goodmorning() -> None:
    schedule_only = "Vandaag op je agenda: Verjaardag tara, van 18:00 tot 21:30."
    out = fix_morning_brief(
        schedule_only,
        weather=WEATHER_PAYLOAD,
        events=[TARA_EVENT],
        locale="en",
    )
    assert "Good morning" in out
    assert "Today's schedule looks like this" in out
    assert "Vandaag op je agenda" not in out


def test_reply_grounded_in_calendar_paraphrase_tokens() -> None:
    events = [TARA_EVENT]
    assert reply_grounded_in_calendar("Vanavond verjaardag van Tara om 18:00.", events)


def test_merge_schedule_preserves_weather_and_greeting() -> None:
    base = (
        "Good morning, sir. It's overcast and about sixteen degrees. "
        "Rain is likely this afternoon. Nothing on the calendar today, sir."
    )
    out = merge_schedule_into_reply(base, [TARA_EVENT], "en")
    assert "Good morning" in out
    assert "sixteen degrees" in out or "16" in out
    assert "Verjaardag tara" in out
    assert "nothing on the calendar" not in out.lower()


def test_merge_schedule_english_locale_for_goodmorning() -> None:
    base = "Good morning, sir. Overcast, sixteen degrees."
    out = merge_schedule_into_reply(base, [TARA_EVENT], "en")
    assert "Today's schedule looks like this" in out
    assert "Vandaag op je agenda" not in out


def test_needs_calendar_grounding_fix() -> None:
    events = [TARA_EVENT]
    assert needs_calendar_grounding_fix("Nothing on the calendar today.", events, "en")
    assert not needs_calendar_grounding_fix("Verjaardag tara at 18:00.", events, "en")


def test_format_schedule_sentence() -> None:
    en = format_schedule_sentence([TARA_EVENT], "en")
    assert "Verjaardag tara" in en
    assert "18:00" in en
    assert "Today's schedule looks like this" in en
    assert "\\u" not in en
    nl = format_schedule_sentence([TARA_EVENT], "nl")
    assert "Verjaardag tara" in nl
    assert "agenda" in nl.lower()
    assert "\\u" not in nl


def test_append_schedule_fallback() -> None:
    out = append_schedule_fallback(
        "Good morning, sir. Overcast and sixteen degrees.",
        [TARA_EVENT],
        "en",
    )
    assert "Verjaardag tara" in out
    assert "18:00" in out


def test_calendar_payload_from_turn_ignores_prior_turns() -> None:
    cal_json = json.dumps({"events": [TARA_EVENT], "event_count": 1})
    messages = [
        ChatMessage(role="user", content="yesterday"),
        ChatMessage(role="tool", content='{"events": []}', tool_name="get_calendar"),
        ChatMessage(role="assistant", content="clear"),
        ChatMessage(role="user", content="goodmorning"),
        ChatMessage(role="tool", content=cal_json, tool_name="get_calendar"),
    ]
    payload = calendar_payload_from_turn(messages)
    assert payload is not None
    assert calendar_events_from_payload(payload)[0]["summary"] == "Verjaardag tara"


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self._responses = list(responses)

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        return ChatResponse(message=self._responses.pop(0))


def _read_tool(name: str, result: str) -> Tool:
    def execute(**kwargs: object) -> str:
        return result

    return Tool(
        name=name,
        description="test read",
        parameters={"type": "object", "properties": {}},
        execute=execute,
    )


def test_agent_appends_schedule_without_llm_retry() -> None:
    cal_payload = json.dumps({"events": [TARA_EVENT], "event_count": 1})
    weather_payload = json.dumps(
        {"current": {"temperature_c": 16, "conditions": "overcast"}}
    )
    registry = {
        "get_weather": _read_tool("get_weather", weather_payload),
        "get_calendar": _read_tool("get_calendar", cal_payload),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(name="get_weather", arguments={})
                    ),
                    ToolCall(
                        function=ToolCallFunction(name="get_calendar", arguments={})
                    ),
                ],
            ),
            ChatMessage(
                role="assistant",
                content=(
                    "Good morning, sir. It's overcast and about sixteen degrees. "
                    "Nothing on the calendar today, sir."
                ),
            ),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="goodmorning")],
        tools=registry,
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    content = result.content or ""
    assert "Good morning" in content
    assert "sixteen degrees" in content or "16" in content
    assert "Verjaardag tara" in content
    assert "nothing on the calendar" not in content.lower()
    assert result.steps[-1].anomaly == "morning_brief_fixup"
    assert len(client._responses) == 0


def test_agent_fixes_schedule_only_model_reply() -> None:
    cal_payload = json.dumps({"events": [TARA_EVENT], "event_count": 1})
    weather_payload = json.dumps(WEATHER_PAYLOAD)
    registry = {
        "get_weather": _read_tool("get_weather", weather_payload),
        "get_calendar": _read_tool("get_calendar", cal_payload),
    }
    schedule_only = (
        "Vandaag op je agenda: Verjaardag tara, van 18:00 tot 21:30 (Fam Palland)."
    )
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(name="get_weather", arguments={})
                    ),
                    ToolCall(
                        function=ToolCallFunction(name="get_calendar", arguments={})
                    ),
                ],
            ),
            ChatMessage(role="assistant", content=schedule_only),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="goodmorning")],
        tools=registry,
        max_iterations=3,
    )
    content = result.content or ""
    assert "Good morning" in content
    assert "Verjaardag tara" in content
    assert "degrees" in content.lower() or "overcast" in content.lower()
    assert result.steps[-1].anomaly == "morning_brief_fixup"
