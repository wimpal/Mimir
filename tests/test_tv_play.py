"""T-116 play movie on TV — phrases, resolve, coordinator (mocked)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from brain.agent import StoppedReason, run_turn
from brain.db import Movie
from brain.device_inventory import (
    DEVICE_LAUNCH_APP_TOOL,
    PendingDeviceStore,
    user_message_requests_device_jellyfin,
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
    execute_play_on_tv,
    extract_play_title,
    match_webos_controllable_sessions,
    resolve_disambiguation_pick,
    resolve_movie_for_play,
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
