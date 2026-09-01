"""Morning-brief calendar grounding guard (T-028 / calendar reliability).

Conservative EN/NL heuristics — safety net when the model template-matches an
empty-schedule phrase while get_calendar returned events.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from brain.ollama import ChatMessage

MAX_CALENDAR_GROUNDING_NUDGES = 1

Locale = Literal["en", "nl"]

_MORNING_GREETING = re.compile(
    r"^(?:"
    r"good\s*morning|goodmorning|morning|mornin|"
    r"goedemorgen|goemorge|goed\s*morgen|morge|morgen"
    r")(?:[\s,.!?—-]|$)",
    re.IGNORECASE,
)

_FALSE_EMPTY_EN: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"nothing\s+on\s+(the\s+)?calendar",
        r"no\s+events?\s+(on\s+)?(the\s+)?calendar",
        r"calendar\s+is\s+clear",
        r"schedule\s+is\s+clear",
        r"nothing\s+scheduled",
        r"clear\s+schedule",
        r"no\s+appointments?",
    )
)

_FALSE_EMPTY_NL: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"niets\s+op\s+de\s+agenda",
        r"geen\s+afspraken",
        r"agenda\s+is\s+leeg",
        r"niets\s+gepland",
        r"rustige\s+dag",
        r"leeg\s+schema",
    )
)


def is_morning_greeting(text: str) -> bool:
    """True when the user message is a standalone or leading morning greeting."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return bool(_MORNING_GREETING.match(normalized))


def morning_brief_locale(text: str) -> Locale:
    """Infer reply locale from the greeting (Dutch vs English)."""
    normalized = (text or "").strip().lower()
    if re.match(r"^(goedemorgen|goemorge|goed\s*morgen|morge|morgen)", normalized):
        return "nl"
    return "en"


def calendar_payload_from_turn(messages: list[ChatMessage]) -> dict[str, Any] | None:
    """Parse get_calendar tool result from the current turn (after last user message)."""
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if msg.role == "user" and (msg.content or "").strip():
            last_user_idx = i
    if last_user_idx < 0:
        return None
    payload: dict[str, Any] | None = None
    for msg in messages[last_user_idx + 1 :]:
        if msg.role != "tool" or msg.tool_name != "get_calendar":
            continue
        raw = (msg.content or "").strip()
        if not raw or raw.startswith("error:"):
            payload = None
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
            continue
        if isinstance(data, dict):
            payload = data
    return payload


def calendar_payload_from_messages(messages: list[ChatMessage]) -> dict[str, Any] | None:
    """Parse the latest get_calendar tool result in the working transcript."""
    for msg in reversed(messages):
        if msg.role != "tool" or msg.tool_name != "get_calendar":
            continue
        raw = (msg.content or "").strip()
        if not raw or raw.startswith("error:"):
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            return data
        return None
    return None


def calendar_events_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    return [ev for ev in events if isinstance(ev, dict)]


def _summary_tokens(summary: str) -> list[str]:
    """Significant words from an event title for fuzzy grounding checks."""
    words = re.findall(r"[\w']+", summary.lower())
    return [w for w in words if len(w) >= 3]


def _event_mentioned(ev: dict[str, Any], content: str) -> bool:
    summary = str(ev.get("summary") or "").strip()
    if summary and summary.lower() in content:
        return True
    tokens = _summary_tokens(summary)
    if tokens and all(token in content for token in tokens):
        return True
    start = str(ev.get("start") or "").strip()
    if len(start) >= 10 and start[:10] in content:
        return True
    if "T" in start and len(start) >= 16:
        hhmm = start[11:16]
        if hhmm and hhmm in content:
            return True
        # Spoken Dutch often uses "18:00" or "18.00" or "18 uur"
        hour = hhmm[:2].lstrip("0") or "0"
        minute = hhmm[3:5]
        if minute == "00":
            if f"{hour} uur" in content or f"{hour}:00" in content:
                return True
        else:
            if f"{hour}:{minute}" in content or f"{hour}.{minute}" in content:
                return True
    # schedule_lines entry may appear verbatim
    return False


def reply_grounded_in_calendar(reply: str, events: list[dict[str, Any]]) -> bool:
    """True when every event summary or start time appears in the reply."""
    if not events:
        return True
    content = (reply or "").lower()
    if not content.strip():
        return False
    return all(_event_mentioned(ev, content) for ev in events)


def reply_falsely_claims_empty(reply: str, locale: Locale) -> bool:
    """True when the reply claims an empty schedule."""
    content = (reply or "").strip()
    if not content:
        return False
    patterns = _FALSE_EMPTY_NL if locale == "nl" else _FALSE_EMPTY_EN
    return any(p.search(content) for p in patterns)


def _format_time_range(start: str, end: str, *, all_day: bool, locale: Locale) -> str:
    if all_day or (start and "T" not in start):
        return "hele dag" if locale == "nl" else "all day"
    if "T" not in start:
        return start
    start_hhmm = start[11:16] if len(start) >= 16 else start
    end_hhmm = end[11:16] if end and "T" in end and len(end) >= 16 else ""
    if end_hhmm and end_hhmm != start_hhmm:
        if locale == "nl":
            return f"van {start_hhmm} tot {end_hhmm}"
        return f"from {start_hhmm} to {end_hhmm}"
    if locale == "nl":
        return f"om {start_hhmm}"
    return f"at {start_hhmm}"


def format_event_line(ev: dict[str, Any], *, locale: Locale = "en") -> str:
    """Single spoken schedule fragment for one event (ASCII-only — safe in JSON tool output)."""
    summary = str(ev.get("summary") or "(no title)").strip()
    start = str(ev.get("start") or "")
    end = str(ev.get("end") or "")
    all_day = bool(ev.get("all_day"))
    time_part = _format_time_range(start, end, all_day=all_day, locale=locale)
    cal = str(ev.get("calendar_name") or "").strip()
    if cal:
        return f"{summary}, {time_part} ({cal})"
    return f"{summary}, {time_part}"


def format_schedule_sentence(events: list[dict[str, Any]], locale: Locale) -> str:
    """Programmatic schedule sentence when the model omits calendar facts."""
    if not events:
        if locale == "nl":
            return "Niets op de agenda vandaag, meneer."
        return "Nothing on the calendar today, sir."
    lines = [format_event_line(ev, locale=locale) for ev in events]
    joined = "; ".join(lines)
    if locale == "nl":
        return f"Vandaag op je agenda: {joined}."
    return f"Today's schedule looks like this: {joined}."


def calendar_grounding_nudge(events: list[dict[str, Any]], locale: Locale) -> str:
    """Hidden correction when the model skipped or falsified the schedule."""
    lines = [format_event_line(ev, locale=locale) for ev in events]
    listing = "; ".join(lines)
    if locale == "nl":
        return (
            "System correction (do not repeat to the user): get_calendar returned "
            f"{len(events)} event(s) today — you MUST include every one with its time "
            "in Dutch, in a natural spoken sentence with a short lead-in (e.g. "
            "'Vandaag op je agenda: …'). Paraphrase in prose; do not paste "
            "schedule_lines or JSON escapes. Required facts: "
            f"{listing}. Do not say the day is empty or clear."
        )
    return (
        "System correction (do not repeat to the user): get_calendar returned "
        f"{len(events)} event(s) today — you MUST include every one with its time "
        "in English, in a natural spoken sentence with a short lead-in (e.g. "
        "'Today's schedule looks like this: …' or 'On your calendar today: …'). "
        "Paraphrase in prose; do not paste schedule_lines or JSON escapes. "
        f"Required facts: {listing}. Do not say the day is empty or clear."
    )


def needs_calendar_grounding_fix(
    reply: str,
    events: list[dict[str, Any]],
    locale: Locale,
) -> bool:
    """True when a morning brief reply misrepresents a non-empty calendar."""
    if not events:
        return False
    if reply_falsely_claims_empty(reply, locale):
        return True
    return not reply_grounded_in_calendar(reply, events)


def strip_false_empty_claims(reply: str, locale: Locale) -> str:
    """Remove a false clear-schedule phrase before appending real events."""
    text = (reply or "").strip()
    if not text:
        return text
    patterns = _FALSE_EMPTY_NL if locale == "nl" else _FALSE_EMPTY_EN
    for pat in patterns:
        text = pat.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"[,;]\s*$", "", text).strip()
    return text


_LOCALE_GREETING_IN_REPLY: dict[Locale, re.Pattern[str]] = {
    "en": re.compile(
        r"\b(good\s*morning|goodmorning|morning|mornin)\b",
        re.IGNORECASE,
    ),
    "nl": re.compile(
        r"\b(goedemorgen|goed\s*morgen|goemorge|morge|morgen)\b",
        re.IGNORECASE,
    ),
}

_WEATHER_IN_REPLY = re.compile(
    r"(°c|°\s*c|graden|degrees?|temperature|bewolkt|overcast|regen|rain|"
    r"precipitation|forecast|voorspelling|weer|weather|zonnig|cloud|bewolk)",
    re.IGNORECASE,
)

_CONDITION_NL: dict[str, str] = {
    "clear": "helder",
    "mainly clear": "grotendeels helder",
    "partly cloudy": "deels bewolkt",
    "overcast": "bewolkt",
    "fog": "mistig",
    "slight rain": "lichte regen",
    "moderate rain": "matige regen",
    "heavy rain": "zware regen",
    "slight rain showers": "lichte buien",
    "moderate rain showers": "matige buien",
    "violent rain showers": "hevige buien",
    "thunderstorm": "onweer",
}


def format_greeting(locale: Locale) -> str:
    if locale == "nl":
        return "Goedemorgen, meneer."
    return "Good morning, sir."


def _round_temp(value: float | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _translate_conditions(conditions: str, locale: Locale) -> str:
    key = (conditions or "").strip().lower()
    if locale == "nl":
        return _CONDITION_NL.get(key, key or "onbekend")
    return key or "unknown"


def format_weather_brief(weather: dict[str, Any], locale: Locale) -> str:
    """Two short spoken weather sentences from get_weather payload."""
    current = weather.get("current") if isinstance(weather.get("current"), dict) else {}
    today = weather.get("today") if isinstance(weather.get("today"), dict) else {}
    temp = _round_temp(current.get("temperature_c"))
    conditions = _translate_conditions(str(current.get("conditions") or ""), locale)
    tmax = _round_temp(today.get("temp_max_c"))
    tmin = _round_temp(today.get("temp_min_c"))
    today_conditions = _translate_conditions(str(today.get("conditions") or ""), locale)

    if locale == "nl":
        now = f"Het is nu {conditions}"
        if temp is not None:
            now = f"{now}, {temp} graden"
        now = f"{now}."
        rest_bits: list[str] = []
        if today_conditions and today_conditions != conditions:
            rest_bits.append(f"de rest van vandaag {today_conditions}")
        if tmax is not None and tmin is not None:
            rest_bits.append(f"tussen {tmin} en {tmax} graden")
        rest = "De rest van vandaag blijft " + ", ".join(rest_bits) + "." if rest_bits else ""
        return f"{now} {rest}".strip()

    now = f"It's {conditions}"
    if temp is not None:
        now = f"{now} and about {temp} degrees"
    now = f"{now}."
    rest_bits = []
    if today_conditions:
        rest_bits.append(f"the rest of today looks {today_conditions}")
    if tmax is not None and tmin is not None:
        rest_bits.append(f"high near {tmax}, low near {tmin}")
    rest = " ".join(rest_bits).capitalize() + "." if rest_bits else ""
    return f"{now} {rest}".strip()


def morning_brief_lacks_greeting(reply: str, locale: Locale) -> bool:
    return not _LOCALE_GREETING_IN_REPLY[locale].search(reply or "")


def morning_brief_lacks_weather(reply: str) -> bool:
    return not _WEATHER_IN_REPLY.search(reply or "")


def needs_morning_brief_fixup(
    reply: str,
    events: list[dict[str, Any]],
    locale: Locale,
    *,
    weather: dict[str, Any] | None,
) -> bool:
    """True when a morning brief is missing greeting, weather, or calendar facts."""
    if morning_brief_lacks_greeting(reply, locale):
        return True
    if weather and morning_brief_lacks_weather(reply):
        return True
    return needs_calendar_grounding_fix(reply, events, locale)


def fix_morning_brief(
    reply: str,
    *,
    weather: dict[str, Any] | None,
    events: list[dict[str, Any]],
    locale: Locale,
) -> str:
    """Build a complete brief: greeting + weather + schedule (code-backed gaps only)."""
    base = strip_false_empty_claims(reply, locale).rstrip()

    # Model returned schedule only — rebuild greeting + weather + schedule.
    if events and reply_grounded_in_calendar(base, events) and (
        morning_brief_lacks_greeting(reply, locale) or morning_brief_lacks_weather(reply)
    ):
        parts: list[str] = []
        if morning_brief_lacks_greeting(reply, locale):
            parts.append(format_greeting(locale))
        if weather and morning_brief_lacks_weather(reply):
            parts.append(format_weather_brief(weather, locale))
        parts.append(format_schedule_sentence(events, locale))
        return " ".join(parts)

    # Greeting + weather present — append or fix schedule only.
    if events and needs_calendar_grounding_fix(reply, events, locale):
        return merge_schedule_into_reply(reply, events, locale)

    # Missing greeting and/or weather; calendar already fine or empty.
    parts = []
    if morning_brief_lacks_greeting(reply, locale):
        parts.append(format_greeting(locale))
    if weather and morning_brief_lacks_weather(reply):
        parts.append(format_weather_brief(weather, locale))
    if not events and (
        reply_falsely_claims_empty(reply, locale) or not (reply or "").strip()
    ):
        parts.append(format_schedule_sentence([], locale))
    if parts:
        body = base if base and not reply_falsely_claims_empty(reply, locale) else ""
        if body:
            return f"{' '.join(parts)} {body}".strip()
        return " ".join(parts)
    return (reply or "").strip()


def merge_schedule_into_reply(
    reply: str,
    events: list[dict[str, Any]],
    locale: Locale,
) -> str:
    """Append code-backed schedule; strip false-empty claims."""
    base = strip_false_empty_claims(reply, locale).rstrip()
    schedule = format_schedule_sentence(events, locale)
    if not base:
        return schedule
    if reply_grounded_in_calendar(base, events):
        return base
    if base.endswith((".", "!", "?")):
        return f"{base} {schedule}"
    return f"{base}. {schedule}"


def append_schedule_fallback(reply: str, events: list[dict[str, Any]], locale: Locale) -> str:
    """Append code-backed schedule when the model omits calendar facts."""
    return merge_schedule_into_reply(reply, events, locale)
