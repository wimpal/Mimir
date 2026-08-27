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
    assert m.last_played_at is None


def test_normalize_last_played_date() -> None:
    m = normalize_item(
        {
            "Id": "m2",
            "Name": "Dune",
            "Genres": ["Sci-Fi"],
            "UserData": {
                "Played": True,
                "PlaybackPositionTicks": 0,
                "LastPlayedDate": "2026-08-20T15:30:45.1234567Z",
            },
        }
    )
    assert m is not None
    assert m.last_played_at == "2026-08-20T15:30:45Z"
    assert m.played is True


def test_normalize_bad_last_played_date() -> None:
    m = normalize_item(
        {
            "Id": "m3",
            "Name": "Nope",
            "Genres": [],
            "UserData": {"Played": True, "LastPlayedDate": "not-a-date"},
        }
    )
    assert m is not None
    assert m.last_played_at is None


def test_normalize_missing_userdata_defaults_unwatched() -> None:
    m = normalize_item({"Id": "x", "Name": "Solo", "Genres": []})
    assert m is not None
    assert m.played is False
    assert m.playback_position_ticks == 0
    assert m.last_played_at is None


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


def test_box_set_membership_maps_catalogue_ids() -> None:
    from brain.jellyfin_client import apply_box_sets

    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if params.get("IncludeItemTypes") == "BoxSet":
            return httpx.Response(
                200,
                json={
                    "Items": [{"Id": "mcu", "Name": "MCU"}],
                    "TotalRecordCount": 1,
                },
            )
        if params.get("ParentId") == "mcu":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {"Id": "im", "Name": "Iron Man"},
                        {"Id": "outside", "Name": "Not In Lib"},
                    ],
                    "TotalRecordCount": 2,
                },
            )
        return httpx.Response(200, json={"Items": [], "TotalRecordCount": 0})

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
        page_size=10,
        client=client,
    )
    membership = jf.build_box_set_membership({"im", "other"})
    assert "im" in membership
    assert membership["im"][0].name == "MCU"
    assert "outside" not in membership
    movies = {
        "im": normalize_item(
            {
                "Id": "im",
                "Name": "Iron Man",
                "Genres": ["Action"],
                "UserData": {"Played": False},
            }
        ),
        "other": normalize_item(
            {
                "Id": "other",
                "Name": "Other",
                "Genres": ["Drama"],
                "UserData": {"Played": False},
            }
        ),
    }
    assert movies["im"] is not None and movies["other"] is not None
    enriched = apply_box_sets(
        {"im": movies["im"], "other": movies["other"]},
        membership,
    )
    by_id = {m.jellyfin_id: m for m in enriched}
    assert by_id["im"].box_sets[0].id == "mcu"
    assert by_id["other"].box_sets == ()
    jf.close()
