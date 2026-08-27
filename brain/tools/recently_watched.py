"""list_recently_watched — Catalogue Recent watches in a time window."""

from __future__ import annotations

import json
from typing import Any

from brain.config import Settings
from brain.db import Database, Movie
from brain.jellyfin_sync import catalogue_status_dict
from brain.tools import Tool

MSG_PLAY_DATES_MISSING = (
    "error: play dates missing from catalogue; run Jellyfin sync to refresh "
    "last_played_at"
)


def _movie_recent_public(m: Movie) -> dict[str, Any]:
    return {
        "id": m.jellyfin_id,
        "name": m.name,
        "year": m.year,
        "last_played_at": m.last_played_at,
        "played": m.played,
        "in_progress": m.playback_position_ticks > 0 and not m.played,
        "genres": list(m.genres),
    }


def list_recently_watched(
    db: Database,
    settings: Settings,
    *,
    days: int | None = None,
) -> str:
    """Return compact JSON of Recent watches, or a clear error string."""
    status = catalogue_status_dict(db, settings)
    if status["active_generation"] is None or status["movie_count"] == 0:
        return "error: catalogue empty; sync required"

    window = (
        int(days)
        if days is not None
        else int(settings.jellyfin.recent_watched_days)
    )
    if window < 1:
        return "error: days must be >= 1"

    if db.has_played_without_dates():
        return MSG_PLAY_DATES_MISSING

    movies = db.list_recently_watched(window)
    payload = {
        "days": window,
        "count": len(movies),
        "movies": [_movie_recent_public(m) for m in movies],
        "catalogue": {
            "stale": bool(status["stale"]),
            "last_success_at": status["last_success_at"],
        },
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def recently_watched_tools(settings: Settings, db: Database) -> dict[str, Tool]:
    def _execute(*, days: int | None = None) -> str:
        return list_recently_watched(db, settings, days=days)

    tool = Tool(
        name="list_recently_watched",
        description=(
            "List movies the user watched or played recently from the Jellyfin "
            "catalogue cache (default window from config, typically ~14 days). "
            "Use when they ask what they watched lately / last week. "
            "Does not call Jellyfin live."
        ),
        parameters={
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": (
                        "Look-back window in days (default: config "
                        "jellyfin.recent_watched_days). Minimum 1."
                    ),
                },
            },
            "additionalProperties": False,
        },
        execute=_execute,
    )
    return {tool.name: tool}
