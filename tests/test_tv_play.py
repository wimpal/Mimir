"""T-116 play movie on TV — phrases, resolve, coordinator (mocked).
T-127 adds series/episode on the same pipeline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from brain.agent import StoppedReason, run_turn
from brain.db import Movie
from brain.device_inventory import (
    DEVICE_LAUNCH_APP_TOOL,
    PendingDeviceStore,
    user_message_requests_device_go_home,
    user_message_requests_device_hdmi,
    user_message_requests_device_jellyfin,
    user_message_requests_device_power_off,
)
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.mcp.write_guard import user_message_requests_write
from brain.ollama import ChatMessage, ChatResponse
from brain.tools import Tool
from brain.tv_play import (
    PendingPlayDisambiguation,
    PendingPlayOnTv,
    PendingPlayStore,
    build_play_confirm_reply,
    build_play_episode_failure_reply,
    episode_availability,
    execute_play_on_tv,
    extract_play_title,
    extract_series_request,
    match_webos_controllable_sessions,
    pick_episode,
    resolve_disambiguation_pick,
    resolve_movie_for_play,
    resolve_series_for_play,
    stage_play_from_series,
    title_match_quality,
    user_message_requests_play_on_tv,
)


def _movie(
    name: str,
    *,
    year: int | None = 2010,
    jid: str | None = None,
    played: bool = False,
) -> Movie:
    return Movie(
        jellyfin_id=jid or name.casefold().replace(" ", "-"),
        name=name,
        year=year,
        overview=None,
        director=None,
        cast=(),
        genres=(),
        community_rating=None,
        official_rating=None,
        played=played,
        playback_position_ticks=0,
        last_played_at=None,
    )


def test_play_on_tv_phrases() -> None:
    assert user_message_requests_play_on_tv("play Inception on the TV")
    assert user_message_requests_play_on_tv("Play Inception on the television")
    assert user_message_requests_play_on_tv("zet Inception op de TV")
    assert user_message_requests_play_on_tv("Speel Inception op de televisie")
    assert user_message_requests_write("play Inception on the TV")
    assert extract_play_title("play Inception on the TV") == "Inception"
    assert extract_play_title("zet The Dark Knight op de TV") == "The Dark Knight"


def test_play_phrases_do_not_match_open_app() -> None:
    assert not user_message_requests_play_on_tv("start jellyfin on the TV")
    assert not user_message_requests_play_on_tv("run jellyfin on the tv")
    assert not user_message_requests_play_on_tv("switch to jellyfin")
    assert not user_message_requests_play_on_tv("turn on the TV and run jellyfin")
    assert user_message_requests_device_jellyfin("start jellyfin on the TV")
    # "play jellyfin on the TV" is open-app, not title playback
    assert not user_message_requests_play_on_tv("play jellyfin on the TV")
    assert user_message_requests_device_jellyfin("play jellyfin on the TV")


def test_play_negations_and_questions() -> None:
    assert not user_message_requests_play_on_tv("don't play Inception on the TV")
    assert not user_message_requests_play_on_tv("what should I play on the TV?")


def test_resolve_includes_watched() -> None:
    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return [_movie("Inception", played=True)]

    movie, amb, missing = resolve_movie_for_play(_Db(), "Inception")  # type: ignore[arg-type]
    assert missing is False
    assert amb is None
    assert movie is not None
    assert movie.played is True


def test_resolve_ambiguous() -> None:
    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return [
                _movie("Dune", year=1984, jid="a"),
                _movie("Dune", year=2021, jid="b"),
            ]

    movie, amb, missing = resolve_movie_for_play(_Db(), "Dune")  # type: ignore[arg-type]
    assert movie is None
    assert missing is False
    assert amb is not None
    assert len(amb) == 2


def test_disambiguation_pick() -> None:
    pending = PendingPlayDisambiguation(
        query="Dune",
        candidates=[
            {"jellyfin_id": "a", "title": "Dune", "year": 1984},
            {"jellyfin_id": "b", "title": "Dune", "year": 2021},
        ],
    )
    assert resolve_disambiguation_pick("2", pending)["jellyfin_id"] == "b"
    assert resolve_disambiguation_pick("2021", pending)["jellyfin_id"] == "b"
    assert resolve_disambiguation_pick("Dune (1984)", pending)["jellyfin_id"] == "a"
    assert resolve_disambiguation_pick("nope", pending) is None


def test_session_match_webos_only() -> None:
    sessions = [
        {
            "Id": "1",
            "Client": "Jellyfin for WebOS",
            "SupportsRemoteControl": True,
        },
        {
            "Id": "2",
            "Client": "Jellyfin Web",
            "SupportsRemoteControl": False,
        },
        {
            "Id": "3",
            "Client": "MimirBrain",
            "SupportsRemoteControl": True,
        },
    ]
    matched = match_webos_controllable_sessions(sessions)
    assert len(matched) == 1
    assert matched[0]["Id"] == "1"


def test_execute_dry_run_stops_without_play() -> None:
    pending = PendingPlayOnTv(
        device_id="dev1",
        device_name="OLED",
        jellyfin_id="item1",
        title="Inception",
        year=2010,
        sync_generation=1,
        wake_if_needed=True,
    )

    class _Db:
        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

        def list_active_movies(self) -> list[Movie]:
            return [_movie("Inception", jid="item1")]

    played: list[str] = []

    def dispatch(name: str, args: dict[str, Any], timeout_s: float) -> str:
        assert name == DEVICE_LAUNCH_APP_TOOL
        return json.dumps(
            {"id": "dev1", "name": "OLED", "status": "dry_run", "action": "launch_app"}
        )

    client = MagicMock(spec=JellyfinClient)
    client.list_sessions.side_effect = AssertionError("should not poll")
    client.play_items.side_effect = AssertionError("should not play")

    settings = MagicMock()
    ok, reply = execute_play_on_tv(
        pending,
        settings=settings,
        db=_Db(),  # type: ignore[arg-type]
        dispatch=dispatch,
        deadline_monotonic=None,
        jellyfin_client=client,
        session_poll_max_s=1.0,
    )
    assert ok is False
    assert "dry_run" in reply.lower()
    client.play_items.assert_not_called()
    assert played == []


def test_execute_happy_path() -> None:
    pending = PendingPlayOnTv(
        device_id="dev1",
        device_name="OLED",
        jellyfin_id="item1",
        title="Inception",
        year=2010,
        sync_generation=1,
        wake_if_needed=False,
        dutch=False,
    )

    class _Db:
        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

        def list_active_movies(self) -> list[Movie]:
            return [_movie("Inception", jid="item1")]

    def dispatch(name: str, args: dict[str, Any], timeout_s: float) -> str:
        assert args["target"] == "jellyfin"
        assert "wake_if_needed" not in args
        return json.dumps(
            {"id": "dev1", "name": "OLED", "status": "ok", "action": "launch_app"}
        )

    client = MagicMock(spec=JellyfinClient)
    client.list_sessions.return_value = [
        {
            "Id": "sess-webos",
            "Client": "Jellyfin for WebOS",
            "SupportsRemoteControl": True,
        }
    ]
    settings = MagicMock()
    ok, reply = execute_play_on_tv(
        pending,
        settings=settings,
        db=_Db(),  # type: ignore[arg-type]
        dispatch=dispatch,
        deadline_monotonic=None,
        jellyfin_client=client,
        session_poll_max_s=1.0,
    )
    assert ok is True
    assert "Inception" in reply
    client.play_items.assert_called_once_with("sess-webos", ["item1"])


def test_execute_ambiguous_sessions_fails() -> None:
    pending = PendingPlayOnTv(
        device_id="dev1",
        device_name="OLED",
        jellyfin_id="item1",
        title="Inception",
        year=2010,
        sync_generation=1,
        wake_if_needed=False,
    )

    class _Db:
        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

        def list_active_movies(self) -> list[Movie]:
            return [_movie("Inception", jid="item1")]

    def dispatch(name: str, args: dict[str, Any], timeout_s: float) -> str:
        return json.dumps({"status": "ok", "action": "launch_app"})

    client = MagicMock(spec=JellyfinClient)
    client.list_sessions.return_value = [
        {
            "Id": "a",
            "Client": "Jellyfin for WebOS",
            "SupportsRemoteControl": True,
        },
        {
            "Id": "b",
            "Client": "Jellyfin for WebOS",
            "SupportsRemoteControl": True,
        },
    ]
    ok, reply = execute_play_on_tv(
        pending,
        settings=MagicMock(),
        db=_Db(),  # type: ignore[arg-type]
        dispatch=dispatch,
        deadline_monotonic=None,
        jellyfin_client=client,
        session_poll_max_s=0.1,
    )
    assert ok is False
    assert "session" in reply.lower() or "sessie" in reply.lower()
    client.play_items.assert_not_called()


def test_execute_launch_ok_play_fails() -> None:
    pending = PendingPlayOnTv(
        device_id="dev1",
        device_name="OLED",
        jellyfin_id="item1",
        title="Inception",
        year=2010,
        sync_generation=1,
        wake_if_needed=False,
    )

    class _Db:
        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

        def list_active_movies(self) -> list[Movie]:
            return [_movie("Inception", jid="item1")]

    def dispatch(name: str, args: dict[str, Any], timeout_s: float) -> str:
        return json.dumps({"status": "ok"})

    client = MagicMock(spec=JellyfinClient)
    client.list_sessions.return_value = [
        {
            "Id": "sess",
            "Client": "Jellyfin for WebOS",
            "SupportsRemoteControl": True,
        }
    ]
    client.play_items.side_effect = JellyfinError("jellyfin unavailable (HTTP 500)")
    ok, reply = execute_play_on_tv(
        pending,
        settings=MagicMock(),
        db=_Db(),  # type: ignore[arg-type]
        dispatch=dispatch,
        deadline_monotonic=None,
        jellyfin_client=client,
        session_poll_max_s=1.0,
    )
    assert ok is False
    assert "playback failed" in reply.lower() or "mislukt" in reply.lower()


def test_agent_stages_play_confirm() -> None:
    movies = [_movie("Inception", jid="item1")]

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return movies

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    def list_handler(**_args: Any) -> str:
        return json.dumps(
            [{"id": "tv1", "name": "OLED", "tv_capable": True}]
        )

    tools = {
        "homebase.devices.list": Tool(
            name="homebase.devices.list",
            description="list",
            parameters={"type": "object", "properties": {}},
            execute=list_handler,
        ),
        DEVICE_LAUNCH_APP_TOOL: Tool(
            name=DEVICE_LAUNCH_APP_TOOL,
            description="launch",
            parameters={
                "type": "object",
                "properties": {
                    "device_id": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["device_id", "target"],
            },
            execute=lambda **a: json.dumps({"status": "ok"}),
        ),
    }
    store = PendingPlayStore()
    client = MagicMock()
    client.chat.side_effect = AssertionError("Ollama must not run for play phrase")

    result = run_turn(
        client,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play Inception on the TV"),
        ],
        tools=tools,
        conversation_id="c1",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=MagicMock(),
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert "Inception" in result.content
    assert "not done yet" in result.content.lower() or "nog niet" in result.content.lower()
    assert store.is_confirmable("c1")
    pending = store.get_play("c1")
    assert pending is not None
    assert pending.jellyfin_id == "item1"
    assert build_play_confirm_reply(pending)


def test_agent_open_app_still_not_play() -> None:
    """T-114 open-app phrase must not take the play-on-TV path."""
    assert not user_message_requests_play_on_tv("start Jellyfin on the TV")
    assert user_message_requests_device_jellyfin("start Jellyfin on the TV")


def test_agent_ambiguous_then_pick() -> None:
    movies = [
        _movie("Dune", year=1984, jid="a"),
        _movie("Dune", year=2021, jid="b"),
    ]

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return movies

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    tools = {
        "homebase.devices.list": Tool(
            name="homebase.devices.list",
            description="list",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_a: json.dumps(
                [{"id": "tv1", "name": "OLED", "tv_capable": True}]
            ),
        ),
        DEVICE_LAUNCH_APP_TOOL: Tool(
            name=DEVICE_LAUNCH_APP_TOOL,
            description="launch",
            parameters={"type": "object", "properties": {}},
            execute=lambda **a: json.dumps({"status": "ok"}),
        ),
    }
    store = PendingPlayStore()
    client = MagicMock()
    client.chat.return_value = ChatResponse(
        message=ChatMessage(role="assistant", content="noop"),
        raw={},
        timings={},
    )

    r1 = run_turn(
        client,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play Dune on the TV"),
        ],
        tools=tools,
        conversation_id="c2",
        pending_play=store,
        db=_Db(),
        settings=MagicMock(),
    )
    assert "which" in r1.content.lower() or "welke" in r1.content.lower()
    assert store.has_ambiguous("c2")

    r2 = run_turn(
        client,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="2"),
        ],
        tools=tools,
        conversation_id="c2",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=MagicMock(),
    )
    assert store.is_confirmable("c2")
    pending = store.get_play("c2")
    assert pending is not None
    assert pending.jellyfin_id == "b"
    assert "2021" in r2.content or "Dune" in r2.content


# ---------------------------------------------------------------- T-127 helpers


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeHttpx:
    """Minimal httpx.Client stand-in recording GET/POST calls."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.gets.append((path, dict(params or {})))
        return self._responses.pop(0)

    def post(self, path: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.posts.append((path, dict(params or {})))
        return self._responses.pop(0)


def _client(responses: list[_FakeResponse]) -> tuple[JellyfinClient, _FakeHttpx]:
    fake = _FakeHttpx(responses)
    client = JellyfinClient("http://jf", "key", user_id="u1", client=fake)  # type: ignore[arg-type]
    return client, fake


def _series_item(
    name: str, *, jid: str = "s1", year: int | None = 2022
) -> dict[str, Any]:
    return {"Id": jid, "Name": name, "ProductionYear": year, "Type": "Series"}


def _episode(
    season: int,
    number: int,
    *,
    played: bool = False,
    eid: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "Id": eid or f"ep-{season}-{number}",
        "Name": name or f"Episode {number}",
        "SeriesName": "The Bear",
        "ParentIndexNumber": season,
        "IndexNumber": number,
        "UserData": {"Played": played},
    }


def _bear_episodes() -> list[dict[str, Any]]:
    return [
        _episode(1, 1, played=True),
        _episode(1, 2, played=True),
        _episode(1, 3),
        _episode(2, 1),
    ]


def _settings() -> Any:
    return SimpleNamespace(
        jellyfin=SimpleNamespace(library_ids=["lib1"]),
        timeouts=SimpleNamespace(tool_s=20.0),
    )


def _devices_and_launch_tools() -> dict[str, Tool]:
    return {
        "homebase.devices.list": Tool(
            name="homebase.devices.list",
            description="list",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_a: json.dumps(
                [{"id": "tv1", "name": "OLED", "tv_capable": True}]
            ),
        ),
        DEVICE_LAUNCH_APP_TOOL: Tool(
            name=DEVICE_LAUNCH_APP_TOOL,
            description="launch",
            parameters={"type": "object", "properties": {}},
            execute=lambda **a: json.dumps({"status": "ok"}),
        ),
    }


# ------------------------------------------------- T-127 Step A: client reads


def test_find_series_queries_all_libraries_without_parent() -> None:
    """Regression (live 2026-10-01): the TV library is not in library_ids.

    Scoping the search with ParentId=<movie library> returned zero series, so
    'reacher' could never beat the movie Machine Gun Preacher.
    """
    client, fake = _client(
        [_FakeResponse(200, {"Items": [_series_item("Reacher")], "TotalRecordCount": 1})]
    )
    items = client.find_series("reacher")
    assert [i["Id"] for i in items] == ["s1"]
    assert len(fake.gets) == 1
    path, params = fake.gets[0]
    assert path == "Users/u1/Items"
    assert params["IncludeItemTypes"] == "Series"
    assert params["SearchTerm"] == "reacher"
    assert params["EnableUserData"] == "true"
    assert "ParentId" not in params


def test_find_series_returns_empty_for_blank_title() -> None:
    client, fake = _client([])
    assert client.find_series("   ") == []
    assert fake.gets == []


def test_list_episodes_requests_userdata() -> None:
    client, fake = _client(
        [_FakeResponse(200, {"Items": _bear_episodes(), "TotalRecordCount": 4})]
    )
    episodes = client.list_episodes("s1")
    assert len(episodes) == 4
    params = fake.gets[0][1]
    assert params["ParentId"] == "s1"
    assert params["IncludeItemTypes"] == "Episode"
    assert params["EnableUserData"] == "true"


def test_get_item_404_is_none_other_errors_raise() -> None:
    client, _fake = _client([_FakeResponse(404, {"error": "gone"})])
    assert client.get_item("ep-1-3") is None

    client2, _fake2 = _client([_FakeResponse(500, {})])
    try:
        client2.get_item("ep-1-3")
    except JellyfinError as exc:
        assert "500" in str(exc)
    else:  # pragma: no cover - must raise
        raise AssertionError("expected JellyfinError")


def test_find_series_upstream_error_raises() -> None:
    client, _fake = _client([_FakeResponse(502, {})])
    try:
        client.find_series("bear")
    except JellyfinError:
        pass
    else:  # pragma: no cover - must raise
        raise AssertionError("expected JellyfinError")


# ------------------------------------------ T-127 Step B: resolve + selector


def test_resolve_series_exact_substring_ambiguous_missing() -> None:
    client, _fake = _client(
        [_FakeResponse(200, {"Items": [_series_item("The Bear")]})]
    )
    found, amb, missing = resolve_series_for_play(
        client, title="the bear"
    )
    assert missing is False and amb is None
    assert found is not None and found.jellyfin_id == "s1"
    assert found.title == "The Bear" and found.year == 2022

    client2, _f2 = _client(
        [_FakeResponse(200, {"Items": [_series_item("The Bear Tonight")]})]
    )
    found2, amb2, missing2 = resolve_series_for_play(
        client2, title="bear"
    )
    assert found2 is not None and amb2 is None and missing2 is False

    client3, _f3 = _client(
        [
            _FakeResponse(
                200,
                {
                    "Items": [
                        _series_item("Dune", jid="a", year=1984),
                        _series_item("Dune", jid="b", year=2021),
                    ]
                },
            )
        ]
    )
    found3, amb3, missing3 = resolve_series_for_play(
        client3, title="Dune"
    )
    assert found3 is None and missing3 is False
    assert amb3 is not None and len(amb3) == 2

    client4, _f4 = _client([_FakeResponse(200, {"Items": []})])
    found4, amb4, missing4 = resolve_series_for_play(
        client4, title="Nope"
    )
    assert found4 is None and amb4 is None and missing4 is True


def test_pick_episode_next_unplayed_after_furthest() -> None:
    picked, reason = pick_episode(_bear_episodes())
    assert reason == "unplayed"
    assert picked is not None and picked["Id"] == "ep-1-3"


def test_pick_episode_skips_finished_seasons_and_specials() -> None:
    episodes = [
        _episode(0, 1),
        _episode(1, 1, played=True),
        _episode(1, 2, played=True),
        _episode(2, 1),
    ]
    picked, reason = pick_episode(episodes)
    assert reason == "unplayed"
    assert picked is not None and picked["Id"] == "ep-2-1"


def test_pick_episode_skips_earlier_holes_after_progress() -> None:
    """Modern Family case: S3 holes must not beat continue-at S8E11."""
    episodes = [
        _episode(3, 14, played=True, name="Tableau Vivant"),
        _episode(3, 15, name="Aunt Mommy"),
        _episode(3, 16, name="Virgin Territory"),
        _episode(8, 10, played=True, name="Ringmaster Keifth"),
        _episode(8, 11, name="Sarge & Pea"),
        _episode(8, 12, name="Do You Believe In Magic"),
    ]
    picked, reason = pick_episode(episodes)
    assert reason == "unplayed"
    assert picked is not None
    assert picked["Id"] == "ep-8-11"
    assert picked["Name"] == "Sarge & Pea"


def test_pick_episode_caught_up_with_earlier_holes() -> None:
    episodes = [
        _episode(3, 15, name="Aunt Mommy"),
        _episode(8, 10, played=True, name="Ringmaster Keifth"),
        _episode(8, 11, played=True, name="Sarge & Pea"),
    ]
    picked, reason = pick_episode(episodes)
    assert picked is None and reason == "caught-up"
    reply = build_play_episode_failure_reply("caught-up", "Modern Family", dutch=False)
    assert "caught up" in reply.casefold()
    assert "all episodes" not in reply.casefold()


def test_next_up_episode_requests_series_id() -> None:
    client, fake = _client(
        [
            _FakeResponse(
                200,
                {
                    "Items": [_episode(8, 11, eid="next", name="Sarge & Pea")],
                    "TotalRecordCount": 1,
                },
            )
        ]
    )
    item = client.next_up_episode("s1")
    assert item is not None and item["Id"] == "next"
    path, params = fake.gets[0]
    assert path == "Shows/NextUp"
    assert params["SeriesId"] == "s1"
    assert params["UserId"] == "u1"
    assert params["Limit"] == 1


def test_agent_uses_next_up_over_local_hole(
    monkeypatch: Any,
) -> None:
    """Bare series play prefers Jellyfin NextUp over an earlier unplayed hole."""
    import brain.agent as agent_mod
    import brain.tv_play as tv_play_mod

    hole_eps = [
        _episode(3, 15, name="Aunt Mommy"),
        _episode(8, 10, played=True, name="Ringmaster Keifth"),
        _episode(8, 11, name="Sarge & Pea"),
    ]
    client = _fake_series_client(
        hole_eps,
        series_items=[_series_item("Modern Family", jid="mf1", year=2009)],
    )
    client.next_up_episode.return_value = _episode(
        8, 11, eid="ep-8-11", name="Sarge & Pea"
    )
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)
    monkeypatch.setattr(tv_play_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return []

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")

    r1 = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play Modern Family on the TV"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s1",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    assert "S8E11" in r1.content or "Sarge & Pea" in r1.content
    assert "Aunt Mommy" not in r1.content
    client.next_up_episode.assert_called_once_with("mf1")


def test_pick_episode_explicit_number() -> None:
    picked, reason = pick_episode(_bear_episodes(), episode=2)
    assert reason == "requested"
    assert picked is not None and picked["Id"] == "ep-1-2"

    picked2, reason2 = pick_episode(_bear_episodes(), season=2, episode=1)
    assert reason2 == "requested"
    assert picked2 is not None and picked2["Id"] == "ep-2-1"


def test_pick_episode_wrong_season_and_season_not_found() -> None:
    picked, reason = pick_episode(_bear_episodes(), season=3, episode=1)
    assert picked is None and reason == "season-not-found"

    picked2, reason2 = pick_episode(_bear_episodes(), season=2, episode=9)
    assert picked2 is None and reason2 == "episode-not-found"
    # Episode number without a season: first season that has that number.
    picked3, reason3 = pick_episode(_bear_episodes(), episode=9)
    assert picked3 is None and reason3 == "episode-not-found"


def test_pick_episode_all_played_and_no_episodes() -> None:
    watched = [_episode(1, 1, played=True), _episode(1, 2, played=True)]
    picked, reason = pick_episode(watched)
    assert picked is None and reason == "all-played"

    picked2, reason2 = pick_episode([])
    assert picked2 is None and reason2 == "no-episodes"

    picked3, reason3 = pick_episode([{"Id": "x", "Name": "Special"}])
    assert picked3 is None and reason3 == "no-episodes"


def test_pick_episode_treats_missing_userdata_as_unplayed() -> None:
    episodes = [{"Id": "a", "Name": "E1", "ParentIndexNumber": 1, "IndexNumber": 1}]
    picked, reason = pick_episode(episodes)
    assert reason == "unplayed" and picked is not None and picked["Id"] == "a"


def test_episode_availability_summary() -> None:
    assert episode_availability(_bear_episodes()) == "(seasons 1-2)"
    assert episode_availability([_episode(1, 1), _episode(1, 7)]) == "(S1 has episodes 1-7)"
    assert episode_availability([]) == ""


# ------------------------------------------------ T-127 Step C: staged + play


def test_stage_play_from_series_carries_episode_fields() -> None:
    from brain.tv_play import Playable

    class _Db:
        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=4)

    pending = stage_play_from_series(
        series=Playable(jellyfin_id="s1", title="The Bear", year=2022),
        episode=_episode(2, 1, name="System"),
        device={"id": "tv1", "name": "OLED"},
        wake_if_needed=True,
        dutch=False,
        db=_Db(),  # type: ignore[arg-type]
    )
    assert pending.media_kind == "series"
    assert pending.jellyfin_id == "ep-2-1"  # played id is the episode id
    assert pending.series_id == "s1"
    assert pending.season_number == 2 and pending.episode_number == 1
    assert pending.episode_title == "System"
    reply = build_play_confirm_reply(pending)
    assert "S2E1" in reply and "The Bear" in reply and "not done yet" in reply


def test_execute_series_happy_path_plays_episode_id() -> None:
    pending = PendingPlayOnTv(
        device_id="dev1",
        device_name="OLED",
        jellyfin_id="ep-1-3",
        title="The Bear",
        year=2022,
        sync_generation=1,
        wake_if_needed=False,
        media_kind="series",
        series_id="s1",
        episode_id="ep-1-3",
        season_number=1,
        episode_number=3,
        episode_title="Episode 3",
    )

    class _Db:
        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

        def list_active_movies(self) -> list[Movie]:
            return []

    def dispatch(name: str, args: dict[str, Any], timeout_s: float) -> str:
        return json.dumps({"status": "ok", "action": "launch_app"})

    client = MagicMock(spec=JellyfinClient)
    client.get_item.return_value = _episode(1, 3)
    client.list_sessions.return_value = [
        {"Id": "sess-webos", "Client": "Jellyfin for WebOS", "SupportsRemoteControl": True}
    ]
    ok, reply = execute_play_on_tv(
        pending,
        settings=MagicMock(),
        db=_Db(),  # type: ignore[arg-type]
        dispatch=dispatch,
        deadline_monotonic=None,
        jellyfin_client=client,
        session_poll_max_s=1.0,
    )
    assert ok is True
    assert "S1E3" in reply and "The Bear" in reply
    client.play_items.assert_called_once_with("sess-webos", ["ep-1-3"])


def test_execute_series_revalidates_episode_availability() -> None:
    pending = PendingPlayOnTv(
        device_id="dev1",
        device_name="OLED",
        jellyfin_id="ep-9-9",
        title="The Bear",
        year=2022,
        sync_generation=1,
        wake_if_needed=False,
        media_kind="series",
    )

    def dispatch(name: str, args: dict[str, Any], timeout_s: float) -> str:
        raise AssertionError("must not launch the TV when the episode is gone")

    client = MagicMock(spec=JellyfinClient)
    client.get_item.return_value = None
    ok, reply = execute_play_on_tv(
        pending,
        settings=MagicMock(),
        db=MagicMock(),
        dispatch=dispatch,
        deadline_monotonic=None,
        jellyfin_client=client,
        session_poll_max_s=1.0,
    )
    assert ok is False
    assert "no longer available" in reply.lower()
    client.play_items.assert_not_called()


# ------------------------------------------- T-127 Step D: phrases + wiring


def test_series_phrase_extraction_table() -> None:
    req = extract_series_request("put The Bear on the TV")
    assert req is None or req.title == "The Bear"  # no marker → movie-first
    assert not extract_series_request("play Inception on the TV")

    req2 = extract_series_request("play episode 3 of Severance on the TV")
    assert req2 is not None
    assert (req2.title, req2.season, req2.episode) == ("Severance", None, 3)

    req3 = extract_series_request("play season 2 episode 3 of Severance on the TV")
    assert req3 is not None
    assert (req3.title, req3.season, req3.episode) == ("Severance", 2, 3)

    req4 = extract_series_request("play the next episode of The Bear on the TV")
    assert req4 is not None
    assert req4.title == "The Bear" and req4.next_episode

    req5 = extract_series_request("zet aflevering 3 van Severance op de TV")
    assert req5 is not None
    assert (req5.title, req5.episode) == ("Severance", 3)

    req6 = extract_series_request("zet seizoen 2 aflevering 3 van Severance op de TV")
    assert req6 is not None
    assert (req6.title, req6.season, req6.episode) == ("Severance", 2, 3)

    req7 = extract_series_request("zet de volgende aflevering van The Bear op de TV")
    assert req7 is not None
    assert req7.title == "The Bear" and req7.next_episode

    req8 = extract_series_request("play the series Severance on the TV")
    assert req8 is not None and req8.title == "Severance"

    assert extract_play_title("put The Bear on the TV") == "The Bear"
    assert extract_play_title("play The Bear on the TV") == "The Bear"
    assert user_message_requests_play_on_tv("put The Bear on the TV")
    assert user_message_requests_write("play episode 3 of Severance on the TV")
    assert user_message_requests_write("zet aflevering 3 van Severance op de TV")


def test_phrase_collisions_route_to_pre_existing_classifiers() -> None:
    assert user_message_requests_device_jellyfin("start jellyfin on the TV")
    assert user_message_requests_device_jellyfin("play jellyfin on the TV")
    assert not user_message_requests_play_on_tv("play jellyfin on the TV")
    assert user_message_requests_device_hdmi("switch the tv to hdmi 1")
    assert user_message_requests_device_power_off("turn off the TV")
    assert user_message_requests_device_go_home("open home on the TV")
    assert not user_message_requests_play_on_tv("what should I play on the TV?")
    assert not user_message_requests_play_on_tv("don't play The Bear on the TV")
    # Device words can never become a title resolve.
    assert not user_message_requests_play_on_tv("put the tv on the television")
    assert extract_series_request("put hdmi1 on the TV") is None


def _fake_series_client(
    episodes: list[dict[str, Any]],
    *,
    series_items: list[dict[str, Any]] | None = None,
) -> MagicMock:
    items = series_items if series_items is not None else [_series_item("The Bear")]
    client = MagicMock(spec=JellyfinClient)
    # Honour SearchTerm so resolves behave like the real server.
    client.find_series.side_effect = lambda title: [
        i for i in items if title.casefold() in str(i["Name"]).casefold()
    ]
    client.list_episodes.return_value = episodes
    client.next_up_episode.return_value = None
    client.get_item.return_value = episodes[0] if episodes else None
    client.list_sessions.return_value = [
        {"Id": "sess-webos", "Client": "Jellyfin for WebOS", "SupportsRemoteControl": True}
    ]
    return client


def test_agent_series_stage_then_confirm_plays_episode(
    monkeypatch: Any,
) -> None:
    import brain.agent as agent_mod
    import brain.tv_play as tv_play_mod

    client = _fake_series_client(_bear_episodes())
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)
    monkeypatch.setattr(tv_play_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return []

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")

    r1 = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="put The Bear on the TV"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s1",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    assert r1.stopped_reason == StoppedReason.FINAL
    assert "not done yet" in r1.content.lower()
    pending = store.get_play("s1")
    assert pending is not None
    assert pending.media_kind == "series"
    assert pending.jellyfin_id == "ep-1-3"
    assert "S1E3" in r1.content

    r2 = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="yes"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s1",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    assert r2.stopped_reason == StoppedReason.FINAL
    assert "Playing" in r2.content and "S1E3" in r2.content
    client.play_items.assert_called_once_with("sess-webos", ["ep-1-3"])


def test_agent_series_explicit_episode_number(monkeypatch: Any) -> None:
    import brain.agent as agent_mod
    import brain.tv_play as tv_play_mod

    client = _fake_series_client(_bear_episodes())
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)
    monkeypatch.setattr(tv_play_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return []

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(
                role="user",
                content="play season 2 episode 1 of The Bear on the TV",
            ),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s2",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    assert "S2E1" in result.content
    pending = store.get_play("s2")
    assert pending is not None and pending.jellyfin_id == "ep-2-1"


def test_agent_series_all_watched_asks(monkeypatch: Any) -> None:
    import brain.agent as agent_mod

    watched = [_episode(1, 1, played=True), _episode(1, 2, played=True)]
    client = _fake_series_client(watched)
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return []

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play the next episode of The Bear on the TV"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s3",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    assert "watched" in result.content.lower()
    assert store.get_play("s3") is None
    client.play_items.assert_not_called()


def test_agent_series_ambiguous_then_pick(monkeypatch: Any) -> None:
    import brain.agent as agent_mod
    import brain.tv_play as tv_play_mod

    client = _fake_series_client(
        _bear_episodes(),
        series_items=[
            _series_item("Severance", jid="a", year=2022),
            _series_item("Severance", jid="b", year=2022),
        ],
    )
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)
    monkeypatch.setattr(tv_play_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return []

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.return_value = ChatResponse(
        message=ChatMessage(role="assistant", content="noop"), raw={}, timings={}
    )
    r1 = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play the next episode of Severance on the TV"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s4",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    assert "which" in r1.content.lower() or "welke" in r1.content.lower()
    assert store.has_ambiguous("s4")

    r2 = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="2"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s4",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    pending = store.get_play("s4")
    assert pending is not None
    assert pending.series_id == "b"
    assert pending.jellyfin_id == "ep-1-3"
    assert "S1E3" in r2.content


def test_agent_movie_wins_over_same_name_series(monkeypatch: Any) -> None:
    import brain.agent as agent_mod

    client = _fake_series_client(_bear_episodes())
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return [_movie("Dune", year=2021, jid="m1")]

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play Dune on the TV"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="m1c",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    pending = store.get_play("m1c")
    assert pending is not None
    assert pending.media_kind == "movie" and pending.jellyfin_id == "m1"
    client.find_series.assert_not_called()
    assert "Dune" in result.content


def test_title_match_quality() -> None:
    assert title_match_quality("Reacher", "Reacher") == "exact"
    assert title_match_quality("reacher", "REACHER") == "exact"
    assert title_match_quality("reacher", "Machine Gun Preacher") == "contains"
    assert title_match_quality("reacher", "Severance") == "none"
    assert title_match_quality("", "Reacher") == "none"
    assert title_match_quality("Reacher", "") == "none"


def test_agent_exact_series_beats_substring_movie(monkeypatch: Any) -> None:
    """Regression: 'reacher' must not resolve to the movie Machine Gun Preacher."""
    import brain.agent as agent_mod

    client = _fake_series_client(
        _bear_episodes(), series_items=[_series_item("Reacher", jid="r1", year=2022)]
    )
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return [_movie("Machine Gun Preacher", year=2011, jid="m1")]

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play reacher on the tv"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="r1c",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    pending = store.get_play("r1c")
    assert pending is not None
    assert pending.media_kind == "series"
    assert pending.series_id == "r1"
    assert "Reacher" in result.content
    assert "Machine Gun Preacher" not in result.content


def test_agent_substring_series_does_not_beat_substring_movie(
    monkeypatch: Any,
) -> None:
    """Ties stay with the movie — only exact beats a weaker match."""
    import brain.agent as agent_mod

    client = _fake_series_client(
        _bear_episodes(),
        series_items=[_series_item("Reacher County", jid="r1", year=2022)],
    )
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return [_movie("Machine Gun Preacher", year=2011, jid="m1")]

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play reacher on the tv"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="r2c",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    pending = store.get_play("r2c")
    assert pending is not None
    assert pending.media_kind == "movie" and pending.jellyfin_id == "m1"
    assert "Machine Gun Preacher" in result.content


def test_agent_substring_movie_still_plays_when_jellyfin_is_down(
    monkeypatch: Any,
) -> None:
    """A failing series probe must never cost the operator their movie."""
    import brain.agent as agent_mod

    def _boom(settings: Any) -> Any:
        raise JellyfinError("jellyfin unavailable (network)")

    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", _boom)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return [_movie("Machine Gun Preacher", year=2011, jid="m1")]

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play preacher on the tv"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="r3c",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    pending = store.get_play("r3c")
    assert pending is not None
    assert pending.media_kind == "movie" and pending.jellyfin_id == "m1"
    assert "Machine Gun Preacher" in result.content


def test_agent_movie_still_wins_when_series_is_ambiguous(monkeypatch: Any) -> None:
    """Ambiguous series must not hijack a matched movie — no guessing, no ask."""
    import brain.agent as agent_mod

    client = _fake_series_client(
        _bear_episodes(),
        series_items=[
            _series_item("Reacher", jid="r1", year=2022),
            _series_item("Reacher", jid="r2", year=2022),
        ],
    )
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return [_movie("Machine Gun Preacher", year=2011, jid="m1")]

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play preacher on the tv"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="r4c",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    pending = store.get_play("r4c")
    assert pending is not None
    assert pending.media_kind == "movie" and pending.jellyfin_id == "m1"
    assert not store.has_ambiguous("r4c")
    assert "Machine Gun Preacher" in result.content


def test_agent_series_fallback_when_no_movie_matched(monkeypatch: Any) -> None:
    """No series marker, no movie in the catalogue → live series resolve."""
    import brain.agent as agent_mod

    client = _fake_series_client(_bear_episodes())
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: client)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return []

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="put The Bear on the TV"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s5",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    pending = store.get_play("s5")
    assert pending is not None
    assert pending.media_kind == "series"
    assert pending.title == "The Bear"
    assert "S1E3" in result.content


def test_agent_nothing_in_jellyfin_keeps_movie_missing_wording(
    monkeypatch: Any,
) -> None:
    import brain.agent as agent_mod

    empty = MagicMock(spec=JellyfinClient)
    empty.find_series.return_value = []
    empty.list_episodes.return_value = []
    monkeypatch.setattr(agent_mod, "jellyfin_client_from_settings", lambda s: empty)

    class _Db:
        def list_active_movies(self) -> list[Movie]:
            return []

        def get_sync_state(self) -> Any:
            return MagicMock(active_generation=1)

    store = PendingPlayStore()
    ollama = MagicMock()
    ollama.chat.side_effect = AssertionError("Ollama must not run for play phrase")
    result = run_turn(
        ollama,
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="play Nonexistent on the TV"),
        ],
        tools=_devices_and_launch_tools(),
        conversation_id="s6",
        pending_play=store,
        pending_devices=PendingDeviceStore(),
        db=_Db(),
        settings=_settings(),
    )
    assert "no movie" in result.content.lower()
    assert store.get_play("s6") is None
