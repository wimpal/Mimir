"""find_media — live Jellyfin lookup (do we have X / under N minutes)."""

from __future__ import annotations

import json
from typing import Any

from brain.config import Settings
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.tools import Tool

# Jellyfin ticks: 10_000_000 per second.
_TICKS_PER_MINUTE = 600_000_000

_NOTES_SERIES_RUNTIME = (
    "series total_runtime_minutes is Jellyfin's RunTimeTicks when present; "
    "often missing or episode-length — not comparable to max_minutes"
)
_NOTES_SERIES_PLAYED_OMITTED = (
    "series watched state omitted: Played=false is ambiguous (never vs partial)"
)
_NOTES_TRUNCATED = (
    "results truncated; do not claim a title is absent from the library"
)


def _jellyfin_ready(settings: Settings) -> bool:
    """url + api_key + user_id only — not jellyfin_sync_configured (needs library_ids)."""
    jf = settings.jellyfin
    return bool(jf.url.strip() and jf.api_key and jf.user_id.strip())


def _client_from_settings(settings: Settings) -> JellyfinClient | None:
    if not _jellyfin_ready(settings):
        return None
    jf = settings.jellyfin
    assert jf.api_key is not None
    return JellyfinClient(
        jf.url,
        jf.api_key,
        user_id=jf.user_id,
        page_size=jf.page_size,
        request_timeout_s=min(30.0, float(settings.timeouts.tool_s)),
    )


def _ticks_to_minutes(ticks: Any) -> int | None:
    if ticks is None:
        return None
    try:
        n = int(ticks)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return max(1, round(n / _TICKS_PER_MINUTE))


def _item_kind(raw: dict[str, Any]) -> str | None:
    t = str(raw.get("Type") or "").strip()
    if t == "Movie":
        return "movie"
    if t == "Series":
        return "series"
    return None


def _public_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    kind = _item_kind(raw)
    item_id = raw.get("Id")
    name = raw.get("Name")
    if kind is None or not item_id or not name:
        return None

    year = raw.get("ProductionYear")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None

    row: dict[str, Any] = {
        "kind": kind,
        "id": str(item_id),
        "title": str(name),
        "year": year,
    }
    runtime = _ticks_to_minutes(raw.get("RunTimeTicks"))
    if kind == "movie":
        if runtime is not None:
            row["runtime_minutes"] = runtime
        ud = raw.get("UserData")
        if isinstance(ud, dict) and "Played" in ud:
            row["played"] = bool(ud.get("Played"))
        else:
            row["played"] = False
    else:
        # Series: do not report played (false is ambiguous). Runtime only when present.
        if runtime is not None:
            row["total_runtime_minutes"] = runtime
    return row


def find_media_in_library(
    settings: Settings,
    *,
    title: str | None = None,
    max_minutes: int | None = None,
    client: JellyfinClient | None = None,
) -> str:
    """Live lookup; returns compact JSON or an ``error:`` string."""
    needle = (title or "").strip() or None
    ceiling: int | None = None
    if max_minutes is not None:
        try:
            ceiling = int(max_minutes)
        except (TypeError, ValueError):
            return "error: max_minutes must be a positive integer"
        if ceiling < 1:
            return "error: max_minutes must be a positive integer"

    if needle is None and ceiling is None:
        return "error: pass title and/or max_minutes (both omitted is not a valid call)"

    owns_client = client is None
    jf = client if client is not None else _client_from_settings(settings)
    if jf is None:
        return "error: jellyfin not configured (url, api_key, user_id required)"

    # max_minutes-only → movies only (series must never be ranked under the ceiling).
    include_series = needle is not None
    try:
        raws, truncated = jf.find_media(title=needle, include_series=include_series)
    except JellyfinError as exc:
        return f"error: {exc}"
    finally:
        if owns_client and jf is not None:
            jf.close()

    results: list[dict[str, Any]] = []
    for raw in raws:
        row = _public_row(raw)
        if row is None:
            continue
        if ceiling is not None and row["kind"] == "movie":
            runtime = row.get("runtime_minutes")
            if runtime is None or runtime > ceiling:
                # Title lookup must not vanish into count:0 ("not in library").
                # Ceiling-only queries drop over-limit films; title+ceiling keeps
                # them and marks the miss.
                if needle is None:
                    continue
                row["within_max_minutes"] = False
            else:
                row["within_max_minutes"] = True
        results.append(row)

    notes: list[str] = [_NOTES_SERIES_RUNTIME, _NOTES_SERIES_PLAYED_OMITTED]
    if truncated:
        notes.append(_NOTES_TRUNCATED)
    if ceiling is not None and needle is not None:
        notes.append(
            "within_max_minutes is false when a title match exceeds max_minutes; "
            "count:0 still means no title match, not 'too long'"
        )

    payload = {
        "query": {"title": needle, "max_minutes": ceiling},
        "count": len(results),
        "results": results,
        "notes": notes,
        "truncated": truncated,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def media_lookup_tools(settings: Settings) -> dict[str, Tool]:
    def _execute(
        *,
        title: str | None = None,
        max_minutes: int | None = None,
    ) -> str:
        return find_media_in_library(
            settings,
            title=title,
            max_minutes=max_minutes,
        )

    tool = Tool(
        name="find_media",
        description=(
            "Live Jellyfin lookup with runtime. REQUIRED for: 'do we have X?', "
            "'hebben we X?', 'is X in the library?', 'a movie under N minutes', "
            "'something under N minutes', 'iets onder de N minuten', or any "
            "length/duration/runtime ask. Call with max_minutes=N for short-"
            "film lists (returns runtime_minutes on each film) and/or title=X "
            "for membership. At least one of title/max_minutes required. "
            "Never use recommend_movies for these asks — it has no runtime. "
            "Ground replies in results only; never invent a title not returned. "
            "Empty count = not in this library (not 'unreleased'). Title + "
            "max_minutes: over-length films still appear with "
            "within_max_minutes false (not absence). truncated true → do not "
            "claim absence. Jellyfin down → error, not empty success. Series "
            "total_runtime_minutes is not comparable to max_minutes. Does not "
            "play, recommend, or write favourites."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title to look up (film or series)",
                },
                "max_minutes": {
                    "type": "integer",
                    "description": (
                        "Runtime ceiling in minutes; filters films only. "
                        "Series are never ranked under this ceiling."
                    ),
                },
            },
            "additionalProperties": False,
        },
        execute=_execute,
    )
    return {tool.name: tool}
