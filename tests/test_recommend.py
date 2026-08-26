"""recommend_movies tool — seed resolution, filters, empty catalogue."""

from __future__ import annotations

import json
from pathlib import Path

from brain.config import Settings
from brain.db import Database, Movie
from brain.tools import build_registry, dispatch
from brain.tools.recommend import OVERVIEW_MAX, recommend_movies, resolve_seed


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        runtime={"data_dir": data_dir},
        jellyfin={"sync_interval_hours": 24},
    )


def _seed_catalogue(db: Database) -> None:
    db.seed_catalogue_for_tests(
        [
            Movie(
                jellyfin_id="br",
                name="Blade Runner",
                year=1982,
                genres=["sci-fi", "thriller"],
                community_rating=8.5,
                overview="A" * 400,
                director="Ridley Scott",
                cast=["Harrison Ford"],
                played=True,
            ),
            Movie(
                jellyfin_id="br2049",
                name="Blade Runner 2049",
                year=2017,
                genres=["sci-fi", "thriller"],
                community_rating=8.0,
                played=False,
                playback_position_ticks=1000,
            ),
            Movie(
                jellyfin_id="ghost",
                name="Ghost in the Shell",
                year=1995,
                genres=["sci-fi", "action"],
                community_rating=7.9,
                played=False,
            ),
            Movie(
                jellyfin_id="haunt",
                name="The Haunting",
                year=1999,
                genres=["horror"],
                community_rating=5.0,
                played=False,
            ),
            Movie(
                jellyfin_id="blade1",
                name="Blade",
                year=1998,
                genres=["action", "horror"],
                community_rating=6.5,
                played=False,
            ),
            Movie(
                jellyfin_id="blade2",
                name="Blade II",
                year=2002,
                genres=["action", "horror"],
                community_rating=6.0,
                played=False,
            ),
        ]
    )


def test_empty_catalogue(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    out = recommend_movies(db, settings)
    assert out.startswith("error: catalogue empty")


def test_seed_exact_and_unwatched(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    _seed_catalogue(db)
    out = json.loads(
        recommend_movies(db, settings, seed_title="Blade Runner", unwatched_only=True)
    )
    assert out["count"] >= 1
    ids = {m["id"] for m in out["movies"]}
    assert "br" not in ids
    assert "br2049" in ids or "ghost" in ids
    assert out["filters"]["seed_resolved"] == "Blade Runner"
    # Watched seed still resolved
    assert all(not m["played"] for m in out["movies"])


def test_ambiguous_seed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    _seed_catalogue(db)
    out = json.loads(recommend_movies(db, settings, seed_title="Runner"))
    assert out.get("ambiguous_seed") is True
    assert len(out["candidates"]) >= 2


def test_mood_map_scary(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    _seed_catalogue(db)
    out = json.loads(recommend_movies(db, settings, mood="scary"))
    assert out["count"] >= 1
    for m in out["movies"]:
        assert "horror" in m["genres"] or "thriller" in m["genres"]


def test_overview_truncated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    _seed_catalogue(db)
    out = json.loads(
        recommend_movies(db, settings, seed_title="Blade Runner", unwatched_only=False)
    )
    # include seed's peers; find long overview if present via genre
    for m in out["movies"]:
        if m["overview"]:
            assert len(m["overview"]) <= OVERVIEW_MAX


def test_seed_overlap_excludes_zero_shared_genres(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    _seed_catalogue(db)
    out = json.loads(
        recommend_movies(db, settings, seed_title="Blade Runner", unwatched_only=True)
    )
    ids = {m["id"] for m in out["movies"]}
    assert "haunt" not in ids  # horror only — no overlap with sci-fi/thriller
    assert "blade1" not in ids
    assert out["filters"]["seed_overlap"] == "genres"
    assert all(
        "sci-fi" in m["genres"] or "thriller" in m["genres"] for m in out["movies"]
    )


def test_seed_overlap_year_when_no_genres(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    db.seed_catalogue_for_tests(
        [
            Movie(jellyfin_id="s", name="Seed Year", year=2000, genres=(), played=False),
            Movie(jellyfin_id="near", name="Near Year", year=2005, genres=("drama",), played=False),
            Movie(jellyfin_id="far", name="Far Year", year=1985, genres=("drama",), played=False),
        ]
    )
    out = json.loads(recommend_movies(db, settings, seed_title="Seed Year"))
    ids = {m["id"] for m in out["movies"]}
    assert "near" in ids
    assert "far" not in ids
    assert out["filters"]["seed_overlap"] == "year"


def test_resolve_seed_helpers() -> None:
    movies = [
        Movie(jellyfin_id="1", name="Alpha", year=2000),
        Movie(jellyfin_id="2", name="Alpha Centauri", year=2001),
    ]
    seed, amb, missing = resolve_seed(movies, "Alpha")
    assert seed is not None and seed.name == "Alpha" and amb is None
    seed, amb, missing = resolve_seed(movies, "Alph")
    assert seed is None and amb is not None and not missing
    seed, amb, missing = resolve_seed(movies, "nope")
    assert missing and seed is None


def test_tool_description_mentions_seed_fallback(tmp_path: Path) -> None:
    from brain.tools.recommend import recommend_tools

    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    tools = recommend_tools(settings, db)
    desc = tools["recommend_movies"].description.lower()
    assert "falls back" in desc
    assert "must exist" not in desc


def test_registry_dispatch(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    _seed_catalogue(db)
    reg = build_registry(settings, db=db)
    assert "recommend_movies" in reg
    raw = dispatch("recommend_movies", {"genre": "horror"}, tools=reg)
    data = json.loads(raw)
    assert data["count"] >= 1
