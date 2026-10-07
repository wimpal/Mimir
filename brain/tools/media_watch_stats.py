"""media_watch_stats — films we last watched in a window (T-131).

Jellyfin keeps one last-played date per item. Rewatch a film and that date
moves; the server cannot say how many times something was watched, or total
hours. This tool therefore answers exactly one honest question: how many
films had their **last play** inside the window. The caveat lives in the
payload, not only the reply, so a paraphrase cannot drop it.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from brain.config import Settings
from brain.db import parse_jellyfin_datetime
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.tools import Tool
from brain.tools.media_lookup import _client_from_settings

_PERIODS = ("this_year", "last_year", "last_month", "last_7_days", "last_30_days", "custom")
_PERIOD_LABELS = {
    "this_year": "this calendar year",
    "last_year": "last calendar year",
    "last_month": "last calendar month",
    "last_7_days": "last 7 days",
    "last_30_days": "last 30 days",
    "custom": "custom window",
}
_RECENT_TITLES = 5

_NOTE_REWATCH = (
    "Jellyfin keeps only the last play per film; a rewatched film counts once"
)
_NOTE_SERIES = (
    "series and episodes are not counted — that is a different number"
)
_NOTE_NOT_SUPPORTED = (
    "times watched and hours watched are not available from Jellyfin"
)
_NOTE_TRUNCATED = (
    "results may be truncated at the per-source page cap; the count may be a floor"
)


def _home_tz(settings: Settings) -> ZoneInfo:
    name = (settings.location.timezone or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def _parse_iso_date(raw: str, *, field: str) -> date | None:
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _resolve_window(
    settings: Settings,
    *,
    period: str,
    since: str | None,
    until: str | None,
) -> tuple[datetime, datetime, str] | str:
    """Return ``(start, end, label)`` datetimes in the home tz, or an error string."""
    if period not in _PERIODS:
        return (
            "error: period must be one of "
            + ", ".join(_PERIODS)
        )

    tz = _home_tz(settings)
    now = datetime.now(tz)

    if period == "this_year":
        start = datetime(now.year, 1, 1, tzinfo=tz)
        end = datetime(now.year + 1, 1, 1, tzinfo=tz)
    elif period == "last_year":
        start = datetime(now.year - 1, 1, 1, tzinfo=tz)
        end = datetime(now.year, 1, 1, tzinfo=tz)
    elif period == "last_month":
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if first_this.month == 1:
            first_prev = first_this.replace(year=first_this.year - 1, month=12)
        else:
            first_prev = first_this.replace(month=first_this.month - 1)
        start = first_prev
        end = first_this
    elif period == "last_7_days":
        start = now - timedelta(days=7)
        end = now
    elif period == "last_30_days":
        start = now - timedelta(days=30)
        end = now
    else:  # custom
        if not since or not until:
            return "error: period=custom requires both since and until (YYYY-MM-DD)"
        start_date = _parse_iso_date(since, field="since")
        end_date = _parse_iso_date(until, field="until")
        if start_date is None or end_date is None:
            return "error: since and until must be YYYY-MM-DD dates"
        if start_date > end_date:
            return "error: since must not be after until"
        start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
        end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=tz) + timedelta(days=1)

    return start, end, _PERIOD_LABELS[period]


def _film_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    if str(raw.get("Type") or "") != "Movie":
        return None
    item_id = raw.get("Id")
    name = raw.get("Name")
    if not item_id or not name:
        return None
    year = raw.get("ProductionYear")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None
    return {
        "id": str(item_id),
        "title": str(name),
        "year": year,
        "last_played_at": parse_jellyfin_datetime(
            (raw.get("UserData") or {}).get("LastPlayedDate")
            if isinstance(raw.get("UserData"), dict)
            else None
        ),
    }


def media_watch_stats(
    settings: Settings,
    *,
    period: str = "this_year",
    since: str | None = None,
    until: str | None = None,
    client: JellyfinClient | None = None,
) -> str:
    """Films whose last play fell in the window; compact JSON or an ``error:`` string."""
    window = _resolve_window(settings, period=period, since=since, until=until)
    if isinstance(window, str):
        return window
    start, end, label = window

    owns_client = client is None
    jf = client if client is not None else _client_from_settings(settings)
    if jf is None:
        return "error: jellyfin not configured (url, api_key, user_id required)"

    try:
        raws, truncated = jf.list_played_movies()
    except JellyfinError as exc:
        return f"error: {exc}"
    finally:
        if owns_client and jf is not None:
            jf.close()

    seen: set[str] = set()
    matched: list[dict[str, Any]] = []
    for raw in raws:
        row = _film_row(raw)
        if row is None:
            continue
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        last_played = row["last_played_at"]
        if last_played is None:
            continue
        try:
            played_local = datetime.fromisoformat(last_played).astimezone(start.tzinfo)
        except ValueError:
            continue
        if start <= played_local < end:
            matched.append(row)

    matched.sort(key=lambda r: r["last_played_at"] or "", reverse=True)
    empty = len(matched) == 0
    notes = [_NOTE_REWATCH, _NOTE_SERIES, _NOTE_NOT_SUPPORTED]
    if empty:
        notes.append("no films had their last play in this window")
    if truncated:
        notes.append(_NOTE_TRUNCATED)

    payload = {
        "window": {
            "period": period,
            "since": start.date().isoformat(),
            "until": end.date().isoformat(),
            "timezone": str(start.tzinfo),
            "label": label,
        },
        "counts": {
            "films_last_played_in_window": len(matched),
            "meaning": "films whose last play fell in the window",
        },
        "watch_history": False,
        "watch_counts_supported": False,
        "hours_watched_supported": False,
        "series_counted": False,
        "not_supported": [
            "times watched",
            "hours watched",
            "series/episode counts",
        ],
        "recent_titles": matched[:_RECENT_TITLES],
        "truncated": truncated,
        "empty": empty,
        "notes": notes,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def media_watch_stats_tools(settings: Settings) -> dict[str, Tool]:
    def _execute(
        *,
        period: str = "this_year",
        since: str | None = None,
        until: str | None = None,
    ) -> str:
        return media_watch_stats(settings, period=period, since=since, until=until)

    tool = Tool(
        name="media_watch_stats",
        description=(
            "How many films we last watched in a window. REQUIRED for: 'how many "
            "films did we watch this year?', 'hoeveel films hebben we dit jaar "
            "gekeken?', 'films last month', 'since June', or any count of films "
            "watched in a period. Returns films whose LAST play fell in the "
            "window — a rewatched film counts ONCE. Default window is this "
            "calendar year; pass period (this_year|last_year|last_month|"
            "last_7_days|last_30_days|custom) or since/until ISO dates for an "
            "explicit window. FILMS ONLY: series and episodes are never counted "
            "— if asked about series, say that is a different number and do not "
            "answer with a film count. NEVER report times watched, watch counts, "
            "or hours watched — Jellyfin keeps only the last play, so those are "
            "not available; say so plainly. The payload carries watch_history: "
            "false and a counts.meaning caveat — keep both in the reply so the "
            "number is understood, not trusted. Empty count is a real zero with "
            "the caveat, not an error. Jellyfin down / not configured -> clear "
            "unavailable error, never zero. Read-only: never plays, recommends, "
            "or writes favourites."
        ),
        parameters={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": (
                        "Window preset. One of this_year (default), last_year, "
                        "last_month, last_7_days, last_30_days, custom."
                    ),
                },
                "since": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD (inclusive). Only with period=custom.",
                },
                "until": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD (inclusive). Only with period=custom.",
                },
            },
            "additionalProperties": False,
        },
        execute=_execute,
    )
    return {tool.name: tool}
