"""Light device_id resolution for homebase.lights.set_state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from brain.mcp.errors import tool_result_is_error

_LAMP_NAME_HINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:zet|doe|turn|switch)\s+(?:de\s+|het\s+)?(\w+)\s+lamp\b",
        r"\b(?:turn|switch)\s+(?:on|off)\s+(\w+)\b",
        r"\blamp\s+(\w+)\b",
    )
)

_ROOM_ALL_HINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:zet|doe|turn|switch)\s+(?:de\s+|het\s+)?(\w+)\s+lampen\b",
        r"\b(?:turn|switch)\s+(?:on|off)\s+(?:the\s+)?(\w+)\s+(?:room\s+)?lights\b",
        r"\b(?:alle\s+)?lichten\s+in\s+(?:de\s+)?(\w+)\b",
        r"\blights?\s+in\s+(?:the\s+)?(\w+)\b",
    )
)

_ROOM_ALL_PREFIX = "room:"
_SKIP_HINT_WORDS = frozenset(
    {"de", "het", "the", "a", "an", "on", "off", "aan", "uit", "alle", "all"}
)


def looks_like_dirigera_device_id(value: str) -> bool:
    """True when value looks like a Dirigera hub device id (UUID suffix)."""
    s = value.strip()
    return len(s) > 20 and "-" in s


def _normalize_room(value: str | None) -> str:
    return (value or "").strip().lower()


def extract_room_all_hint(user_message: str) -> str | None:
    """Room name when user wants every lamp in that room (e.g. woonkamer lampen)."""
    text = (user_message or "").strip()
    if not text:
        return None
    for pattern in _ROOM_ALL_HINT_PATTERNS:
        match = pattern.search(text)
        if match:
            word = match.group(1).strip()
            if word.lower() not in _SKIP_HINT_WORDS:
                return word
    return None


def extract_lamp_name_hint(user_message: str) -> str | None:
    """Best-effort single-lamp name from the user's latest message."""
    if extract_room_all_hint(user_message):
        return None
    text = (user_message or "").strip()
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
    room_hint = extract_room_all_hint(user_message)
    if room_hint:
        return f"{_ROOM_ALL_PREFIX}{room_hint}"
    hint = extract_lamp_name_hint(user_message)
    if hint:
        return hint
    return model_device_id.strip()


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


def build_set_state_args_from_user_message(
    user_message: str, model_args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Merge model args with lamp name/room and on/off from the user message."""
    base = dict(model_args or {})
    raw_id = str(base.get("device_id", ""))
    base["device_id"] = prefer_device_id_for_set_state(user_message, raw_id)
    on_hint = infer_light_on_from_user_message(user_message)
    if on_hint is not None:
        base["on"] = on_hint
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
    if "brightness" in args:
        out["brightness"] = args["brightness"]
    return out


def user_message_requests_light_write(text: str) -> bool:
    """True when the user asked to toggle lamp(s) this turn."""
    from brain.mcp.write_guard import user_message_requests_write

    if not user_message_requests_write(text):
        return False
    normalized = (text or "").strip()
    if extract_room_all_hint(normalized) or extract_lamp_name_hint(normalized):
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


def room_phrase_from_target(phrase: str) -> str:
    return phrase.strip()[len(_ROOM_ALL_PREFIX) :].strip()


def lights_in_room(lights: list[dict[str, Any]], room_phrase: str) -> list[dict[str, Any]]:
    """All lights whose room matches room_phrase (case-insensitive, trimmed)."""
    lower = room_phrase.strip().lower()
    exact = [
        light
        for light in lights
        if _normalize_room(light.get("room")) == lower
    ]
    if exact:
        return exact
    return [
        light
        for light in lights
        if lower in _normalize_room(light.get("room"))
    ]


def resolve_set_state_device_ids(
    lights: list[dict[str, Any]], phrase: str
) -> tuple[list[str], str | None]:
    """Resolve one or more device ids. Returns (ids, error_message)."""
    needle = phrase.strip()
    if not needle:
        return [], "empty device_id"

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
    if "\n" in body and body.split("\n", 1)[0].startswith("Note:"):
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


_LIGHTS_SET_STATE_NOTE = (
    "Note: homebase.lights.set_state succeeded when success is true. "
    "Lights toggles are not in homebase.changes v1 — no revert. "
    "When devices_toggled > 1, every listed lamp was updated — name each in the reply."
)


def present_lights_set_state_batch(
    results: list[dict[str, Any]], *, on: bool, room: str | None = None
) -> str:
    """Summarize multi-lamp set_state for the model."""
    names = [str(r.get("name") or r.get("device_id") or "?") for r in results]
    payload = {
        "success": True,
        "on": on,
        "devices_toggled": len(results),
        "device_ids": [r.get("device_id") for r in results],
        "names": names,
    }
    if room:
        payload["room"] = room
    body = json.dumps(payload, ensure_ascii=False)
    return f"{_LIGHTS_SET_STATE_NOTE}\n{body}"


def present_lights_set_state_json(text: str) -> str:
    """Add success hint so the model trusts success: true."""
    stripped = text.strip()
    if not stripped or stripped.startswith("error:") or stripped[:1] != "{":
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict) or parsed.get("error") or not parsed.get("success"):
        return text
    body = json.dumps(parsed, ensure_ascii=False)
    return f"{_LIGHTS_SET_STATE_NOTE}\n{body}"


def set_state_tool_succeeded(text: str) -> bool:
    if tool_result_is_error(text):
        return False
    body = text.strip()
    if "\n" in body and body.split("\n", 1)[0].startswith("Note:"):
        body = body.split("\n", 1)[1].strip()
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
