"""Light device_id resolution for homebase.lights.set_state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from brain.mcp.errors import tool_result_is_error

# Multi-word room capture: "living room", "dining room", or a single token.
_ROOM_WORD = r"(?:[\w]+(?:\s+[\w]+)?)"

_LAMP_NAME_HINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:zet|doe|turn|switch|maak)\s+(?:de\s+|het\s+)?(\w+)\s+lamp\b",
        # T-040 appearance: "Zet Ballon op 40%", "Dim Ballon to 40%", …
        r"\b(?:zet|doe|maak)\s+(?:de\s+|het\s+)?(\w+)\s+op\b",
        r"\bdim\s+(?:de\s+|het\s+|the\s+)?(\w+)\b",
        r"\b(?:make|maak)\s+(?:de\s+|het\s+|the\s+)?(\w+)\b",
        r"\bturn\s+(?:de\s+|het\s+|the\s+)?(\w+)\s+to\b",
        r"\b(?:turn|switch)\s+(?:on|off)\s+(\w+)\b",
        r"\blamp\s+(\w+)\b",
    )
)

# Warmth defaults (Tradfri / handoff T-040).
_WARM_KELVIN = 2700
_COOL_KELVIN = 4000

# Tiny NL/EN colour name → IKEA Tradfri chromatic preset id.
_COLOUR_NAME_TO_PRESET: dict[str, str] = {
    "red": "saturated_red",
    "rood": "saturated_red",
    "blue": "blue",
    "blauw": "blue",
    "green": "lime",
    "groen": "lime",
    "lime": "lime",
    "yellow": "yellow",
    "geel": "yellow",
    "pink": "pink",
    "roze": "pink",
    "purple": "saturated_purple",
    "paars": "saturated_purple",
    "orange": "peach",
    "oranje": "peach",
    "peach": "peach",
    "amber": "warm_amber",
    "cyan": "light_blue",
    "turquoise": "light_blue",
}

_BRIGHTNESS_RE = re.compile(r"\b(\d{1,3})\s*%", re.IGNORECASE)
_KELVIN_RE = re.compile(r"\b(\d{4})\s*(?:k|kelvin)\b", re.IGNORECASE)
_WARM_RE = re.compile(
    r"\b(?:warm(?:e)?\s+wit|warm\s+white|warm(?:e)?)\b", re.IGNORECASE
)
_COOL_RE = re.compile(
    r"\b(?:cool\s+white|koel(?:e)?\s+wit|cool|koel)\b", re.IGNORECASE
)
_COLOUR_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_COLOUR_NAME_TO_PRESET, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_APPEARANCE_KEYS = frozenset(
    {"brightness", "color_temp_kelvin", "color_preset", "color_hex"}
)
_COLOUR_KEYS = frozenset({"color_preset", "color_hex"})

# Stable Homebase capability errors (T-040).
DEVICE_NO_COLOUR_ERROR = "Device does not support colour"
DEVICE_NO_COLOR_TEMP_ERROR = "Device does not support color temperature"
COLOUR_AND_CT_BOTH_ERROR = "Specify colour or color temperature, not both"
INVALID_COLOUR_OR_CT_ERROR = "Invalid colour or color temperature"

_ROOM_ALL_HINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\b(?:zet|doe|turn|switch|maak|make)\s+(?:de\s+|het\s+|the\s+)?({_ROOM_WORD})\s+lampen\b",
        rf"\b(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?({_ROOM_WORD})\s+(?:room\s+)?lights\b",
        rf"\b(?:make|maak)\s+(?:de\s+|het\s+|the\s+)?({_ROOM_WORD})\s+(?:room\s+)?lights\b",
        rf"\b(?:alle\s+)?lichten\s+in\s+(?:de\s+|het\s+|the\s+)?({_ROOM_WORD})\b",
        rf"\blights?\s+in\s+(?:the\s+|de\s+|het\s+)?({_ROOM_WORD})\b",
    )
)

_ROOM_HINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\b(?:licht|lamp)\b.*\bin\s+(?:het\s+|de\s+|the\s+)?({_ROOM_WORD})\b",
        rf"\b(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(?:light|lamp)\s+in\s+(?:the\s+)?({_ROOM_WORD})\b",
        # "turn off the office light" / "turn on the living room light" (singular)
        rf"\b(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?({_ROOM_WORD})\s+(?:light|lamp)\b",
    )
)

_ROOM_ALL_PREFIX = "room:"
_HOUSE_ALL_PREFIX = "all:"
_SKIP_HINT_WORDS = frozenset(
    {"de", "het", "the", "a", "an", "on", "off", "aan", "uit", "alle", "all", "every"}
)
_ARTICLE_REPEAT = re.compile(r"\b(de|het|the)\s+(?:\1\s+)+", re.IGNORECASE)
_COMPOUND_LAMPEN = re.compile(r"\b(\w+)lampen\b", re.IGNORECASE)
_COMPOUND_LAMP = re.compile(r"\b(\w+)lamp\b", re.IGNORECASE)
_FUZZY_ROOM_MAX_DISTANCE = 1

# NL ↔ EN room aliases. Keys are canonical; match against hub `room` from lights.list
# (household hubs use Dutch labels: Woonkamer, Kantoor, … — bidirectional anyway).
_ROOM_ALIAS_GROUPS: dict[str, frozenset[str]] = {
    "woonkamer": frozenset({"woonkamer", "living room", "livingroom", "living"}),
    "kantoor": frozenset({"kantoor", "office", "kantor"}),
    "keuken": frozenset({"keuken", "kitchen"}),
    "slaapkamer": frozenset({"slaapkamer", "bedroom"}),
    "badkamer": frozenset({"badkamer", "bathroom"}),
    "eetkamer": frozenset({"eetkamer", "dining room", "diningroom", "dining"}),
}

_ALIAS_TO_CANONICAL: dict[str, str] = {
    phrase: key
    for key, phrases in _ROOM_ALIAS_GROUPS.items()
    for phrase in phrases | {key}
}
# Compact form without spaces (livingroom) already in sets; also index space-stripped.
for _key, _phrases in _ROOM_ALIAS_GROUPS.items():
    for _p in _phrases:
        _ALIAS_TO_CANONICAL.setdefault(_p.replace(" ", ""), _key)
    _ALIAS_TO_CANONICAL.setdefault(_key.replace(" ", ""), _key)

_MULTIWORD_ALIASES_LONGEST: tuple[str, ...] = tuple(
    sorted(
        {p for phrases in _ROOM_ALIAS_GROUPS.values() for p in phrases if " " in p},
        key=len,
        reverse=True,
    )
)


def _edit_distance(a: str, b: str, *, max_distance: int = _FUZZY_ROOM_MAX_DISTANCE) -> int:
    """Levenshtein distance; returns max_distance + 1 when clearly over the limit."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_distance:
        return max_distance + 1
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
            row_min = min(row_min, curr[j])
        if row_min > max_distance:
            return max_distance + 1
        prev = curr
    return prev[-1]


def _unique_room_names(lights: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for light in lights:
        room = _normalize_room(light.get("room"))
        if room and room not in seen:
            seen.add(room)
            out.append(room)
    return out


def _normalize_room(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_room_phrase(value: str | None) -> str:
    """Lowercase + collapse whitespace for alias lookup."""
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def canonical_room_key(phrase: str | None) -> str:
    """Map NL/EN room phrase (or hub label) to a canonical key; identity if unknown."""
    lower = _normalize_room_phrase(phrase)
    if not lower:
        return ""
    if lower in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[lower]
    compact = lower.replace(" ", "")
    if compact in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[compact]
    return lower


def _alias_phrases_for_hub_room(hub_room: str) -> set[str]:
    """All phrases that should match this hub room label (including aliases)."""
    key = canonical_room_key(hub_room)
    phrases: set[str] = {key, _normalize_room_phrase(hub_room)}
    phrases.update(_ROOM_ALIAS_GROUPS.get(key, frozenset()))
    phrases.add(key.replace(" ", ""))
    phrases.add(_normalize_room_phrase(hub_room).replace(" ", ""))
    return {p for p in phrases if p}


def rooms_equivalent(a: str | None, b: str | None) -> bool:
    """True when two room phrases refer to the same hub room via aliases."""
    ka, kb = canonical_room_key(a), canonical_room_key(b)
    return bool(ka) and ka == kb


def _expand_captured_room_hint(word: str, text: str) -> str:
    """Prefer longest multi-word alias in the message when regex captured a short token."""
    lower_word = _normalize_room_phrase(word)
    if not lower_word or lower_word in _SKIP_HINT_WORDS:
        return word
    lower_text = text.lower()
    for phrase in _MULTIWORD_ALIASES_LONGEST:
        if phrase in lower_text and (
            lower_word == phrase
            or lower_word == phrase.split()[0]
            or lower_word in phrase.split()
        ):
            return phrase
    return word


def _fuzzy_room_names(lights: list[dict[str, Any]], needle: str) -> list[str]:
    """Room names within edit distance of needle (for STT typos like kantor → kantoor)."""
    lower = needle.strip().lower()
    if not lower:
        return []
    matched: list[str] = []
    for room in _unique_room_names(lights):
        if _edit_distance(lower, room) <= _FUZZY_ROOM_MAX_DISTANCE:
            matched.append(room)
            continue
        if any(
            _edit_distance(lower, phrase) <= _FUZZY_ROOM_MAX_DISTANCE
            for phrase in _alias_phrases_for_hub_room(room)
        ):
            matched.append(room)
    return matched


def _lights_for_fuzzy_room(lights: list[dict[str, Any]], needle: str) -> list[dict[str, Any]] | None:
    """Lights in the one fuzzy-matched room, or None when zero / multiple rooms match."""
    matched_rooms = _fuzzy_room_names(lights, needle)
    if len(matched_rooms) != 1:
        return None
    room = matched_rooms[0]
    return [
        light
        for light in lights
        if _normalize_room(light.get("room")) == room
    ]


def _lights_for_aliased_room(lights: list[dict[str, Any]], needle: str) -> list[dict[str, Any]]:
    """Lights whose hub room shares a canonical alias key with needle."""
    key = canonical_room_key(needle)
    if not key:
        return []
    return [
        light
        for light in lights
        if canonical_room_key(light.get("room")) == key
    ]


def _message_for_hints(user_message: str) -> str:
    """Normalize typos that break regex extractors (e.g. 'de de kantoor', 'kantoorlamp')."""
    text = (user_message or "").strip()
    if not text:
        return text
    text = _ARTICLE_REPEAT.sub(r"\1 ", text)
    text = _COMPOUND_LAMPEN.sub(r"\1 lampen", text)
    text = _COMPOUND_LAMP.sub(r"\1 lamp", text)
    return text


def message_for_hints(user_message: str) -> str:
    """Public alias for write-guard and hint extractors (STT compounds, doubled articles)."""
    return _message_for_hints(user_message)


def looks_like_dirigera_device_id(value: str) -> bool:
    """True when value looks like a Dirigera hub device id (UUID suffix)."""
    s = value.strip()
    return len(s) > 20 and "-" in s


def extract_room_all_hint(user_message: str) -> str | None:
    """Room name when user wants every lamp in that room (e.g. woonkamer lampen)."""
    text = _message_for_hints(user_message)
    if not text:
        return None
    for pattern in _ROOM_ALL_HINT_PATTERNS:
        match = pattern.search(text)
        if match:
            word = _expand_captured_room_hint(match.group(1).strip(), text)
            if word.lower() not in _SKIP_HINT_WORDS:
                return word
    return None


def extract_room_hint(user_message: str) -> str | None:
    """Room name for a single-lamp toggle (e.g. licht aan in het kantoor)."""
    if extract_room_all_hint(user_message):
        return None
    text = _message_for_hints(user_message)
    if not text:
        return None
    for pattern in _ROOM_HINT_PATTERNS:
        match = pattern.search(text)
        if match:
            word = _expand_captured_room_hint(match.group(1).strip(), text)
            if word.lower() not in _SKIP_HINT_WORDS:
                return word
    return None


def extract_lamp_name_hint(user_message: str) -> str | None:
    """Best-effort single-lamp name from the user's latest message."""
    if extract_room_all_hint(user_message):
        return None
    # Singular "… office light" / "… living room light" is a room hint, not a lamp name.
    if extract_room_hint(user_message):
        text = _message_for_hints(user_message)
        if re.search(
            rf"\b(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?{_ROOM_WORD}\s+(?:light|lamp)\b",
            text,
            re.IGNORECASE,
        ):
            return None
    text = _message_for_hints(user_message)
    if not text:
        return None
    skip = _SKIP_HINT_WORDS
    for pattern in _LAMP_NAME_HINT_PATTERNS:
        match = pattern.search(text)
        if match:
            word = match.group(1).strip()
            if word.lower() not in skip:
                return word
    return None


def prefer_device_id_for_set_state(user_message: str, model_device_id: str) -> str:
    """Prefer user intent over a model-copied Dirigera uuid."""
    from brain.mcp.party_mode import user_message_requests_house_wide_lights

    if user_message_requests_house_wide_lights(user_message):
        return _HOUSE_ALL_PREFIX
    room_all = extract_room_all_hint(user_message)
    if room_all:
        return f"{_ROOM_ALL_PREFIX}{room_all}"
    lamp_hint = extract_lamp_name_hint(user_message)
    if lamp_hint:
        return lamp_hint
    room_hint = extract_room_hint(user_message)
    if room_hint:
        return room_hint
    # Reject model-invented house-wide target without classifier validation.
    raw = model_device_id.strip()
    if is_house_all_phrase(raw):
        return ""
    return raw


def infer_light_on_from_user_message(user_message: str) -> bool | None:
    """Infer on/off from aan/uit/on/off in the user's latest message."""
    text = (user_message or "").strip().lower()
    if not text:
        return None
    if re.search(r"\b(?:uit|off|uitzetten)\b", text):
        return False
    if re.search(r"\b(?:aan|on)\b", text):
        return True
    return None


def infer_brightness_from_user_message(user_message: str) -> int | None:
    """Absolute brightness 0–100 from a percent in the message, if any."""
    match = _BRIGHTNESS_RE.search(user_message or "")
    if not match:
        return None
    value = int(match.group(1))
    return max(0, min(100, value))


def infer_color_temp_kelvin_from_user_message(user_message: str) -> int | None:
    """Kelvin from NNNNK / kelvin, or warm≈2700 / cool≈4000 defaults."""
    text = user_message or ""
    kelvin_match = _KELVIN_RE.search(text)
    if kelvin_match:
        return int(kelvin_match.group(1))
    # Explicit Kelvin wins over warm/cool adjectives; check kelvin first above.
    if _WARM_RE.search(text):
        return _WARM_KELVIN
    if _COOL_RE.search(text):
        return _COOL_KELVIN
    return None


def infer_color_preset_from_user_message(user_message: str) -> str | None:
    """Map a tiny NL/EN colour name to a Tradfri chromatic preset id.

    Prefers the colour after op/to; otherwise the last colour token when several
    appear (so "Make purple lamp red" → red, not purple). Ignores a lone colour
    word that is only the lamp name in an on/off toggle.
    """
    text = user_message or ""
    colour_alt = "|".join(
        re.escape(k) for k in sorted(_COLOUR_NAME_TO_PRESET, key=len, reverse=True)
    )
    after_op = re.search(
        rf"\b(?:op|to)\s+({colour_alt})\b", text, re.IGNORECASE
    )
    if after_op:
        return _COLOUR_NAME_TO_PRESET.get(after_op.group(1).lower())

    matches = list(_COLOUR_WORD_RE.finditer(text))
    if not matches:
        return None
    if len(matches) > 1:
        return _COLOUR_NAME_TO_PRESET.get(matches[-1].group(1).lower())

    # Single colour token: only when an appearance imperative is present and
    # this is not a plain on/off of a colour-named lamp ("turn on purple lamp").
    if re.search(r"\b(?:aan|uit|on|off|uitzetten)\b", text, re.IGNORECASE):
        return None
    if not re.search(r"\b(?:make|maak|zet|doe|turn)\b", text, re.IGNORECASE):
        return None
    return _COLOUR_NAME_TO_PRESET.get(matches[0].group(1).lower())


def _has_appearance_args(args: dict[str, Any]) -> bool:
    return any(k in args for k in _APPEARANCE_KEYS)


def _has_colour_args(args: dict[str, Any]) -> bool:
    return any(k in args and args[k] is not None for k in _COLOUR_KEYS)


def _strip_appearance(args: dict[str, Any]) -> None:
    for key in _APPEARANCE_KEYS:
        args.pop(key, None)


def _strip_colour(args: dict[str, Any]) -> None:
    for key in _COLOUR_KEYS:
        args.pop(key, None)


def _apply_colour_ct_xor(
    args: dict[str, Any],
    *,
    user_ct: bool,
    user_colour: bool,
) -> None:
    """Ensure colour and color_temp_kelvin are never both present."""
    has_ct = "color_temp_kelvin" in args and args["color_temp_kelvin"] is not None
    has_colour = _has_colour_args(args)
    if not (has_ct and has_colour):
        return
    if user_ct and not user_colour:
        _strip_colour(args)
    elif user_colour and not user_ct:
        args.pop("color_temp_kelvin", None)
    elif user_ct:
        # Both inferred from user — warmth wins (handoff precedence).
        _strip_colour(args)
    else:
        # Model-only conflict: drop colour, keep CT.
        _strip_colour(args)


def build_set_state_args_from_user_message(
    user_message: str, model_args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Merge model args with lamp name/room, on/off, and appearance from the user message."""
    base = dict(model_args or {})
    raw_id = str(base.get("device_id", ""))
    base["device_id"] = prefer_device_id_for_set_state(user_message, raw_id)

    brightness = infer_brightness_from_user_message(user_message)
    color_temp = infer_color_temp_kelvin_from_user_message(user_message)
    color_preset = infer_color_preset_from_user_message(user_message)
    user_ct = color_temp is not None
    user_colour = color_preset is not None

    if brightness is not None:
        base["brightness"] = brightness
    if color_temp is not None:
        base["color_temp_kelvin"] = color_temp
    if color_preset is not None:
        base["color_preset"] = color_preset
        # Prefer preset over free hex when user named a colour.
        base.pop("color_hex", None)
    elif "color_preset" in base and "color_hex" in base:
        # Model sent both — keep preset only.
        base.pop("color_hex", None)

    on_hint = infer_light_on_from_user_message(user_message)
    if on_hint is False:
        base["on"] = False
        _strip_appearance(base)
    elif on_hint is True:
        base["on"] = True
    elif _has_appearance_args(base):
        base["on"] = True

    _apply_colour_ct_xor(base, user_ct=user_ct, user_colour=user_colour)
    return base


def light_set_state_args_from_user_message(user_message: str) -> dict[str, Any] | None:
    """Ready-to-call set_state args when user message fully specifies target + on/off."""
    args = build_set_state_args_from_user_message(user_message, {})
    device_id = args.get("device_id")
    if not device_id or not str(device_id).strip():
        return None
    if "on" not in args:
        return None
    out: dict[str, Any] = {"device_id": str(device_id), "on": bool(args["on"])}
    for key in ("brightness", "color_temp_kelvin", "color_preset", "color_hex"):
        if key in args and args[key] is not None:
            out[key] = args[key]
    return out


def args_request_colour(args: dict[str, Any]) -> bool:
    """True when set_state args include a colour mode field."""
    return _has_colour_args(args)


def args_request_color_temp(args: dict[str, Any]) -> bool:
    return "color_temp_kelvin" in args and args.get("color_temp_kelvin") is not None


def capability_error_for_light(
    light: dict[str, Any], args: dict[str, Any]
) -> str | None:
    """Return a stable T-040 error when list explicitly denies a capability."""
    if args_request_colour(args) and light.get("supports_color") is False:
        return DEVICE_NO_COLOUR_ERROR
    if args_request_color_temp(args) and light.get("supports_color_temp") is False:
        return DEVICE_NO_COLOR_TEMP_ERROR
    return None


def house_wide_set_state_args_from_user_message(
    user_message: str,
) -> dict[str, Any] | None:
    """Args for classifier-validated house-wide set_state (all:)."""
    from brain.mcp.party_mode import (
        should_reroute_party_to_house_wide,
        user_message_requests_house_wide_lights,
    )

    if not (
        user_message_requests_house_wide_lights(user_message)
        or should_reroute_party_to_house_wide(user_message)
    ):
        return None
    on_hint = infer_light_on_from_user_message(user_message)
    if on_hint is None:
        return None
    return {"device_id": _HOUSE_ALL_PREFIX, "on": on_hint}


def user_message_requests_light_write(text: str) -> bool:
    """True when the user asked to toggle lamp(s) this turn."""
    from brain.mcp.party_mode import user_message_requests_house_wide_lights
    from brain.mcp.write_guard import user_message_requests_write

    if not user_message_requests_write(text):
        return False
    normalized = (text or "").strip()
    if user_message_requests_house_wide_lights(normalized):
        return True
    if (
        extract_room_all_hint(normalized)
        or extract_lamp_name_hint(normalized)
        or extract_room_hint(normalized)
    ):
        return True
    return bool(
        re.search(
            r"\b(turn\s+(on|off)|switch|zet|doe|lamp|licht|dim)\b",
            normalized,
            re.IGNORECASE,
        )
    )


def is_room_all_phrase(phrase: str) -> bool:
    return phrase.strip().lower().startswith(_ROOM_ALL_PREFIX)


def is_house_all_phrase(phrase: str) -> bool:
    return phrase.strip().lower().startswith(_HOUSE_ALL_PREFIX)


def room_phrase_from_target(phrase: str) -> str:
    return phrase.strip()[len(_ROOM_ALL_PREFIX) :].strip()


def lights_for_house_wide(lights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lamps includable in house-wide fan-out (skip reachable: false only)."""
    return [
        light
        for light in lights
        if light.get("id") and light.get("reachable") is not False
    ]


def lights_in_room(lights: list[dict[str, Any]], room_phrase: str) -> list[dict[str, Any]]:
    """All lights whose room matches room_phrase (exact, partial, NL/EN alias, or fuzzy)."""
    lower = room_phrase.strip().lower()
    exact = [
        light
        for light in lights
        if _normalize_room(light.get("room")) == lower
    ]
    if exact:
        return exact
    aliased = _lights_for_aliased_room(lights, lower)
    if aliased:
        return aliased
    partial = [
        light
        for light in lights
        if lower in _normalize_room(light.get("room"))
    ]
    if partial:
        return partial
    fuzzy = _lights_for_fuzzy_room(lights, lower)
    return fuzzy if fuzzy is not None else []


def resolve_set_state_device_ids(
    lights: list[dict[str, Any]], phrase: str
) -> tuple[list[str], str | None]:
    """Resolve one or more device ids. Returns (ids, error_message)."""
    needle = phrase.strip()
    if not needle:
        return [], "empty device_id"

    if is_house_all_phrase(needle):
        matches = lights_for_house_wide(lights)
        if not matches:
            return [], "no includable lights for house-wide set_state"
        return [str(light["id"]) for light in matches if light.get("id")], None

    if is_room_all_phrase(needle):
        room = room_phrase_from_target(needle)
        matches = lights_in_room(lights, room)
        if not matches:
            return [], f"no lights in room '{room}'"
        return [str(light["id"]) for light in matches if light.get("id")], None

    resolved = resolve_light(lights, needle)
    if resolved.status == "found" and resolved.device_id:
        return [resolved.device_id], None
    if resolved.status == "ambiguous":
        labels = [
            f"{light.get('name') or '?'} ({light.get('room') or '?'})"
            for light in resolved.matches
        ]
        return [], f"ambiguous: {', '.join(labels)}"
    picked = pick_light_id(lights, needle)
    if picked:
        return [picked], None
    return [], f"no light matching '{needle}'"


def _parse_ndjson_objects(body: str) -> list[dict[str, Any]] | None:
    """Parse one or more concatenated JSON objects (some MCP mocks omit array wrapper)."""
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    idx = 0
    text = body.strip()
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict) and obj.get("id"):
            items.append(obj)
        idx = end
    return items if items else None


def parse_lights_list(raw: str) -> list[dict[str, Any]] | None:
    if raw.startswith("error:"):
        return None
    body = raw.strip()
    # Strip leading Note: lines (status / set_state presentation wrappers).
    while body.startswith("Note:") or (
        "\n" in body and body.split("\n", 1)[0].startswith("Note:")
    ):
        if "\n" not in body:
            return None
        body = body.split("\n", 1)[1].strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        ndjson = _parse_ndjson_objects(body)
        return ndjson
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and parsed.get("id") and parsed.get("name"):
        return [parsed]
    ndjson = _parse_ndjson_objects(body)
    if ndjson:
        return ndjson
    return None


@dataclass(frozen=True)
class LightResolveResult:
    status: Literal["found", "ambiguous", "not_found"]
    device_id: str | None = None
    matches: tuple[dict[str, Any], ...] = ()


def resolve_light(lights: list[dict[str, Any]], phrase: str) -> LightResolveResult:
    """Map user/model phrase to one Dirigera device_id when unique."""
    needle = phrase.strip()
    if not needle:
        return LightResolveResult(status="not_found")

    by_id = {str(light["id"]): light for light in lights if light.get("id")}
    if needle in by_id:
        return LightResolveResult(status="found", device_id=needle)

    lower = needle.lower()
    exact_name = [light for light in lights if (light.get("name") or "").strip().lower() == lower]
    if len(exact_name) == 1:
        return LightResolveResult(status="found", device_id=str(exact_name[0]["id"]))
    if len(exact_name) > 1:
        return LightResolveResult(status="ambiguous", matches=tuple(exact_name))

    exact_room = [
        light
        for light in lights
        if _normalize_room(light.get("room")) == lower
    ]
    if len(exact_room) == 1:
        return LightResolveResult(status="found", device_id=str(exact_room[0]["id"]))
    if len(exact_room) > 1:
        return LightResolveResult(status="ambiguous", matches=tuple(exact_room))

    alias_room = _lights_for_aliased_room(lights, lower)
    # Only treat as room-alias resolve when the phrase is an alias/hub room, not a lamp name.
    if alias_room and (
        lower in _ALIAS_TO_CANONICAL
        or lower.replace(" ", "") in _ALIAS_TO_CANONICAL
        or any(rooms_equivalent(lower, light.get("room")) for light in lights)
    ):
        if len(alias_room) == 1:
            return LightResolveResult(status="found", device_id=str(alias_room[0]["id"]))
        if len(alias_room) > 1:
            return LightResolveResult(status="ambiguous", matches=tuple(alias_room))

    partial_name = [
        light
        for light in lights
        if lower in (light.get("name") or "").strip().lower()
    ]
    if len(partial_name) == 1:
        return LightResolveResult(status="found", device_id=str(partial_name[0]["id"]))
    if len(partial_name) > 1:
        return LightResolveResult(status="ambiguous", matches=tuple(partial_name))

    partial_room = [
        light
        for light in lights
        if lower in _normalize_room(light.get("room"))
    ]
    if len(partial_room) == 1:
        return LightResolveResult(status="found", device_id=str(partial_room[0]["id"]))
    if len(partial_room) > 1:
        return LightResolveResult(status="ambiguous", matches=tuple(partial_room))

    fuzzy_room = _lights_for_fuzzy_room(lights, lower)
    if fuzzy_room is not None:
        if len(fuzzy_room) == 1:
            return LightResolveResult(status="found", device_id=str(fuzzy_room[0]["id"]))
        return LightResolveResult(status="ambiguous", matches=tuple(fuzzy_room))

    return LightResolveResult(status="not_found")


def pick_light_id(lights: list[dict[str, Any]], phrase: str) -> str | None:
    result = resolve_light(lights, phrase)
    return result.device_id if result.status == "found" else None


def light_resolve_error(code: str, message: str) -> str:
    payload = {"error": {"code": code, "message": message, "retryable": False}}
    return f"error: {json.dumps(payload, ensure_ascii=False)}"


def light_not_found_error(phrase: str, *, known: list[str] | None = None) -> str:
    hint = (
        f"No IKEA light matching '{phrase}'. Call homebase.lights.list first and pass "
        "the lamp **name** or **room** as device_id, or the id from the list."
    )
    if known:
        hint += f" Known lights: {', '.join(known)}."
    return light_resolve_error("not_found", hint)


def light_ambiguous_error(phrase: str, matches: list[dict[str, Any]]) -> str:
    labels = [
        f"{light.get('name') or '?'} ({light.get('room') or '?'})"
        for light in matches
    ]
    hint = (
        f"Multiple lights match '{phrase}': {', '.join(labels)}. "
        "Ask the user which lamp, or pass an exact name."
    )
    return light_resolve_error("ambiguous", hint)


_LIGHTS_LIST_STATUS_NOTE = (
    "Note: for 'which lights are on' / status questions — a lamp counts as **on** only when "
    "`isOn: true` **and** `reachable` is not false. Treat `reachable: false` as **off** "
    "(mesh offline). If no lamps are effectively on, reply in one short sentence that all "
    "IKEA lights are off — do not narrate every room or name offline lamps. When naming "
    "lamps that are on, use name + room; do not mention reachable/offline unless asked."
)

_LIGHTS_SET_STATE_NOTE = (
    "Note: homebase.lights.set_state succeeded when success is true. "
    "Lights toggles are not in homebase.changes v1 — no revert. "
    "When devices_toggled > 1, every listed lamp was updated — name each in the reply."
)


def light_is_effectively_on(light: dict[str, Any]) -> bool:
    """Household status rule: unreachable lamps count as off."""
    if light.get("reachable") is False:
        return False
    return light.get("isOn") is True


def present_lights_list_json(text: str) -> str:
    """Annotate lights.list for status replies (unreachable = off; empty → all off)."""
    stripped = text.strip()
    if not stripped or stripped.startswith("error:"):
        return text
    lights = parse_lights_list(stripped)
    if lights is None:
        return text
    on_lamps = [light for light in lights if light_is_effectively_on(light)]
    if not on_lamps:
        status_line = (
            "Note: status summary — effectively_on_count=0 all_off=true. "
            "Reply that all IKEA lights are off (one short sentence)."
        )
    else:
        labels = ", ".join(
            f"{light.get('name') or '?'} ({light.get('room') or '?'})"
            for light in on_lamps
        )
        status_line = (
            f"Note: status summary — effectively_on_count={len(on_lamps)} "
            f"all_off=false; on: {labels}. Name only these as on."
        )
    body = json.dumps(lights, ensure_ascii=False)
    return f"{_LIGHTS_LIST_STATUS_NOTE}\n{status_line}\n{body}"


_LIGHTS_UNREACHABLE_NOTE = (
    "Note: hub reports reachable: false — isOn from list may be stale. "
    "Confirm from success: true; do not tell the user the lamp was already on/off "
    "from list data alone."
)

_LIGHTS_SET_STATE_FAIL_NOTE = (
    "Note: homebase.lights.set_state failed (success is false). "
    "Quote the JSON `error` string to the user (exact Homebase text); "
    "do not claim the lamp changed. If name/room are present, say which lamp failed. "
    "On 'Unknown or stale device_id', call lights.list and pass name/room — never invent an id. "
    "On 'Device unreachable (Zigbee mesh)', the hub refused the write (mesh); try again later."
)

# Stable Homebase lights.set_state error strings (contracts/homebase.tools.yaml).
HOMEBASE_LIGHTS_SET_STATE_ERRORS = frozenset(
    {
        "Dirigera not configured",
        "Failed to reach Dirigera hub",
        "Dirigera authentication failed",
        "Unknown or stale device_id",
        "Device unreachable (Zigbee mesh)",
        DEVICE_NO_COLOUR_ERROR,
        DEVICE_NO_COLOR_TEMP_ERROR,
        COLOUR_AND_CT_BOTH_ERROR,
        INVALID_COLOUR_OR_CT_ERROR,
    }
)
STALE_DEVICE_ID_ERROR = "Unknown or stale device_id"
MESH_UNREACHABLE_ERROR = "Device unreachable (Zigbee mesh)"


def format_capability_failure(
    error: str, *, light: dict[str, Any] | None = None
) -> str:
    """JSON failure payload matching Homebase set_state shape, for the model."""
    payload: dict[str, Any] = {"success": False, "error": error}
    if light:
        if light.get("id") is not None:
            payload["device_id"] = light.get("id")
        if light.get("name") is not None:
            payload["name"] = light.get("name")
        if light.get("room") is not None:
            payload["room"] = light.get("room")
    body = json.dumps(payload, ensure_ascii=False)
    return f"{_LIGHTS_SET_STATE_FAIL_NOTE}\n{body}"


def _strip_leading_notes(text: str) -> str:
    body = text.strip()
    while body.startswith("Note:") or (
        "\n" in body and body.split("\n", 1)[0].startswith("Note:")
    ):
        if "\n" not in body:
            return ""
        body = body.split("\n", 1)[1].strip()
    return body


def extract_set_state_error_message(text: str) -> str | None:
    """Pull Homebase error string from a set_state tool result, if present."""
    body = _strip_leading_notes(text)
    if not body:
        return None
    if body.startswith("error:"):
        rest = body[len("error:") :].strip()
        try:
            parsed = json.loads(rest)
        except json.JSONDecodeError:
            return rest or None
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if isinstance(err, str) and err:
                return err
            if parsed.get("message"):
                return str(parsed["message"])
        return rest or None
    if not body.startswith("{"):
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    err = parsed.get("error")
    if isinstance(err, str) and err:
        return err
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if parsed.get("success") is False:
        return "success is not true"
    return None


def is_stale_device_id_error(text: str) -> bool:
    return extract_set_state_error_message(text) == STALE_DEVICE_ID_ERROR


def format_set_state_failure_for_model(text: str) -> str:
    """Ensure the model sees a clear failure with Homebase error text."""
    detail = extract_set_state_error_message(text) or "success is not true"
    return (
        f"error: homebase.lights.set_state failed: {detail}. "
        "Quote that error to the user; do not claim the lamp changed."
    )


def present_lights_set_state_batch(
    results: list[dict[str, Any]],
    *,
    on: bool,
    room: str | None = None,
    skipped: list[dict[str, Any]] | None = None,
    house_wide: bool = False,
) -> str:
    """Summarize multi-lamp set_state for the model."""
    names = [str(r.get("name") or r.get("device_id") or "?") for r in results]
    skipped = skipped or []
    skipped_names = [
        str(r.get("name") or r.get("device_id") or "?") for r in skipped
    ]
    payload: dict[str, Any] = {
        "success": True,
        "on": on,
        "devices_toggled": len(results),
        "device_ids": [r.get("device_id") for r in results],
        "names": names,
    }
    if room:
        payload["room"] = room
    if house_wide:
        payload["house_wide"] = True
    if skipped_names:
        payload["skipped"] = skipped_names
        payload["devices_skipped"] = len(skipped_names)
    body = json.dumps(payload, ensure_ascii=False)
    return f"{_LIGHTS_SET_STATE_NOTE}\n{body}"


def present_lights_set_state_json(
    text: str,
    *,
    light: dict[str, Any] | None = None,
) -> str:
    """Add success/failure hint and optional list snapshot for the model."""
    stripped = text.strip()
    if not stripped or stripped.startswith("error:") or stripped[:1] != "{":
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict):
        return text

    if parsed.get("success") is False or (
        "error" in parsed and parsed.get("success") is not True
    ):
        if light:
            if light.get("name") is not None:
                parsed.setdefault("name", light.get("name"))
            if light.get("room") is not None:
                parsed.setdefault("room", light.get("room"))
        body = json.dumps(parsed, ensure_ascii=False)
        return f"{_LIGHTS_SET_STATE_FAIL_NOTE}\n{body}"

    if parsed.get("error") or not parsed.get("success"):
        return text

    if light:
        if light.get("name") is not None:
            parsed.setdefault("name", light.get("name"))
        if light.get("room") is not None:
            parsed.setdefault("room", light.get("room"))
        if "isOn" in light:
            parsed["prior_isOn"] = light.get("isOn")
        if "reachable" in light:
            parsed["reachable"] = light.get("reachable")
    note_parts: list[str] = []
    if light and light.get("reachable") is False:
        note_parts.append(_LIGHTS_UNREACHABLE_NOTE)
    note_parts.append(_LIGHTS_SET_STATE_NOTE)
    body = json.dumps(parsed, ensure_ascii=False)
    return f"{note_parts[0]}\n{body}" if len(note_parts) == 1 else f"{note_parts[0]}\n{note_parts[1]}\n{body}"


def set_state_tool_succeeded(text: str) -> bool:
    if tool_result_is_error(text):
        return False
    body = _strip_leading_notes(text)
    if not body.startswith("{"):
        return False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get("devices_toggled", 0) > 1:
        return parsed.get("success") is True
    return parsed.get("success") is True
