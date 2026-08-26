"""Jellyfin HTTP client — pagination and UserData params."""

from __future__ import annotations

import httpx

from brain.jellyfin_client import JellyfinClient, normalize_item


def test_normalize_item_people_and_userdata() -> None:
    raw = {
        "Id": "m1",
        "Name": "Blade Runner",
        "ProductionYear": 1982,
        "Overview": "A replicant story.",
        "Genres": ["Sci-Fi", "Thriller"],
        "CommunityRating": 8.5,
        "OfficialRating": "R",
        "People": [
            {"Name": "Ridley Scott", "Type": "Director"},
            {"Name": "Harrison Ford", "Type": "Actor"},
            {"Name": "Rutger Hauer", "Type": "Actor"},
        ],
        "UserData": {"Played": False, "PlaybackPositionTicks": 123},
    }
    m = normalize_item(raw)
    assert m is not None
    assert m.jellyfin_id == "m1"
    assert m.name == "Blade Runner"
    assert m.year == 1982
    assert m.director == "Ridley Scott"
    assert m.cast == ("Harrison Ford", "Rutger Hauer")
    assert m.genres == ("Sci-Fi", "Thriller")
    assert m.community_rating == 8.5
    assert m.played is False
    assert m.playback_position_ticks == 123


def test_normalize_missing_userdata_defaults_unwatched() -> None:
    m = normalize_item({"Id": "x", "Name": "Solo", "Genres": []})
    assert m is not None
    assert m.played is False
    assert m.playback_position_ticks == 0


def test_iter_library_movies_paginates_and_sets_params() -> None:
    seen_params: list[dict] = []
    pages = [
        {
            "Items": [
                {
                    "Id": "1",
                    "Name": "One",
                    "Genres": ["Drama"],
                    "UserData": {"Played": True, "PlaybackPositionTicks": 0},
                }
            ],
            "TotalRecordCount": 2,
        },
        {
            "Items": [
                {
                    "Id": "2",
                    "Name": "Two",
                    "Genres": ["Comedy"],
                    "UserData": {"Played": False, "PlaybackPositionTicks": 0},
                }
            ],
            "TotalRecordCount": 2,
        },
    ]
    page_i = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/Users/user-1/Items" in str(request.url)
        params = dict(request.url.params)
        seen_params.append(params)
        assert params.get("IncludeItemTypes") == "Movie"
        assert params.get("Recursive") == "true"
        assert params.get("EnableUserData") == "true"
        assert params.get("ParentId") == "lib-movies"
        assert "Overview" in (params.get("Fields") or "")
        body = pages[page_i["n"]]
        page_i["n"] += 1
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        transport=transport,
        base_url="http://jellyfin.test/",
        headers={"X-Emby-Token": "key"},
    )
    jf = JellyfinClient(
        "http://jellyfin.test",
        "key",
        user_id="user-1",
        page_size=1,
        client=client,
    )
    movies = list(jf.iter_library_movies("lib-movies"))
    assert [m.name for m in movies] == ["One", "Two"]
    assert movies[0].played is True
    assert len(seen_params) == 2
    assert seen_params[0]["StartIndex"] == "0"
    assert seen_params[1]["StartIndex"] == "1"
    jf.close()
