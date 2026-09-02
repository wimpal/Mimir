"""Post-turn reply fixups — locale, weather+shopping compound answers, list grounding."""

from __future__ import annotations

import re
from typing import Any, Literal

from brain.shopping_list import filter_shopping_list_items
from brain.morning_brief import (
    Locale,
    _round_temp,
    _translate_conditions,
    morning_brief_lacks_weather,
)

_NL_MARKERS = re.compile(
    r"\b("
    r"vertel|wat|weer|hoe|waarom|boodschappen|boodschappelijst|boodschappenlijst|"
    r"lijst|vandaag|vanavond|graag|zinnen|staat|staan|hebben|nodig|inkopen|"
    r"koop|meneer|alstublieft|alsjeblieft|morgen|van|het|een"
    r")\b",
    re.IGNORECASE,
)

_EN_MARKERS = re.compile(
    r"\b("
    r"tell|what|weather|shopping|list|please|today|tomorrow|sentences?|"
    r"includes|forecast"
    r")\b",
    re.IGNORECASE,
)

_WEATHER_ASK = re.compile(
    r"\b(weer|weather|forecast|voorspelling|temperatuur|temperature|regen|rain)\b",
    re.IGNORECASE,
)

_SHOPPING_ASK = re.compile(
    r"\b("
    r"boodschappenlijst|boodschappelijst|boodschappenlijn\b|boodschappelij\b|shopping\s*list|"
    r"wat\s+er\b.*\b(?:lijst|staat)|wat\s+staat\b.*\b(?:lijst|staat)|"
    r"what(?:'s| is)\s+on\s+the\s+(?:shopping\s+)?list|"
    r"what\s+do\s+we\s+need\s+to\s+buy"
    r")\b",
    re.IGNORECASE,
)

_EN_REPLY_STRONG = re.compile(
    r"\b("
    r"the shopping list|would you like|includes:|labeled|marked as checked|"
    r"adjust any|add more to the list|several items"
    r")\b",
    re.IGNORECASE,
)

_NL_REPLY_STRONG = re.compile(
    r"\b("
    r"op de boodschappenlijst|graden|bewolkt|meneer|het weer|"
    r"staat alleen|staan "
    r")\b",
    re.IGNORECASE,
)

_HALLUCINATION_MARKERS = re.compile(
    r"mcp[- ]?smoke|\blabeled\b|marked as checked|several items|"
    r"aangemarkeerd|gemarkeerd|diverse\s+\w*producten",
    re.IGNORECASE,
)

_LIST_CLAIM = re.compile(
    r"\b(includes|items|kaas|melk|coffee|milk|shopping list|boodschappenlijst)\b",
    re.IGNORECASE,
)

_SHOPPING_STOPWORDS = frozenset(
    {
        "the",
        "shopping",
        "list",
        "includes",
        "items",
        "several",
        "labeled",
        "marked",
        "checked",
        "would",
        "like",
        "adjust",
        "them",
        "more",
        "some",
        "these",
        "those",
        "each",
        "with",
        "quantity",
        "and",
        "are",
        "already",
        "any",
        "add",
        "to",
        "you",
        "your",
        "what",
        "that",
        "this",
        "for",
    }
)


def user_message_locale(text: str) -> Locale:
    """Infer reply locale from the user's latest message (NL vs EN)."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return "en"
    if re.match(
        r"^(goedemorgen|goemorge|goed\s*morgen|morge)\b",
        normalized,
    ):
        return "nl"
    if re.match(r"^(good\s*morning|goodmorning|morning|mornin)\b", normalized):
        return "en"
    nl_score = len(_NL_MARKERS.findall(normalized))
    en_score = len(_EN_MARKERS.findall(normalized))
    if nl_score > en_score:
        return "nl"
    if en_score > nl_score:
        return "en"
    if re.search(r"\b(de|het|een|van|op|er|is|zinnen)\b", normalized):
        return "nl"
    return "en"


def user_asked_about_weather(text: str) -> bool:
    return bool(_WEATHER_ASK.search(text or ""))


def user_asked_about_shopping_list(text: str) -> bool:
    normalized = (text or "").lower()
    if _SHOPPING_ASK.search(normalized):
        return True
    if re.search(r"boodschap\w*", normalized) and (
        "staat" in normalized or "lijst" in normalized or "wat er" in normalized
    ):
        return True
    if "lijst" in normalized and ("boodschappen" in normalized or "wat er" in normalized):
        return True
    return False


def reply_locale_mismatch(user_locale: Locale, reply: str) -> bool:
    """True when a Dutch user got a clearly English assistant reply."""
    if user_locale != "nl":
        return False
    text = reply or ""
    if _NL_REPLY_STRONG.search(text):
        return False
    return bool(_EN_REPLY_STRONG.search(text))


def _item_names(items: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("name") or "").strip().lower()
        for item in items
        if str(item.get("name") or "").strip()
    }


def _looks_like_nonempty_list(reply: str) -> bool:
    return bool(_LIST_CLAIM.search(reply or "")) or bool(
        _HALLUCINATION_MARKERS.search(reply or "")
    )


def reply_grounded_in_shopping_list(reply: str, items: list[dict[str, Any]]) -> bool:
    """True when the reply only reflects shopping_list.list tool output."""
    items = filter_shopping_list_items(items)
    text = reply or ""
    if _HALLUCINATION_MARKERS.search(text):
        return False
    names = _item_names(items)
    lowered = text.lower()
    if not names:
        return not _looks_like_nonempty_list(text)
    if not all(name in lowered for name in names):
        return False
    for match in re.finditer(r"\b([a-z][a-z0-9_-]{3,})\s*\(\d+\)", lowered):
        token = match.group(1)
        if token not in names and token not in _SHOPPING_STOPWORDS:
            return False
    for match in re.finditer(r'"([^"]{2,})"', text):
        if match.group(1).strip().lower() not in names:
            return False
    if re.search(r"mcp|smoke", lowered) and not any(
        "mcp" in name or "smoke" in name for name in names
    ):
        return False
    return True


def format_weather_one_liner(
    weather: dict[str, Any],
    locale: Locale,
    *,
    compact: bool = False,
) -> str:
    """One spoken weather sentence for compound answers."""
    current = weather.get("current") if isinstance(weather.get("current"), dict) else {}
    today = weather.get("today") if isinstance(weather.get("today"), dict) else {}
    temp = _round_temp(current.get("temperature_c"))
    conditions = _translate_conditions(str(current.get("conditions") or ""), locale)
    tmax = _round_temp(today.get("temp_max_c"))
    tmin = _round_temp(today.get("temp_min_c"))

    if locale == "nl":
        sentence = f"Het is nu {conditions}"
        if temp is not None:
            sentence = f"{sentence}, {temp} graden"
        sentence = f"{sentence}."
        if not compact and tmax is not None and tmin is not None:
            sentence = f"{sentence} Vandaag tussen {tmin} en {tmax} graden."
        return sentence

    sentence = f"It's {conditions}"
    if temp is not None:
        sentence = f"{sentence}, about {temp} degrees"
    sentence = f"{sentence}."
    if not compact and tmax is not None and tmin is not None:
        sentence = f"{sentence} Today between {tmin} and {tmax} degrees."
    return sentence


def format_shopping_list_sentence(items: list[dict[str, Any]], locale: Locale) -> str:
    """One spoken shopping-list sentence grounded in tool output."""
    items = filter_shopping_list_items(items)
    labeled: list[str] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        qty = item.get("quantity")
        try:
            q = int(qty) if qty is not None else 1
        except (TypeError, ValueError):
            q = 1
        labeled.append(f"{name} ({q})" if q != 1 else name)

    if not labeled:
        if locale == "nl":
            return "De boodschappenlijst is leeg."
        return "The shopping list is empty."

    if locale == "nl":
        if len(labeled) == 1:
            return f"Op de boodschappenlijst staat alleen {labeled[0]}."
        joined = ", ".join(labeled[:-1]) + f" en {labeled[-1]}"
        return f"Op de boodschappenlijst staan {joined}."

    if len(labeled) == 1:
        return f"On the shopping list: {labeled[0]}."
    joined = ", ".join(labeled[:-1]) + f" and {labeled[-1]}"
    return f"On the shopping list: {joined}."


def can_tool_backed_weather_shopping_reply(
    user_message: str,
    *,
    weather: dict[str, Any] | None,
    shopping_list_fetched: bool,
) -> bool:
    """True when tool payloads are enough to build a reply without the model."""
    if user_asked_about_weather(user_message) and weather:
        return True
    if shopping_list_fetched:
        return True
    return False


def needs_weather_shopping_fixup(
    user_message: str,
    reply: str,
    *,
    weather: dict[str, Any] | None,
    shopping_list_fetched: bool,
    shopping_items: list[dict[str, Any]],
) -> bool:
    """True when a compound or grounded reply should be rebuilt from tool data."""
    locale = user_message_locale(user_message)
    asked_weather = user_asked_about_weather(user_message)
    asked_list = user_asked_about_shopping_list(user_message)

    if locale == "nl" and reply_locale_mismatch(locale, reply):
        if (asked_weather and weather) or (asked_list and shopping_list_fetched):
            return True

    if asked_weather and weather and morning_brief_lacks_weather(reply):
        return True

    if shopping_list_fetched and not reply_grounded_in_shopping_list(
        reply, shopping_items
    ):
        return True

    return False


def fix_weather_shopping_reply(
    reply: str,
    user_message: str,
    *,
    weather: dict[str, Any] | None,
    shopping_list_fetched: bool,
    shopping_items: list[dict[str, Any]],
) -> str:
    """Rebuild weather and/or shopping facts in the user's language."""
    locale = user_message_locale(user_message)
    include_weather = bool(weather and user_asked_about_weather(user_message))
    include_list = shopping_list_fetched
    compact_weather = include_weather and include_list
    parts: list[str] = []

    if include_weather:
        parts.append(format_weather_one_liner(weather, locale, compact=compact_weather))
    if include_list:
        parts.append(format_shopping_list_sentence(shopping_items, locale))

    if parts:
        return " ".join(parts)
    return (reply or "").strip()
