"""Evening wind-down brief (T-053): good-night trigger + tomorrow brief + lights off."""

from __future__ import annotations

import json
import re
from typing import Any

from brain.morning_brief import (
    Locale,
    calendar_events_from_payload,
    format_event_line,
    reply_falsely_claims_empty,
    reply_grounded_in_calendar,
    strip_false_empty_claims,
)

_EVENING_GREETING = re.compile(
    r"^(?:"
    r"good\s*night|goodnight|"
    r"welterusten|goedenacht|goeden\s*nacht|"
    r"slaap\s*lekker"
    r")(?:[\s,.!?—-]|$)",
    re.IGNORECASE,
)

_LOCALE_GREETING_IN_REPLY: dict[Locale, re.Pattern[str]] = {
    "en": re.compile(r"\b(good\s*night|goodnight)\b", re.IGNORECASE),
    "nl": re.compile(
        r"\b(welterusten|goedenacht|goeden\s*nacht|slaap\s*lekker)\b",
        re.IGNORECASE,
    ),
}

_WEATHER_IN_REPLY = re.compile(
    r"(°c|°\s*c|graden|degrees?|temperature|bewolkt|overcast|regen|rain|"
    r"precipitation|forecast|voorspelling|weer|weather|zonnig|cloud|bewolk|"
    r"morgen|tomorrow|overnight|nacht)",
    re.IGNORECASE,
)

_CONDITION_NL: dict[str, str] = {
    "clear": "helder",
    "mainly clear": "grotendeels helder",
    "partly cloudy": "deels bewolkt",
    "overcast": "bewolkt",
    "fog": "mistig",
    "drizzle": "motregen",
    "light drizzle": "lichte motregen",
    "moderate drizzle": "matige motregen",
    "dense drizzle": "dichte motregen",
    "slight rain": "lichte regen",
    "moderate rain": "matige regen",
    "heavy rain": "zware regen",
    "slight rain showers": "lichte buien",
    "moderate rain showers": "matige buien",
    "violent rain showers": "hevige buien",
    "thunderstorm": "onweer",
}

TASKS_LIST_TOOL = "homebase.tasks.list"
LIGHTS_SET_STATE_TOOL = "homebase.lights.set_state"
HOUSE_ALL_OFF_ARGS: dict[str, Any] = {"device_id": "all:", "on": False}


def is_evening_wind_down(text: str) -> bool:
    """True when the user message is a standalone or leading good-night greeting."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return bool(_EVENING_GREETING.match(normalized))


def evening_wind_down_locale(text: str) -> Locale:
    """Infer reply locale from the greeting (Dutch vs English)."""
    normalized = (text or "").strip().lower()
    if re.match(
        r"^(welterusten|goedenacht|goeden\s*nacht|slaap\s*lekker)",
        normalized,
    ):
        return "nl"
    return "en"


def format_evening_greeting(locale: Locale) -> str:
    if locale == "nl":
        return "Welterusten, meneer."
    return "Good night, sir."


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


def format_tomorrow_weather_brief(weather: dict[str, Any], locale: Locale) -> str:
    """Spoken tomorrow / overnight outlook from get_weather payload."""
    tomorrow = weather.get("tomorrow") if isinstance(weather.get("tomorrow"), dict) else {}
    current = weather.get("current") if isinstance(weather.get("current"), dict) else {}
    tmax = _round_temp(tomorrow.get("temp_max_c"))
    tmin = _round_temp(tomorrow.get("temp_min_c"))
    conditions = _translate_conditions(str(tomorrow.get("conditions") or ""), locale)
    # Fall back to current conditions label only when tomorrow slice lacks one.
    if not conditions or conditions in {"unknown", "onbekend"}:
        conditions = _translate_conditions(str(current.get("conditions") or ""), locale)

    if locale == "nl":
        bits: list[str] = []
        if conditions and conditions not in {"onbekend"}:
            bits.append(conditions)
        if tmax is not None and tmin is not None:
            bits.append(f"tussen {tmin} en {tmax} graden")
        elif tmax is not None:
            bits.append(f"max ongeveer {tmax} graden")
        if bits:
            return "Morgen wordt het " + ", ".join(bits) + "."
        return "Het weer voor morgen kon ik niet goed lezen."

    bits = []
    if conditions and conditions != "unknown":
        bits.append(f"tomorrow looks {conditions}")
    if tmax is not None and tmin is not None:
        bits.append(f"high near {tmax}, low near {tmin}")
    elif tmax is not None:
        bits.append(f"high near {tmax}")
    if bits:
        text = "; ".join(bits)
        return text[0].upper() + text[1:] + "."
    return "I couldn't read tomorrow's weather clearly."


def format_tomorrow_schedule_sentence(
    events: list[dict[str, Any]], locale: Locale
) -> str:
    if not events:
        if locale == "nl":
            return "Niets op de agenda morgen, meneer."
        return "Nothing on the calendar tomorrow, sir."
    lines = [format_event_line(ev, locale=locale) for ev in events]
    joined = "; ".join(lines)
    if locale == "nl":
        return f"Morgen op je agenda: {joined}."
    return f"Tomorrow's schedule looks like this: {joined}."


def format_tasks_sentence(
    tasks: list[dict[str, Any]] | None,
    locale: Locale,
    *,
    tasks_fetched: bool,
) -> str:
    if not tasks_fetched:
        if locale == "nl":
            return "Open taken kon ik niet ophalen."
        return "I couldn't fetch open tasks."
    items = tasks or []
    titles = [
        str(t.get("title") or "").strip()
        for t in items
        if isinstance(t, dict) and str(t.get("title") or "").strip()
    ]
    if not titles:
        if locale == "nl":
            return "Geen open taken."
        return "No open tasks."
    # Keep the brief short — first few titles.
    shown = titles[:5]
    joined = "; ".join(shown)
    extra = len(titles) - len(shown)
    if locale == "nl":
        more = f" (en {extra} meer)" if extra > 0 else ""
        return f"Open taken: {joined}{more}."
    more = f" (and {extra} more)" if extra > 0 else ""
    return f"Open tasks: {joined}{more}."


def format_lights_off_sentence(
    *,
    locale: Locale,
    lights_ok: bool | None,
    lights_attempted: bool,
) -> str:
    """lights_ok True=success, False=failed, None=tool unavailable / not attempted."""
    if not lights_attempted or lights_ok is None:
        if locale == "nl":
            return "De lampen kon ik niet uitzetten."
        return "I couldn't turn the lights off."
    if lights_ok:
        if locale == "nl":
            return "Alle lampen staan uit."
        return "All the lights are off."
    if locale == "nl":
        return "De lampen uitzetten lukte niet."
    return "Turning the lights off didn't work."


def calendar_is_tomorrow(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    offset = payload.get("day_offset")
    return offset == 1


def tasks_from_payload(raw: str | None) -> list[dict[str, Any]] | None:
    """Parse homebase.tasks.list JSON; None on error."""
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.startswith("error:"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return None


def lights_off_succeeded(raw: str | None) -> bool:
    """True only for a successful house-wide off batch (not a single-lamp write)."""
    if raw is None:
        return False
    from brain.mcp.lights import set_state_tool_succeeded

    if not set_state_tool_succeeded(raw):
        return False
    text = raw.strip()
    brace = text.find("{")
    if brace < 0:
        return False
    try:
        data = json.loads(text[brace:])
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    if data.get("on") is not False:
        return False
    return data.get("house_wide") is True


def evening_wind_down_tools_incomplete(
    *,
    weather: dict[str, Any] | None,
    calendar_tomorrow_fetched: bool,
    tasks_fetched: bool,
    tasks_available: bool,
    lights_done: bool,
    lights_available: bool,
) -> bool:
    """True when required wind-down tools are missing this turn."""
    if weather is None:
        return True
    if not calendar_tomorrow_fetched:
        return True
    if tasks_available and not tasks_fetched:
        return True
    if lights_available and not lights_done:
        return True
    return False


def evening_wind_down_lacks_greeting(reply: str, locale: Locale) -> bool:
    return not _LOCALE_GREETING_IN_REPLY[locale].search(reply or "")


def evening_wind_down_lacks_weather(reply: str) -> bool:
    return not _WEATHER_IN_REPLY.search(reply or "")


def weather_fetch_failed_line(locale: Locale) -> str:
    if locale == "nl":
        return "Het weer kon ik niet ophalen."
    return "I couldn't fetch the weather."


def calendar_fetch_failed_line(locale: Locale) -> str:
    if locale == "nl":
        return "De agenda kon ik niet ophalen."
    return "I couldn't fetch the calendar."


def build_evening_wind_down_from_tools(
    *,
    weather: dict[str, Any] | None,
    events: list[dict[str, Any]],
    tasks: list[dict[str, Any]] | None,
    locale: Locale,
    calendar_fetched: bool = True,
    tasks_fetched: bool = False,
    lights_ok: bool | None = None,
    lights_attempted: bool = False,
) -> str:
    """Code-backed wind-down: greeting + lights + weather + schedule + tasks."""
    parts: list[str] = [format_evening_greeting(locale)]
    parts.append(
        format_lights_off_sentence(
            locale=locale,
            lights_ok=lights_ok,
            lights_attempted=lights_attempted,
        )
    )
    if weather:
        parts.append(format_tomorrow_weather_brief(weather, locale))
    else:
        parts.append(weather_fetch_failed_line(locale))
    if calendar_fetched:
        parts.append(format_tomorrow_schedule_sentence(events, locale))
    else:
        parts.append(calendar_fetch_failed_line(locale))
    parts.append(
        format_tasks_sentence(tasks, locale, tasks_fetched=tasks_fetched)
    )
    return " ".join(parts)


def needs_evening_wind_down_fixup(
    reply: str,
    events: list[dict[str, Any]],
    locale: Locale,
    *,
    weather: dict[str, Any] | None,
    calendar_fetched: bool = True,
    tasks_fetched: bool = False,
    tasks_available: bool = False,
    lights_done: bool = False,
    lights_available: bool = False,
) -> bool:
    if evening_wind_down_tools_incomplete(
        weather=weather,
        calendar_tomorrow_fetched=calendar_fetched,
        tasks_fetched=tasks_fetched,
        tasks_available=tasks_available,
        lights_done=lights_done,
        lights_available=lights_available,
    ):
        return True
    if evening_wind_down_lacks_greeting(reply, locale):
        return True
    if weather and evening_wind_down_lacks_weather(reply):
        return True
    if events and reply_falsely_claims_empty(reply, locale):
        return True
    if events and not reply_grounded_in_calendar(reply, events):
        return True
    return False


def fix_evening_wind_down(
    reply: str,
    *,
    weather: dict[str, Any] | None,
    events: list[dict[str, Any]],
    tasks: list[dict[str, Any]] | None,
    locale: Locale,
    calendar_fetched: bool = True,
    tasks_fetched: bool = False,
    lights_ok: bool | None = None,
    lights_attempted: bool = False,
) -> str:
    """Prefer full code-backed rebuild for wind-down (short, consistent)."""
    _ = strip_false_empty_claims(reply, locale)
    return build_evening_wind_down_from_tools(
        weather=weather,
        events=events,
        tasks=tasks,
        locale=locale,
        calendar_fetched=calendar_fetched,
        tasks_fetched=tasks_fetched,
        lights_ok=lights_ok,
        lights_attempted=lights_attempted,
    )


def calendar_events_for_tomorrow(
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not calendar_is_tomorrow(payload):
        return []
    return calendar_events_from_payload(payload)
