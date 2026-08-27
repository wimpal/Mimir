"""list_recently_watched tool — window, sort, fail-clear."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from brain.config import Settings
from brain.db import Database, Movie
from brain.tools import build_registry, dispatch
from brain.tools.recently_watched import MSG_PLAY_DATES_MISSING, list_recently_watched


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        runtime={"data_dir": data_dir},
        jellyfin={"sync_interval_hours": 24, "recent_watched_days": 14},
    )


def _iso_days_ago(days: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_list_recently_watched_window_and_sort(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    db.seed_catalogue_for_tests(
        [
            Movie(
                jellyfin_id="new",
                name="Newest",
                genres=["sci-fi"],
                played=True,
                last_played_at=_iso_days_ago(1),
            ),
            Movie(
                jellyfin_id="mid",
                name="Mid",
                genres=["drama"],
                played=False,
                playback_position_ticks=100,
                last_played_at=_iso_days_ago(5),
            ),
            Movie(
                jellyfin_id="old",
                name="Too Old",
                genres=["comedy"],
                played=True,
                last_played_at=_iso_days_ago(40),
            ),
        ]
    )
    out = json.loads(list_recently_watched(db, settings, days=14))
    names = [m["name"] for m in out["movies"]]
    assert names == ["Newest", "Mid"]
    assert out["days"] == 14
    assert out["count"] == 2


def test_list_play_dates_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    db.seed_catalogue_for_tests(
        [Movie(jellyfin_id="a", name="Watched", played=True)]
    )
    out = list_recently_watched(db, settings)
    assert out == MSG_PLAY_DATES_MISSING


def test_dispatch_list_recently_watched(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    db.seed_catalogue_for_tests(
        [
            Movie(
                jellyfin_id="a",
                name="Recent",
                genres=["sci-fi"],
                played=True,
                last_played_at=_iso_days_ago(2),
            )
        ]
    )
    tools = build_registry(settings, db=db)
    raw = dispatch("list_recently_watched", {}, tools=tools)
    data = json.loads(raw)
    assert data["count"] == 1
    assert data["movies"][0]["name"] == "Recent"
