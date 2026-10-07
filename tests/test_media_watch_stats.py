"""media_watch_stats — films we last watched in a window (T-131)."""

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
from brain.tools.media_watch_stats import media_watch_stats, media_watch_stats_tools


def _settings(tmp_path: Path, **jf_overrides: Any) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0, "timezone": "Europe/Amsterdam"},
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


def _played(
    *,
    item_id: str = "m1",
    name: str = "Dune",
    year: int = 2021,
    last_played: str = "2026-10-05T18:00:00.0000000Z",
    kind: str = "Movie",
) -> dict[str, Any]:
    return {
        "Id": item_id,
        "Name": name,
        "Type": kind,
        "ProductionYear": year,
        "UserData": {"Played": True, "LastPlayedDate": last_played},
    }


def _mock_client(
    raws: list[dict[str, Any]], *, truncated: bool = False
) -> MagicMock:
    jf = MagicMock()
    jf.list_played_movies.return_value = (raws, truncated)
    jf.close = MagicMock()
    return jf


def test_film_in_window_counted(tmp_path: Path) -> None:
    jf = _mock_client([_played()])
    raw = media_watch_stats(_settings(tmp_path), client=jf)
    assert not raw.startswith("error:")
    payload = json.loads(raw)
    assert payload["counts"]["films_last_played_in_window"] == 1
    assert payload["counts"]["meaning"] == "films whose last play fell in the window"
    assert payload["empty"] is False
    assert payload["watch_history"] is False
    assert payload["series_counted"] is False
    assert payload["window"]["period"] == "this_year"
    assert payload["recent_titles"][0]["title"] == "Dune"
    jf.list_played_movies.assert_called_once()


def test_film_out_of_window_excluded(tmp_path: Path) -> None:
    jf = _mock_client([_played(last_played="2025-03-01T18:00:00.0000000Z")])
    payload = json.loads(media_watch_stats(_settings(tmp_path), client=jf))
    assert payload["counts"]["films_last_played_in_window"] == 0
    assert payload["empty"] is True
    assert not payload["truncated"]


def test_rewatched_film_counts_once(tmp_path: Path) -> None:
    """One Jellyfin row per film; a rewatched film is a single Played item."""
    jf = _mock_client([_played(last_played="2026-10-05T18:00:00.0000000Z")])
    payload = json.loads(media_watch_stats(_settings(tmp_path), client=jf))
    assert payload["counts"]["films_last_played_in_window"] == 1


def test_duplicate_id_deduped(tmp_path: Path) -> None:
    """Defensive: the same film id appearing twice is still counted once."""
    jf = _mock_client([_played(), _played()])
    payload = json.loads(media_watch_stats(_settings(tmp_path), client=jf))
    assert payload["counts"]["films_last_played_in_window"] == 1


def test_series_and_episodes_excluded(tmp_path: Path) -> None:
    jf = _mock_client(
        [
            _played(),
            _played(item_id="s1", name="The Bear", kind="Series", year=2022),
            _played(item_id="ep1", name="S8E11", kind="Episode", year=2023),
        ]
    )
    payload = json.loads(media_watch_stats(_settings(tmp_path), client=jf))
    assert payload["counts"]["films_last_played_in_window"] == 1
    assert payload["series_counted"] is False
    titles = {r["title"] for r in payload["recent_titles"]}
    assert titles == {"Dune"}


def test_timezone_boundary_counts_in_local_year(tmp_path: Path) -> None:
    """2025-12-31T23:30Z is 2026-01-01 00:30 in Europe/Amsterdam -> this year."""
    jf = _mock_client([_played(last_played="2025-12-31T23:30:00.0000000Z")])
    payload = json.loads(media_watch_stats(_settings(tmp_path), client=jf))
    assert payload["counts"]["films_last_played_in_window"] == 1
    assert payload["window"]["timezone"] == "Europe/Amsterdam"


def test_empty_window_not_error(tmp_path: Path) -> None:
    jf = _mock_client([])
    raw = media_watch_stats(_settings(tmp_path), client=jf)
    assert not raw.startswith("error:")
    payload = json.loads(raw)
    assert payload["counts"]["films_last_played_in_window"] == 0
    assert payload["empty"] is True
    assert any("no films" in n for n in payload["notes"])


def test_jellyfin_down(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.list_played_movies.side_effect = JellyfinError("jellyfin unavailable (timeout)")
    out = media_watch_stats(_settings(tmp_path), client=jf)
    assert out.startswith("error:")
    assert "unavailable" in out


def test_not_configured(tmp_path: Path) -> None:
    settings = _settings(tmp_path, url="", api_key=None, user_id="")
    out = media_watch_stats(settings)
    assert out.startswith("error:")
    assert "not configured" in out


def test_truncation_flag(tmp_path: Path) -> None:
    jf = _mock_client([_played()], truncated=True)
    payload = json.loads(media_watch_stats(_settings(tmp_path), client=jf))
    assert payload["truncated"] is True
    assert any("truncated" in n for n in payload["notes"])


def test_period_last_year(tmp_path: Path) -> None:
    jf = _mock_client([_played(last_played="2025-06-01T18:00:00.0000000Z")])
    payload = json.loads(
        media_watch_stats(_settings(tmp_path), period="last_year", client=jf)
    )
    assert payload["window"]["period"] == "last_year"
    assert payload["counts"]["films_last_played_in_window"] == 1


def test_period_last_month_excludes_this_month(tmp_path: Path) -> None:
    jf = _mock_client([_played(last_played="2026-10-05T18:00:00.0000000Z")])
    payload = json.loads(
        media_watch_stats(_settings(tmp_path), period="last_month", client=jf)
    )
    assert payload["window"]["period"] == "last_month"
    assert payload["counts"]["films_last_played_in_window"] == 0


def test_custom_window(tmp_path: Path) -> None:
    jf = _mock_client([_played(last_played="2026-06-15T18:00:00.0000000Z")])
    payload = json.loads(
        media_watch_stats(
            _settings(tmp_path),
            period="custom",
            since="2026-06-01",
            until="2026-06-30",
            client=jf,
        )
    )
    assert payload["window"]["since"] == "2026-06-01"
    assert payload["window"]["until"].startswith("2026-07-01")
    assert payload["counts"]["films_last_played_in_window"] == 1


def test_custom_requires_both_bounds(tmp_path: Path) -> None:
    out = media_watch_stats(
        _settings(tmp_path), period="custom", since="2026-06-01"
    )
    assert out.startswith("error:")
    assert "since and until" in out


def test_since_after_until_rejected(tmp_path: Path) -> None:
    out = media_watch_stats(
        _settings(tmp_path),
        period="custom",
        since="2026-06-30",
        until="2026-06-01",
    )
    assert out.startswith("error:")
    assert "since must not be after until" in out


def test_invalid_date_format_rejected(tmp_path: Path) -> None:
    out = media_watch_stats(
        _settings(tmp_path), period="custom", since="06/01/2026", until="2026-06-30"
    )
    assert out.startswith("error:")
    assert "YYYY-MM-DD" in out


def test_invalid_period_rejected(tmp_path: Path) -> None:
    out = media_watch_stats(_settings(tmp_path), period="forever")
    assert out.startswith("error:")
    assert "period must be one of" in out


def test_no_http_on_validation_error(tmp_path: Path) -> None:
    jf = MagicMock()
    media_watch_stats(_settings(tmp_path), period="forever", client=jf)
    jf.list_played_movies.assert_not_called()


def test_registry_and_read_only(tmp_path: Path) -> None:
    reg = build_registry(_settings(tmp_path), db=None)
    assert "media_watch_stats" in reg
    assert "media_watch_stats" in _READ_ONLY_TOOL_NAMES
    assert _LOCAL_CLUSTER["media_watch_stats"] == "local.media"
    en, nl = _CLUSTER_LABELS["local.media"]
    assert "watch stats" in en
    assert "kijkstatistieken" in nl
    schemas = [
        {"type": "function", "function": {"name": "media_watch_stats"}},
        {"type": "function", "function": {"name": "echo"}},
    ]
    kept = _schemas_after_tools(
        schemas,
        tools_used_this_turn=["media_watch_stats"],
        has_tool_results=True,
    )
    assert kept == schemas
    empty = build_registry(
        _settings(tmp_path, url="", api_key=None, user_id=""),
        db=None,
    )
    out = dispatch("media_watch_stats", {}, tools=empty)
    assert out.startswith("error:")
    tools = media_watch_stats_tools(_settings(tmp_path))
    assert "media_watch_stats" in tools


def test_dispatch_not_configured_errors(tmp_path: Path) -> None:
    reg = build_registry(
        _settings(tmp_path, url="", api_key=None, user_id=""), db=None
    )
    out = dispatch("media_watch_stats", {}, tools=reg)
    assert out.startswith("error:")
    assert "not configured" in out


def test_tool_gate_phrases() -> None:
    assert should_offer_tools("how many films did we watch this year?")
    assert should_offer_tools("hoeveel films hebben we dit jaar gekeken?")
    assert should_offer_tools("films last month")
    assert should_offer_tools("how many movies have we watched?")


def test_description_forbids_hours_and_counts() -> None:
    tools = media_watch_stats_tools(
        Settings(
            location={"latitude": 1.0, "longitude": 2.0, "timezone": "UTC"},
            jellyfin={"url": "", "api_key": None, "user_id": ""},
        )
    )
    desc = tools["media_watch_stats"].description.lower()
    assert "never" in desc
    assert "hours watched" in desc
    assert "times watched" in desc
    assert "series" in desc
    assert "read-only" in desc


def test_http_shape_and_params(tmp_path: Path) -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        assert params.get("IncludeItemTypes") == "Movie"
        assert params.get("Filters") == "IsPlayed"
        assert params.get("SortBy") == "DatePlayed"
        assert "ParentId" not in params
        assert "UserData" in (params.get("Fields") or "")
        return httpx.Response(
            200,
            json={"Items": [_played()], "TotalRecordCount": 1},
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://jellyfin.test/",
        headers={"X-Emby-Token": "key"},
    )
    jf = JellyfinClient("http://jellyfin.test", "key", user_id="user-1", client=http)
    raws, truncated = jf.list_played_movies()
    assert len(raws) == 1
    assert truncated is False
    assert seen
    payload = json.loads(media_watch_stats(_settings(tmp_path), client=jf))
    assert payload["counts"]["films_last_played_in_window"] == 1
