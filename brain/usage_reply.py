"""Spoken replies for usage_stats (T-058) — keep free of turn_log/agent imports."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

Locale = Literal["nl", "en"]

_USAGE_ASK = re.compile(
    r"(?i)\b("
    r"how busy|busy have you|usage stats?|how much have you|"
    r"hoe druk|druk ben je|gebruiks?stat|"
    r"hoeveel.*gedaan|how many turns"
    r")\b"
)


def user_asked_about_usage(text: str) -> bool:
    return bool(_USAGE_ASK.search(text or ""))


def parse_usage_payload(result: str) -> dict[str, Any] | None:
    """Parse a successful usage_stats tool result; None on error/malformed."""
    text = (result or "").strip()
    if not text or text.startswith("error:"):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if "turn_count" not in data:
        return None
    return data


def reply_looks_like_usage_json(reply: str) -> bool:
    """True when the assistant pasted tool JSON instead of speaking."""
    text = (reply or "").strip()
    if not text.startswith("{") or "turn_count" not in text:
        return False
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        pass
    if text.count('"turn_count"') >= 1 and text.count("{") >= 1:
        return True
    return False


def format_usage_stats_reply(payload: dict[str, Any], locale: Locale) -> str:
    """Short spoken summary from usage_stats JSON — never raw tool dump."""
    turns = int(payload.get("turn_count") or 0)
    ok = int(payload.get("success_count") or 0)
    failed = int(payload.get("failed_count") or 0)
    tools = int(payload.get("tool_call_count") or 0)
    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    top = payload.get("top_tools") if isinstance(payload.get("top_tools"), list) else []
    milestones = (
        payload.get("milestones_reached")
        if isinstance(payload.get("milestones_reached"), list)
        else []
    )
    voice = payload.get("voice_stt_count")

    if locale == "nl":
        if window.get("kind") == "days" and window.get("days"):
            when = f"de afgelopen {int(window['days'])} dagen"
        else:
            when = "totaal"
        parts = [
            f"Over {when}: {turns} beurten "
            f"({ok} gelukt, {failed} mislukt), {tools} tool-aanroepen."
        ]
        if top:
            bits = []
            for row in top[:5]:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "").strip()
                count = int(row.get("count") or 0)
                if name:
                    bits.append(f"{name} ({count})")
            if bits:
                parts.append("Meest gebruikt: " + ", ".join(bits) + ".")
        if milestones:
            parts.append(
                "Mijlpalen: " + ", ".join(str(m) for m in milestones) + "."
            )
        if isinstance(voice, int):
            parts.append(f"Spraak (STT): {voice} keer.")
        return " ".join(parts)

    if window.get("kind") == "days" and window.get("days"):
        when = f"the last {int(window['days'])} days"
    else:
        when = "all time"
    parts = [
        f"Over {when}: {turns} turns "
        f"({ok} ok, {failed} failed), {tools} tool calls."
    ]
    if top:
        bits = []
        for row in top[:5]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            count = int(row.get("count") or 0)
            if name:
                bits.append(f"{name} ({count})")
        if bits:
            parts.append("Most used: " + ", ".join(bits) + ".")
    if milestones:
        parts.append("Milestones: " + ", ".join(str(m) for m in milestones) + ".")
    if isinstance(voice, int):
        parts.append(f"Voice STT: {voice}.")
    return " ".join(parts)


def needs_usage_stats_fixup(reply: str, payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    return reply_looks_like_usage_json(reply) or not (reply or "").strip()
