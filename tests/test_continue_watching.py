"""list_continue_watching — live Jellyfin resume + next-up (T-129)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx

from brain.agent import _READ_ONLY_TOOL_NAMES, _schemas_after_tools
from brain.capability_discovery import _CLUSTER_LABELS, _LOCAL_CLUSTER
from brain.config import Settings
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.tool_gate import should_offer_tools
from brain.tools import build_registry, dispatch
from brain.tools.continue_watching import (
    continue_watching_tools,
    list_continue_watching,
)


def _settings(tmp_path: Path, **jf_overrides: Any) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        runtime={"data_dir": data_dir},
        timeouts={"tool_s": 30.0},
        jellyfin={
            "url": "http://jellyfin.test",
            "api_key": "key",
            "user_id": "user-1",
            "library_ids": ["lib-movies"],
            "page_size": 50,
            **jf_overrides,
        },
    )


def _movie_resume(
    *,
    item_id: str = "m1",
    name: str = "Dune",
    year: int = 2021,
    runtime_min: int = 155,
    position_ticks: int = 50 * 600_000_000,
    last_played: str = "2026-10-05T18:00:00.0000000Z",
) -> dict[str, Any]:
    return {
        "Id": item_id,
        "Name": name,
        "Type": "Movie",
        "ProductionYear": year,
        "RunTimeTicks": runtime_min * 600_000_000,
        "UserData": {
            "Played": False,
            "PlaybackPositionTicks": position_ticks,
            "LastPlayedDate": last_played,
        },
    }


def _next_up_episode(
    *,
    ep_id: str = "ep1",
    ep_name: str = "Sarge & Pea",
    series_id: str = "s1",
    series_name: str = "Modern Family",
    season: int = 8,
    episode: int = 11,
    year: int = 2017,
    last_played: str | None = "2026-10-05T17:00:00.0000000Z",
) -> dict[str, Any]:
    ud: dict[str, Any] = {
        "Played": False,
        "PlaybackPositionTicks": 0,
    }
    if last_played is not None:
        ud["LastPlayedDate"] = last_played
    return {
        "Id": ep_id,
        "Name": ep_name,
        "Type": "Episode",
        "SeriesId": series_id,
        "SeriesName": series_name,
        "ParentIndexNumber": season,
        "IndexNumber": episode,
        "ProductionYear": year,
        "UserData": ud,
    }


def test_film_and_series_listed(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.list_resume_movies.return_value = [_movie_resume()]
    jf.list_next_up.return_value = [_next_up_episode()]
    jf.close = MagicMock()

    raw = list_continue_watching(_settings(tmp_path), client=jf)
    assert not raw.startswith("error:")
    payload = json.loads(raw)
    assert payload["count"] == 2
    assert payload["empty"] is False
    kinds = {r["kind"] for r in payload["results"]}
    assert kinds == {"movie", "series"}
    movie = next(r for r in payload["results"] if r["kind"] == "movie")
    assert movie["title"] == "Dune"
    assert movie["resume_position_ticks"] > 0
    assert movie["runtime_minutes"] == 155
    series = next(r for r in payload["results"] if r["kind"] == "series")
    assert series["title"] == "Modern Family"
    assert series["id"] == "s1"
    assert series["next_episode"]["label"] == "S8E11"
    assert series["next_episode"]["title"] == "Sarge & Pea"
    # Movie last-played later → first
    assert payload["results"][0]["kind"] == "movie"
    jf.list_resume_movies.assert_called_once()
    jf.list_next_up.assert_called_once()


def test_fully_watched_series_absent(tmp_path: Path) -> None:
    """Global NextUp empty → series does not appear (no S1E1 invent)."""
    jf = MagicMock()
    jf.list_resume_movies.return_value = []
    jf.list_next_up.return_value = []
    raw = list_continue_watching(_settings(tmp_path), client=jf)
    payload = json.loads(raw)
    assert payload["count"] == 0
    assert payload["empty"] is True
    assert "nothing part-watched" in " ".join(payload["notes"])


def test_empty_not_error(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.list_resume_movies.return_value = []
    jf.list_next_up.return_value = []
    raw = list_continue_watching(_settings(tmp_path), client=jf)
    assert not raw.startswith("error:")
    payload = json.loads(raw)
    assert payload["results"] == []


def test_jellyfin_down(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.list_resume_movies.side_effect = JellyfinError(
        "jellyfin unavailable (timeout)"
    )
    out = list_continue_watching(_settings(tmp_path), client=jf)
    assert out.startswith("error:")
    assert "unavailable" in out


def test_not_configured(tmp_path: Path) -> None:
    settings = _settings(tmp_path, url="", api_key=None, user_id="")
    out = list_continue_watching(settings)
    assert out.startswith("error:")
    assert "not configured" in out


def test_no_play_apis_called(tmp_path: Path) -> None:
    jf = MagicMock(spec=JellyfinClient)
    jf.list_resume_movies.return_value = [_movie_resume()]
    jf.list_next_up.return_value = []
    list_continue_watching(_settings(tmp_path), client=jf)
    jf.list_resume_movies.assert_called_once()
    jf.list_next_up.assert_called_once()
    # Only the two list reads — no session / play helpers.
    names = {c[0] for c in jf.method_calls}
    assert names <= {"list_resume_movies", "list_next_up", "close"}


def test_resume_filters_non_movies_and_zero_ticks(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/Items/Resume"):
            assert "ParentId" not in dict(request.url.params)
            return httpx.Response(
                200,
                json={
                    "Items": [
                        _movie_resume(),
                        {
                            "Id": "ep-x",
                            "Name": "Leak",
                            "Type": "Episode",
                            "UserData": {"PlaybackPositionTicks": 99},
                        },
                        _movie_resume(
                            item_id="m0",
                            name="Done",
                            position_ticks=0,
                        ),
                    ]
                },
            )
        if path.endswith("/Shows/NextUp") or path == "/Shows/NextUp":
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://jellyfin.test/",
        headers={"X-Emby-Token": "key"},
    )
    jf = JellyfinClient(
        "http://jellyfin.test",
        "key",
        user_id="user-1",
        client=http,
    )
    movies = jf.list_resume_movies()
    assert len(movies) == 1
    assert movies[0]["Id"] == "m1"
    raw = list_continue_watching(_settings(tmp_path), client=jf)
    payload = json.loads(raw)
    assert payload["count"] == 1
    assert payload["results"][0]["kind"] == "movie"


def test_next_up_http_shape(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "Resume" in request.url.path:
            return httpx.Response(200, json={"Items": []})
        assert request.url.path.rstrip("/").endswith("Shows/NextUp")
        params = dict(request.url.params)
        assert params.get("UserId") == "user-1"
        assert "SeriesId" not in params
        assert "ParentId" not in params
        return httpx.Response(
            200,
            json={"Items": [_next_up_episode()]},
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://jellyfin.test/",
        headers={"X-Emby-Token": "key"},
    )
    jf = JellyfinClient(
        "http://jellyfin.test",
        "key",
        user_id="user-1",
        client=http,
    )
    raw = list_continue_watching(_settings(tmp_path), client=jf)
    payload = json.loads(raw)
    assert payload["count"] == 1
    assert payload["results"][0]["next_episode"]["label"] == "S8E11"


def test_registry_and_read_only(tmp_path: Path) -> None:
    reg = build_registry(_settings(tmp_path), db=None)
    assert "list_continue_watching" in reg
    assert "list_continue_watching" in _READ_ONLY_TOOL_NAMES
    assert _LOCAL_CLUSTER["list_continue_watching"] == "local.media"
    en, nl = _CLUSTER_LABELS["local.media"]
    assert "continue watching" in en
    assert "verder kijken" in nl
    schemas = [
        {"type": "function", "function": {"name": "list_continue_watching"}},
    ]
    kept = _schemas_after_tools(
        schemas,
        tools_used_this_turn=["list_continue_watching"],
        has_tool_results=True,
    )
    assert kept == schemas
    empty = build_registry(
        _settings(tmp_path, url="", api_key=None, user_id=""),
        db=None,
    )
    out = dispatch("list_continue_watching", {}, tools=empty)
    assert out.startswith("error:")
    tools = continue_watching_tools(_settings(tmp_path))
    assert "list_continue_watching" in tools


def test_tool_gate_phrases() -> None:
    assert should_offer_tools("what should we finish?")
    assert should_offer_tools("what were we watching?")
    assert should_offer_tools("continue watching")
    assert should_offer_tools("wat zijn we aan het kijken?")
    assert should_offer_tools("verder kijken")


def test_description_forbids_playback(tmp_path: Path) -> None:
    tools = continue_watching_tools(_settings(tmp_path))
    desc = tools["list_continue_watching"].description.lower()
    assert "read-only" in desc or "never" in desc
    assert "playback" in desc or "play" in desc
