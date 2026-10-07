"""list_continue_watching — live Jellyfin resume + next-up (T-129)."""

from __future__ import annotations

import json
from typing import Any

from brain.config import Settings
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.tools import Tool
from brain.tools.media_lookup import (
    _client_from_settings,
    _jellyfin_ready,
    _public_row,
)

_NOTE_EMPTY = "nothing part-watched"
_NOTE_READ_ONLY = (
    "read-only list; does not start or continue playback "
    "(use the TV play path for that)"
)
_NOTE_TRUNCATED = (
    "results may be truncated at the per-source limit; "
    "do not claim the household has nothing else in progress"
)
_DEFAULT_LIMIT = 25


def _last_played_sort_key(raw: dict[str, Any]) -> tuple[int, str]:
    """Sort key: (1, iso) when LastPlayedDate present, else (0, '') so undated last."""
    ud = raw.get("UserData")
    if isinstance(ud, dict):
        raw_date = ud.get("LastPlayedDate")
        if isinstance(raw_date, str) and raw_date.strip():
            return (1, raw_date.strip())
    return (0, "")


def _episode_label(raw: dict[str, Any]) -> str | None:
    season = raw.get("ParentIndexNumber")
    episode = raw.get("IndexNumber")
    try:
        s = int(season) if season is not None else None
        e = int(episode) if episode is not None else None
    except (TypeError, ValueError):
        return None
    if s is None or e is None:
        return None
    return f"S{s}E{e}"


def _movie_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    row = _public_row(raw)
    if row is None or row.get("kind") != "movie":
        return None
    ud = raw.get("UserData")
    ticks = 0
    if isinstance(ud, dict):
        try:
            ticks = int(ud.get("PlaybackPositionTicks") or 0)
        except (TypeError, ValueError):
            ticks = 0
    if ticks > 0:
        row["resume_position_ticks"] = ticks
    return row


def _series_row_from_next_up(raw: dict[str, Any]) -> dict[str, Any] | None:
    if str(raw.get("Type") or "") != "Episode":
        return None
    series_id = raw.get("SeriesId")
    series_name = raw.get("SeriesName")
    ep_id = raw.get("Id")
    ep_title = raw.get("Name")
    if not series_id or not series_name or not ep_id:
        return None
    label = _episode_label(raw)
    if label is None:
        return None
    # Episode ProductionYear is not series year — omit rather than mislabel.
    next_ep: dict[str, Any] = {
        "id": str(ep_id),
        "label": label,
        "title": str(ep_title) if ep_title else "",
    }
    return {
        "kind": "series",
        "id": str(series_id),
        "title": str(series_name),
        "year": None,
        "next_episode": next_ep,
    }


def list_continue_watching(
    settings: Settings,
    *,
    client: JellyfinClient | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> str:
    """Live continue-watching list; compact JSON or an ``error:`` string."""
    owns_client = client is None
    jf = client if client is not None else _client_from_settings(settings)
    if jf is None:
        return "error: jellyfin not configured (url, api_key, user_id required)"

    try:
        movies_raw = jf.list_resume_movies(limit=limit)
        next_up_raw = jf.list_next_up(limit=limit)
    except JellyfinError as exc:
        return f"error: {exc}"
    finally:
        if owns_client and jf is not None:
            jf.close()

    truncated = len(movies_raw) >= limit or len(next_up_raw) >= limit

    tagged: list[tuple[tuple[int, str], dict[str, Any]]] = []
    for raw in movies_raw:
        row = _movie_row(raw)
        if row is not None:
            tagged.append((_last_played_sort_key(raw), row))
    for raw in next_up_raw:
        row = _series_row_from_next_up(raw)
        if row is not None:
            tagged.append((_last_played_sort_key(raw), row))

    # Most recent first (ISO strings compare lexicographically for Jellyfin dates).
    tagged.sort(key=lambda t: t[0], reverse=True)
    results = [row for _, row in tagged]
    empty = len(results) == 0
    notes = [_NOTE_READ_ONLY]
    if empty:
        notes.append(_NOTE_EMPTY)
    if truncated:
        notes.append(_NOTE_TRUNCATED)

    payload = {
        "count": len(results),
        "results": results,
        "notes": notes,
        "empty": empty,
        "truncated": truncated,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def continue_watching_tools(settings: Settings) -> dict[str, Tool]:
    def _execute() -> str:
        return list_continue_watching(settings)

    tool = Tool(
        name="list_continue_watching",
        description=(
            "Live Jellyfin continue-watching list: part-watched films (resume) "
            "plus each series' next unplayed episode (NextUp). REQUIRED for: "
            "'what should we finish?', 'what were we watching?', 'continue "
            "watching', 'wat zijn we aan het kijken?', 'verder kijken', "
            "'aan het kijken', or similar. Returns one labelled list (kind "
            "movie|series). Series rows include next_episode.label (S8E11) "
            "and title. Empty count means nothing part-watched — not a "
            "library dump; the model may then use recommend_movies. "
            "Jellyfin down → error, never empty success. READ-ONLY: never "
            "starts or continues playback (that is the TV play path). "
            "Never invent a title not in results."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        execute=_execute,
    )
    return {tool.name: tool}


# Re-export readiness for tests that assert configuration gating.
jellyfin_ready_for_continue = _jellyfin_ready
