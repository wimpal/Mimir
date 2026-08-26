"""recommend_movies — filter Catalogue subset for the LLM (no live Jellyfin)."""

from __future__ import annotations

import json
from typing import Any

from brain.config import Settings
from brain.db import Database, Movie
from brain.jellyfin_sync import catalogue_status_dict
from brain.tools import Tool

SUBSET_CAP = 20
OVERVIEW_MAX = 240

MOOD_GENRES: dict[str, list[str]] = {
    "cozy": ["drama", "comedy", "romance", "family"],
    "scary": ["horror", "thriller"],
    "funny": ["comedy"],
    "thoughtful": ["drama", "documentary", "mystery"],
    "action": ["action", "adventure", "thriller"],
}


def _truncate(text: str | None, max_len: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _genres_folded(genres: tuple[str, ...] | list[str]) -> set[str]:
    return {g.casefold() for g in genres if g}


def _movie_public(m: Movie) -> dict[str, Any]:
    return {
        "id": m.jellyfin_id,
        "name": m.name,
        "year": m.year,
        "genres": list(m.genres),
        "community_rating": m.community_rating,
        "official_rating": m.official_rating,
        "director": m.director,
        "cast": list(m.cast[:5]),
        "overview": _truncate(m.overview, OVERVIEW_MAX),
        "played": m.played,
        "in_progress": m.playback_position_ticks > 0 and not m.played,
    }


def resolve_seed(
    movies: list[Movie],
    seed_title: str,
) -> tuple[Movie | None, list[Movie] | None, bool]:
    """Return (seed, ambiguous_candidates, missing).

    Exact case-insensitive match first; else unique contains; else ambiguous list.
    """
    needle = seed_title.strip()
    if not needle:
        return None, None, True

    lower = needle.casefold()
    exact = [m for m in movies if m.name.casefold() == lower]
    if len(exact) == 1:
        return exact[0], None, False
    if len(exact) > 1:
        return None, exact, False

    contains = [m for m in movies if lower in m.name.casefold()]
    if len(contains) == 1:
        return contains[0], None, False
    if len(contains) > 1:
        return None, contains, False
    return None, None, True


def _genre_match(movie: Movie, wanted: set[str]) -> bool:
    if not wanted:
        return True
    return bool(_genres_folded(movie.genres) & wanted)


def _seed_overlap_ok(m: Movie, seed: Movie) -> bool:
    seed_genres = _genres_folded(seed.genres)
    if seed_genres:
        return bool(_genres_folded(m.genres) & seed_genres)
    if seed.year is not None:
        return m.year is not None and abs(m.year - seed.year) <= 10
    return True


def _rank_key(m: Movie, *, seed: Movie | None) -> tuple:
    shared = 0
    year_close = 0
    if seed is not None:
        shared = len(_genres_folded(m.genres) & _genres_folded(seed.genres))
        if m.year is not None and seed.year is not None:
            year_close = 1 if abs(m.year - seed.year) <= 10 else 0
    rating = m.community_rating if m.community_rating is not None else -1.0
    return (-shared, -year_close, -rating, m.name.casefold())


def recommend_movies(
    db: Database,
    settings: Settings,
    *,
    seed_title: str | None = None,
    genre: str | None = None,
    mood: str | None = None,
    unwatched_only: bool = True,
    min_rating: float | None = None,
) -> str:
    """Return compact JSON Catalogue subset (or structured error / ambiguity)."""
    status = catalogue_status_dict(db, settings)
    if status["active_generation"] is None or status["movie_count"] == 0:
        return "error: catalogue empty; sync required"

    all_movies = db.list_active_movies()
    sync_status_stale = bool(status["stale"])
    last_success_at = status["last_success_at"]

    filters_applied: dict[str, Any] = {
        "unwatched_only": unwatched_only,
    }

    seed: Movie | None = None
    if seed_title and seed_title.strip():
        seed, ambiguous, missing = resolve_seed(all_movies, seed_title)
        if ambiguous is not None:
            return json.dumps(
                {
                    "ambiguous_seed": True,
                    "candidates": [
                        {"id": m.jellyfin_id, "name": m.name, "year": m.year}
                        for m in ambiguous[:10]
                    ],
                    "hint": "ask which title was meant",
                },
                separators=(",", ":"),
            )
        filters_applied["seed_title"] = seed_title.strip()
        if missing:
            filters_applied["seed_missing"] = True
        elif seed is not None:
            filters_applied["seed_resolved"] = seed.name
            if _genres_folded(seed.genres):
                filters_applied["seed_overlap"] = "genres"
            elif seed.year is not None:
                filters_applied["seed_overlap"] = "year"
            else:
                filters_applied["seed_overlap"] = "skipped"

    genre_tokens: set[str] = set()
    if genre and genre.strip():
        g = genre.strip().casefold()
        genre_tokens.add(g)
        filters_applied["genre"] = g

    if mood and mood.strip():
        key = mood.strip().casefold()
        mapped = MOOD_GENRES.get(key)
        filters_applied["mood"] = key
        if mapped:
            genre_tokens.update(mapped)
            filters_applied["mood_genres"] = mapped
        else:
            filters_applied["mood_ignored"] = True

    if min_rating is not None:
        filters_applied["min_rating"] = min_rating

    candidates: list[Movie] = []
    for m in all_movies:
        if seed is not None and m.jellyfin_id == seed.jellyfin_id:
            continue
        if unwatched_only and m.played:
            continue
        if genre_tokens and not _genre_match(m, genre_tokens):
            continue
        if min_rating is not None:
            if m.community_rating is None or m.community_rating < min_rating:
                continue
        if seed is not None and not _seed_overlap_ok(m, seed):
            continue
        candidates.append(m)

    if seed is not None:
        candidates.sort(key=lambda m: _rank_key(m, seed=seed))
    else:
        candidates.sort(
            key=lambda m: (
                -(m.community_rating if m.community_rating is not None else -1.0),
                m.name.casefold(),
            )
        )

    subset = candidates[:SUBSET_CAP]
    if not subset:
        return json.dumps(
            {
                "no_matches": True,
                "filters": filters_applied,
                "hint": "relax genre, mood, unwatched_only, min_rating, or seed overlap",
                "stale": sync_status_stale,
                "last_success_at": last_success_at,
            },
            separators=(",", ":"),
        )

    payload = {
        "count": len(subset),
        "stale": sync_status_stale,
        "last_success_at": last_success_at,
        "filters": filters_applied,
        "movies": [_movie_public(m) for m in subset],
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def recommend_tools(settings: Settings, db: Database) -> dict[str, Tool]:
    def _execute(
        *,
        seed_title: str | None = None,
        genre: str | None = None,
        mood: str | None = None,
        unwatched_only: bool = True,
        min_rating: float | None = None,
    ) -> str:
        return recommend_movies(
            db,
            settings,
            seed_title=seed_title,
            genre=genre,
            mood=mood,
            unwatched_only=unwatched_only,
            min_rating=min_rating,
        )

    tool = Tool(
        name="recommend_movies",
        description=(
            "Recommend movies from the user's Jellyfin Catalogue cache. "
            "Use for movie suggestions, 'something like X', unwatched picks, "
            "or genre/mood requests. Returns a small subset — pick and explain "
            "from the tool output only; never invent titles. "
            "Optional seed_title for 'like X': if missing from the Catalogue, "
            "the tool reports seed_missing and falls back to genre/mood filters."
        ),
        parameters={
            "type": "object",
            "properties": {
                "seed_title": {
                    "type": "string",
                    "description": (
                        "Reference title for similar picks; missing titles fall "
                        "back to genre/mood"
                    ),
                },
                "genre": {
                    "type": "string",
                    "description": "Genre filter (exact match, case-insensitive)",
                },
                "mood": {
                    "type": "string",
                    "description": (
                        "Mood hint mapped to genres: cozy, scary, funny, "
                        "thoughtful, action"
                    ),
                },
                "unwatched_only": {
                    "type": "boolean",
                    "description": "If true (default), exclude fully watched movies",
                    "default": True,
                },
                "min_rating": {
                    "type": "number",
                    "description": "Minimum Jellyfin community rating (optional)",
                },
            },
            "additionalProperties": False,
        },
        execute=_execute,
    )
    return {tool.name: tool}
