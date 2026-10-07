"""find_media — live Jellyfin lookup (T-128)."""

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
from brain.tools.media_lookup import find_media_in_library, media_lookup_tools


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
            "page_size": 2,
            **jf_overrides,
        },
    )


def _raw(
    *,
    item_id: str,
    name: str,
    kind: str,
    year: int | None = 2020,
    ticks: int | None = 60 * 600_000_000,
    played: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "Id": item_id,
        "Name": name,
        "Type": kind,
        "ProductionYear": year,
        "UserData": {"Played": played},
    }
    if ticks is not None:
        out["RunTimeTicks"] = ticks
    return out


def _mock_client(handler: Any, *, page_size: int = 2) -> JellyfinClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://jellyfin.test/",
        headers={"X-Emby-Token": "key"},
    )
    return JellyfinClient(
        "http://jellyfin.test",
        "key",
        user_id="user-1",
        page_size=page_size,
        client=http,
    )


def test_find_media_film_hit(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert "ParentId" not in params
        assert params.get("IncludeItemTypes") == "Movie,Series"
        assert params.get("SearchTerm") == "Dune"
        assert "RunTimeTicks" in (params.get("Fields") or "")
        return httpx.Response(
            200,
            json={
                "Items": [
                    _raw(
                        item_id="d1",
                        name="Dune",
                        kind="Movie",
                        year=2021,
                        ticks=155 * 600_000_000,
                        played=True,
                    )
                ],
                "TotalRecordCount": 1,
            },
        )

    jf = _mock_client(handler)
    out = json.loads(
        find_media_in_library(
            _settings(tmp_path),
            title="Dune",
            client=jf,
        )
    )
    assert out["count"] == 1
    assert out["results"][0]["kind"] == "movie"
    assert out["results"][0]["title"] == "Dune"
    assert out["results"][0]["year"] == 2021
    assert out["results"][0]["runtime_minutes"] == 155
    assert out["results"][0]["played"] is True
    assert out["truncated"] is False
    jf.close()


def test_find_media_series_hit(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Items": [
                    _raw(
                        item_id="s1",
                        name="The Bear",
                        kind="Series",
                        year=2022,
                        ticks=0,
                    )
                ],
                "TotalRecordCount": 1,
            },
        )

    jf = _mock_client(handler)
    out = json.loads(
        find_media_in_library(_settings(tmp_path), title="The Bear", client=jf)
    )
    assert out["count"] == 1
    row = out["results"][0]
    assert row["kind"] == "series"
    assert row["title"] == "The Bear"
    assert "played" not in row
    assert "total_runtime_minutes" not in row  # ticks 0 omitted
    jf.close()


def test_find_media_film_and_series_same_name(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Items": [
                    _raw(item_id="m1", name="Dune", kind="Movie", year=2021),
                    _raw(
                        item_id="s1",
                        name="Dune",
                        kind="Series",
                        year=2000,
                        ticks=3200 * 600_000_000,
                    ),
                ],
                "TotalRecordCount": 2,
            },
        )

    jf = _mock_client(handler)
    out = json.loads(
        find_media_in_library(_settings(tmp_path), title="Dune", client=jf)
    )
    kinds = {r["kind"] for r in out["results"]}
    assert kinds == {"movie", "series"}
    assert out["count"] == 2
    series = next(r for r in out["results"] if r["kind"] == "series")
    assert series["total_runtime_minutes"] == 3200
    jf.close()


def test_find_media_exact_beats_substring(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Deliberately reverse order from Jellyfin.
        return httpx.Response(
            200,
            json={
                "Items": [
                    _raw(item_id="2", name="Dune: Part Two", kind="Movie", year=2024),
                    _raw(item_id="1", name="Dune", kind="Movie", year=2021),
                ],
                "TotalRecordCount": 2,
            },
        )

    jf = _mock_client(handler)
    out = json.loads(
        find_media_in_library(_settings(tmp_path), title="Dune", client=jf)
    )
    assert out["results"][0]["title"] == "Dune"
    assert out["results"][1]["title"] == "Dune: Part Two"
    jf.close()


def test_find_media_no_hit(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})

    jf = _mock_client(handler)
    raw = find_media_in_library(_settings(tmp_path), title="Nope", client=jf)
    assert not raw.startswith("error:")
    out = json.loads(raw)
    assert out["count"] == 0
    assert out["results"] == []
    jf.close()


def test_find_media_runtime_filter(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params.get("IncludeItemTypes") == "Movie"
        assert "SearchTerm" not in params
        assert "ParentId" not in params
        return httpx.Response(
            200,
            json={
                "Items": [
                    _raw(
                        item_id="short",
                        name="Short",
                        kind="Movie",
                        ticks=90 * 600_000_000,
                    ),
                    _raw(
                        item_id="long",
                        name="Long",
                        kind="Movie",
                        ticks=150 * 600_000_000,
                    ),
                ],
                "TotalRecordCount": 2,
            },
        )

    jf = _mock_client(handler)
    out = json.loads(
        find_media_in_library(_settings(tmp_path), max_minutes=100, client=jf)
    )
    assert out["count"] == 1
    assert out["results"][0]["title"] == "Short"
    assert out["results"][0]["runtime_minutes"] == 90
    jf.close()


def test_find_media_runtime_no_match(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Items": [
                    _raw(
                        item_id="long",
                        name="Long",
                        kind="Movie",
                        ticks=150 * 600_000_000,
                    )
                ],
                "TotalRecordCount": 1,
            },
        )

    jf = _mock_client(handler)
    out = json.loads(
        find_media_in_library(_settings(tmp_path), max_minutes=100, client=jf)
    )
    assert out["count"] == 0
    jf.close()


def test_find_media_series_excluded_from_ceiling_only_query(tmp_path: Path) -> None:
    """max_minutes-only must request Movie only (client IncludeItemTypes)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params).get("IncludeItemTypes", ""))
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})

    jf = _mock_client(handler)
    find_media_in_library(_settings(tmp_path), max_minutes=100, client=jf)
    assert seen == ["Movie"]
    jf.close()


def test_find_media_title_and_ceiling_keeps_overlength_film(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Items": [
                    _raw(
                        item_id="m1",
                        name="Dune",
                        kind="Movie",
                        year=2021,
                        ticks=155 * 600_000_000,
                    ),
                    _raw(
                        item_id="s1",
                        name="Dune",
                        kind="Series",
                        year=2000,
                        ticks=500 * 600_000_000,
                    ),
                ],
                "TotalRecordCount": 2,
            },
        )

    jf = _mock_client(handler)
    out = json.loads(
        find_media_in_library(
            _settings(tmp_path),
            title="Dune",
            max_minutes=100,
            client=jf,
        )
    )
    # Both kinds kept; film marked over ceiling — not a false "not in library".
    assert out["count"] == 2
    film = next(r for r in out["results"] if r["kind"] == "movie")
    assert film["within_max_minutes"] is False
    assert film["runtime_minutes"] == 155
    series = next(r for r in out["results"] if r["kind"] == "series")
    assert "within_max_minutes" not in series
    jf.close()


def test_find_media_jellyfin_down(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    jf = _mock_client(handler)
    out = find_media_in_library(_settings(tmp_path), title="Dune", client=jf)
    assert out.startswith("error:")
    assert "jellyfin unavailable" in out
    jf.close()


def test_find_media_not_configured(tmp_path: Path) -> None:
    settings = _settings(tmp_path, url="", api_key=None, user_id="")
    out = find_media_in_library(settings, title="Dune")
    assert out.startswith("error:")
    assert "not configured" in out


def test_find_media_both_args_empty_no_http(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})

    jf = _mock_client(handler)
    out = find_media_in_library(_settings(tmp_path), title="  ", client=jf)
    assert out.startswith("error:")
    assert "title and/or max_minutes" in out
    assert calls["n"] == 0
    jf.close()


def test_find_media_invalid_max_minutes_no_http(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})

    jf = _mock_client(handler)
    out = find_media_in_library(_settings(tmp_path), max_minutes=0, client=jf)
    assert out.startswith("error:")
    assert calls["n"] == 0
    jf.close()


def test_find_media_truncation_flag(tmp_path: Path) -> None:
    pages = [
        {
            "Items": [
                _raw(item_id="a", name="A", kind="Movie"),
                _raw(item_id="b", name="B", kind="Movie"),
            ],
            "TotalRecordCount": 10,
        },
        {
            "Items": [
                _raw(item_id="c", name="C", kind="Movie"),
                _raw(item_id="d", name="D", kind="Movie"),
            ],
            "TotalRecordCount": 10,
        },
        {
            "Items": [
                _raw(item_id="e", name="E", kind="Movie"),
                _raw(item_id="f", name="F", kind="Movie"),
            ],
            "TotalRecordCount": 10,
        },
    ]
    page_i = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages[page_i["n"]]
        page_i["n"] += 1
        return httpx.Response(200, json=body)

    jf = _mock_client(handler, page_size=2)
    out = json.loads(
        find_media_in_library(_settings(tmp_path), max_minutes=999, client=jf)
    )
    assert out["truncated"] is True
    assert "truncated" in " ".join(out["notes"]).lower() or any(
        "truncated" in n for n in out["notes"]
    )
    assert page_i["n"] == 3
    jf.close()


def test_find_media_client_raises(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.find_media.side_effect = JellyfinError("jellyfin unavailable (timeout)")
    out = find_media_in_library(_settings(tmp_path), title="X", client=jf)
    assert out == "error: jellyfin unavailable (timeout)"


def test_registry_and_dispatch_empty_args(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reg = build_registry(settings, db=None)
    assert "find_media" in reg
    raw = dispatch("find_media", {}, tools=reg)
    assert raw.startswith("error:")
    assert "title and/or max_minutes" in raw


def test_tool_description_and_capability() -> None:
    assert _LOCAL_CLUSTER["find_media"] == "local.media"
    en, nl = _CLUSTER_LABELS["local.media"]
    assert "lookup" in en.lower() or "bibliotheek" in nl.lower()
    tools = media_lookup_tools(
        Settings(
            location={"latitude": 1.0, "longitude": 2.0},
            jellyfin={"url": "", "api_key": None, "user_id": ""},
        )
    )
    desc = tools["find_media"].description.lower()
    assert "do we have" in desc
    assert "never invent" in desc
    assert "max_minutes" in desc
    assert "runtime" in desc
    assert "recommend_movies" in desc


def test_read_only_schema_retention() -> None:
    assert "find_media" in _READ_ONLY_TOOL_NAMES
    assert "recommend_movies" in _READ_ONLY_TOOL_NAMES
    assert "list_recently_watched" in _READ_ONLY_TOOL_NAMES
    assert "recommend" not in _READ_ONLY_TOOL_NAMES
    schemas = [
        {"type": "function", "function": {"name": "find_media"}},
        {"type": "function", "function": {"name": "recommend_movies"}},
        {"type": "function", "function": {"name": "echo"}},
    ]
    kept = _schemas_after_tools(
        schemas,
        tools_used_this_turn=["find_media"],
        has_tool_results=True,
    )
    names = {
        (s.get("function") or {}).get("name") if isinstance(s.get("function"), dict) else None
        for s in kept
    }
    assert "find_media" in names
    assert "recommend_movies" in names


def test_tool_gate_dutch_and_en_lookup_phrases() -> None:
    assert should_offer_tools("do we have Dune?")
    assert should_offer_tools("Hebben we Dune op de kast?")
    assert should_offer_tools("something under 100 minutes")
    assert should_offer_tools("iets onder de 100 minuten")


def test_no_parent_id_in_client_params() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        assert "ParentId" not in params
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})

    jf = _mock_client(handler)
    jf.find_media(title="X", include_series=True)
    jf.close()
    assert seen
    assert all("ParentId" not in p for p in seen)
