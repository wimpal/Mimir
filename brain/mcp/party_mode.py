"""Party mode intent and args for homebase.lights.party_mode (T-039 / T-041)."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any

_MAX_DURATION_S = 60
_MIN_DURATION_S = 1

_PARTY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bparty\s+mode\b",
        r"\blet'?s\s+party\b",
        r"\bstart\s+the\s+party\b",
        r"\bdisco\s+mode\b",
        r"\bparty\s+tijd\b",
        r"\bfeestmodus\b",
        r"\bfeest\b",
        r"\bdisco\b",
        r"\b\d+\s*(?:second|seconds|sec|secs|seconde|seconden)\s+party\b",
        r"\bparty\b.*\b\d+\s*(?:second|seconds|sec|secs|seconde|seconden)\b",
    )
)

_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bnot\s+party(?:\s+mode)?\b",
        r"\bno,?\s+not\s+party(?:\s+mode)?\b",
        r"\bno\s+party(?:\s+mode)?\b",
        r"\bdon'?t\s+(?:use\s+)?party(?:\s+mode)?\b",
        r"\bdo\s+not\s+(?:use\s+)?party(?:\s+mode)?\b",
        r"\bgeen\s+(?:feest|party)(?:\s*modus)?\b",
        r"\bgeen\s+feestmodus\b",
        r"\b(niet|geen)\s+(?:de\s+)?party(?:\s+mode)?\b",
        r"\bsimply\s+turn\s+(?:on|off)\s+(?:every|all)\b",
        r"\bgewoon\s+(?:alle\s+)?(?:lampen|lichten)\s+(?:aan|uit)\b",
    )
)

_HOUSE_WIDE_ROOM_WORDS = frozenset(
    {
        "all",
        "every",
        "alle",
        "all the",
        "the",
        "house",
        "huis",
        "the house",
        "het huis",
    }
)

_HOUSE_WIDE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:turn|switch)\s+(?:on|off)\s+(?:every|all)\s+(?:the\s+)?lights?\b",
        r"\b(?:turn|switch)\s+(?:on|off)\s+(?:every|all)\s+(?:the\s+)?lights?\s+in\s+(?:the\s+)?house\b",
        r"\bevery\s+light\s+in\s+(?:the\s+)?house\b",
        r"\ball\s+(?:of\s+)?(?:the\s+)?lights?\s+(?:on|off|aan|uit)\b",
        r"\b(?:turn|switch|doe|zet)\b.*\b(?:all|every)\s+(?:the\s+)?lights?\b",
        r"\b(?:doe|zet)\s+alle\s+(?:lampen|lichten)\s+(?:aan|uit)\b",
        r"\balle\s+(?:lampen|lichten)\s+(?:aan|uit)\b",
        r"\b(?:lampen|lichten)\s+(?:overal|in\s+(?:het\s+)?huis)\s+(?:aan|uit)\b",
        r"\balle\s+(?:lampen|lichten)\s+in\s+(?:het\s+)?huis\s+(?:aan|uit)\b",
        r"\b(?:turn|switch)\s+(?:on|off)\s+every\s+light\b",
        r"\b(?:turn|switch)\s+(?:on|off)\s+all\s+(?:of\s+)?(?:the\s+)?lights?\b",
    )
)

_HOUSE_WIDE_NEGATION = re.compile(
    r"\b(?:don'?t|do\s+not|niet)\b.{0,40}\b(?:turn|switch|doe|zet|alle|all|every)\b",
    re.IGNORECASE,
)
_HOUSE_WIDE_QUESTION = re.compile(
    r"^\s*(?:are|is|which|what|hoe|welke|staan)\b",
    re.IGNORECASE,
)

_DURATION_RE = re.compile(
    r"(\d+)\s*(?:second|seconds|sec|secs|seconde|seconden)\b",
    re.IGNORECASE,
)


class LightsWriteIntent(StrEnum):
    """Precedence: refused_party > party > house_wide > other."""

    REFUSED_PARTY = "refused_party"
    PARTY = "party"
    HOUSE_WIDE = "house_wide"
    OTHER = "other"


def user_message_refuses_party_mode(text: str) -> bool:
    """True when the user rejected party mode this turn."""
    if not (text or "").strip():
        return False
    return any(p.search(text.strip()) for p in _REFUSAL_PATTERNS)


def user_message_requests_house_wide_lights(text: str) -> bool:
    """True for house-wide on/off — not room plurals (*woonkamer lampen*)."""
    if not (text or "").strip():
        return False
    from brain.mcp.lights import extract_room_all_hint, message_for_hints

    normalized = message_for_hints(text)
    if _HOUSE_WIDE_NEGATION.search(normalized) or _HOUSE_WIDE_QUESTION.search(
        normalized
    ):
        return False
    if not any(p.search(normalized) for p in _HOUSE_WIDE_PATTERNS):
        return False
    # "turn on all lights in the living room" is room-scoped, not house-wide.
    room_all = extract_room_all_hint(normalized)
    if room_all and room_all.lower() not in _HOUSE_WIDE_ROOM_WORDS:
        return False
    return True


def user_message_requests_party_mode(text: str) -> bool:
    """True when the user asked for party mode this turn (phrase match only)."""
    if not (text or "").strip():
        return False
    normalized = (text or "").strip()
    return any(p.search(normalized) for p in _PARTY_PATTERNS)


def classify_lights_write_intent(text: str) -> LightsWriteIntent:
    """Classify lights write intent with T-041 precedence."""
    if not (text or "").strip():
        return LightsWriteIntent.OTHER
    if user_message_refuses_party_mode(text):
        return LightsWriteIntent.REFUSED_PARTY
    if user_message_requests_party_mode(text):
        return LightsWriteIntent.PARTY
    if user_message_requests_house_wide_lights(text):
        return LightsWriteIntent.HOUSE_WIDE
    return LightsWriteIntent.OTHER


def party_mode_disallowed_this_turn(text: str) -> bool:
    """True when party_mode must not run this turn.

    Allowed only for explicit party intent. Bare yes/ja confirms stay OTHER and
    fall through to the generic mutation write_guard (pre-existing M3 behaviour).
    """
    intent = classify_lights_write_intent(text)
    return intent != LightsWriteIntent.PARTY


def should_reroute_party_to_house_wide(text: str) -> bool:
    """True when a misrouted party_mode call should become house-wide set_state."""
    intent = classify_lights_write_intent(text)
    if intent == LightsWriteIntent.HOUSE_WIDE:
        return True
    if intent == LightsWriteIntent.REFUSED_PARTY:
        return user_message_requests_house_wide_lights(text)
    return False


def extract_party_duration_seconds(text: str) -> int | None:
    """Parse optional duration from user message; clamp 1–60."""
    match = _DURATION_RE.search(text or "")
    if not match:
        return None
    value = int(match.group(1))
    return max(_MIN_DURATION_S, min(_MAX_DURATION_S, value))


def build_party_mode_args_from_user_message(
    user_message: str,
    model_args: dict[str, Any],
) -> dict[str, Any]:
    """Merge model args with duration extracted from user message."""
    out = dict(model_args)
    extracted = extract_party_duration_seconds(user_message)
    if extracted is not None:
        out["duration_seconds"] = extracted
    elif "duration_seconds" in out:
        try:
            raw = int(float(out["duration_seconds"]))
            out["duration_seconds"] = max(_MIN_DURATION_S, min(_MAX_DURATION_S, raw))
        except (TypeError, ValueError):
            out.pop("duration_seconds", None)
    return out


def _parse_tool_json(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if not body:
        return None
    if "\n" in body and body.split("\n", 1)[0].startswith("Note:"):
        body = body.split("\n", 1)[1].strip()
    if not body.startswith("{"):
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def party_mode_tool_succeeded(result: str) -> bool:
    """True when party_mode tool output reports success."""
    parsed = _parse_tool_json(result)
    if parsed is None:
        return False
    return parsed.get("success") is True
