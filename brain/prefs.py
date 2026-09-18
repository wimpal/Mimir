"""Allowlisted user Preferences — normalize, format for system inject."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from brain.mcp.names import display_service_name

# Stable order for GET /v1/preferences and TUI /settings.
PREFERENCE_KEYS: tuple[str, ...] = ("favorite_genres", "tone")
ALLOWED_KEYS = frozenset(PREFERENCE_KEYS)

DEFAULT_BIRTHDAY = "2026-08-26"


def parse_birthday(raw: str | None) -> date | None:
    """Parse ISO YYYY-MM-DD birthday; None if missing/invalid."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def birthday_month_day_matches(today: date, birthday: date) -> bool:
    """True when local today shares month-day with birthday (annual)."""
    return today.month == birthday.month and today.day == birthday.day


def normalize_preference_value(key: str, value: Any) -> str | None:
    """Return stored string form, or None if invalid for the key."""
    if key not in ALLOWED_KEYS:
        return None

    if key == "tone":
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text if text else None

    # favorite_genres — JSON array of non-empty strings
    genres: list[str]
    if isinstance(value, list):
        genres = [str(g).strip() for g in value if str(g).strip()]
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, list):
                return None
            genres = [str(g).strip() for g in parsed if str(g).strip()]
        else:
            genres = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        return None

    if not genres:
        return None
    return json.dumps(genres, separators=(",", ":"))


def format_prefs_block(prefs: dict[str, str]) -> str:
    """Short markdown block for system prompt; omit unset / unknown keys."""
    lines: list[str] = []
    if "favorite_genres" in prefs:
        try:
            genres = json.loads(prefs["favorite_genres"])
        except json.JSONDecodeError:
            genres = None
        if isinstance(genres, list) and genres:
            lines.append(f"- favorite_genres: {', '.join(str(g) for g in genres)}")
    if "tone" in prefs and prefs["tone"].strip():
        lines.append(f"- tone: {prefs['tone'].strip()}")
    if not lines:
        return ""
    return "## Known preferences\n\n" + "\n".join(lines)


def format_clock_block(
    *,
    timezone: str,
    birthday: str | None = None,
    now: datetime | None = None,
) -> str:
    """Inject household-local date/time so relative periods resolve correctly."""
    try:
        tz = ZoneInfo(timezone)
        clock = now if now is not None else datetime.now(tz)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=tz)
        else:
            clock = clock.astimezone(tz)
    except Exception:  # noqa: BLE001 — bad tz name; omit block
        return ""
    lines = [
        "## Current date and time",
        "",
        f"- now: {clock.strftime('%Y-%m-%dT%H:%M:%S')} ({timezone})",
        f"- today: {clock.date().isoformat()}",
        '- Use this for "today", "this month", "last month" / "vorige maand", etc. '
        "Never guess the year or month.",
    ]
    bday = parse_birthday(birthday)
    if bday is not None and birthday_month_day_matches(clock.date(), bday):
        lines.extend(
            [
                "",
                f"- Today is Mimir's birthday ({bday.isoformat()} and annually "
                "on this month-day). Acknowledge briefly if the user greets or "
                "asks — keep it short, no over-the-top party.",
            ]
        )
    return "\n".join(lines)


def format_unavailable_services_block(unavailable: list[str]) -> str:
    """Note for the system prompt when MCP services failed to connect."""
    if not unavailable:
        return ""
    lines = [
        f"- {display_service_name(sid)} is unreachable — do not invent data from that "
        f"service; tell the user plainly that you cannot reach {display_service_name(sid)} "
        "right now."
        for sid in unavailable
    ]
    return "## Unavailable services\n\n" + "\n".join(lines)


def build_system_prompt(
    base: str,
    prefs: dict[str, str],
    *,
    unavailable_services: list[str] | None = None,
    timezone: str | None = None,
    birthday: str | None = None,
    now: datetime | None = None,
) -> str:
    parts = [base.rstrip()]
    if timezone:
        clock = format_clock_block(
            timezone=timezone, birthday=birthday, now=now
        )
        if clock:
            parts.append(clock)
    block = format_prefs_block(prefs)
    if block:
        parts.append(block)
    unavail = format_unavailable_services_block(unavailable_services or [])
    if unavail:
        parts.append(unavail)
    return "\n\n".join(parts) + "\n"
