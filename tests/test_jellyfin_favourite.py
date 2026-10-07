"""Jellyfin favourite / unfavourite — early M3 path (T-130)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx

from brain.agent import ChatMessage, run_turn
from brain.config import Settings
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.jellyfin_favourite import (
    FavouriteCandidate,
    PendingFavourite,
    PendingFavouriteDisambiguation,
    PendingFavouriteStore,
    execute_favourite_write,
    extract_favourite_request,
    resolve_favourite_disambiguation_pick,
    resolve_favourite_target,
    stage_favourite_from_request,
    user_message_requests_favourite,
)


class _FakeChat:
    def chat(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Ollama must not run on favourite early path")


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


def _movie_raw(
    *,
    item_id: str = "m1",
    name: str = "Inception",
    year: int = 2010,
) -> dict[str, Any]:
    return {
        "Id": item_id,
        "Name": name,
        "Type": "Movie",
        "ProductionYear": year,
        "UserData": {"IsFavorite": False},
    }


def _series_raw(
    *,
    item_id: str = "s1",
    name: str = "Dune",
    year: int = 2000,
) -> dict[str, Any]:
    return {
        "Id": item_id,
        "Name": name,
        "Type": "Series",
        "ProductionYear": year,
        "UserData": {"IsFavorite": False},
    }


def test_extract_mark_and_unmark() -> None:
    req = extract_favourite_request("mark Inception as a favourite")
    assert req is not None and req.favorite and req.title == "Inception"
    req2 = extract_favourite_request("remove the favourite from Reacher")
    assert req2 is not None and not req2.favorite and req2.title == "Reacher"
    req3 = extract_favourite_request("maak Dune een favoriet")
    assert req3 is not None and req3.favorite and req3.dutch
    assert extract_favourite_request("what is my favourite movie?") is None
    assert extract_favourite_request("don't mark Inception as a favourite") is None
    assert extract_favourite_request("favourite episode 3 of Reacher") is None


def test_resolve_ambiguous_film_and_series() -> None:
    jf = MagicMock()
    jf.find_media.return_value = (
        [_movie_raw(name="Dune", year=2021), _series_raw(name="Dune", year=2000)],
        False,
    )
    resolved = resolve_favourite_target(jf, "Dune")
    assert resolved.status == "ambiguous"
    assert len(resolved.candidates) == 2
    kinds = {c.kind for c in resolved.candidates}
    assert kinds == {"movie", "series"}


def test_resolve_truncated_fail_closed() -> None:
    jf = MagicMock()
    jf.find_media.return_value = ([_movie_raw()], True)
    assert resolve_favourite_target(jf, "Inception").status == "truncated"


def test_stage_does_not_write(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.find_media.return_value = ([_movie_raw()], False)
    jf.set_favorite = MagicMock()
    reply, pending, amb = stage_favourite_from_request(
        _settings(tmp_path),
        extract_favourite_request("mark Inception as a favourite"),  # type: ignore[arg-type]
        client=jf,
    )
    assert pending is not None
    assert amb is None
    assert "not done yet" in reply.casefold() or "nog niet" in reply.casefold()
    jf.set_favorite.assert_not_called()


def test_confirm_writes_once(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.set_favorite.return_value = True
    pending = PendingFavourite(
        item_id="m1",
        title="Inception",
        year=2010,
        kind="movie",
        favorite=True,
        dutch=False,
    )
    ok, reply = execute_favourite_write(_settings(tmp_path), pending, client=jf)
    assert ok
    assert "now a favourite" in reply.casefold()
    jf.set_favorite.assert_called_once_with("m1", favorite=True)


def test_decline_clears_no_write(tmp_path: Path) -> None:
    store = PendingFavouriteStore()
    store.set_favourite(
        "c1",
        PendingFavourite(
            item_id="m1",
            title="Inception",
            year=2010,
            kind="movie",
            favorite=True,
            dutch=False,
        ),
    )
    settings = _settings(tmp_path)

    result = run_turn(
        _FakeChat(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="no"),
        ],
        tools={},
        conversation_id="c1",
        pending_favourites=store,
        settings=settings,
    )
    assert (
        "not changed" in result.content.casefold()
        or "niet gewijzigd" in result.content.casefold()
    )
    assert not store.has_favourite("c1")


def test_agent_stage_then_confirm_no_write_before(tmp_path: Path, monkeypatch: Any) -> None:
    store = PendingFavouriteStore()
    settings = _settings(tmp_path)
    calls: list[tuple[str, bool]] = []

    class FakeJF:
        def find_media(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
            return ([_movie_raw()], False)

        def set_favorite(self, item_id: str, *, favorite: bool) -> bool:
            calls.append((item_id, favorite))
            return favorite

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "brain.jellyfin_favourite.client_from_settings",
        lambda s: FakeJF(),
    )
    monkeypatch.setattr(
        "brain.jellyfin_favourite._client_from_settings",
        lambda s: FakeJF(),
    )

    r1 = run_turn(
        _FakeChat(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="mark Inception as a favourite"),
        ],
        tools={},
        conversation_id="c1",
        pending_favourites=store,
        settings=settings,
    )
    assert store.is_confirmable("c1")
    assert calls == []
    assert "inception" in r1.content.casefold()

    r2 = run_turn(
        _FakeChat(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="yes"),
        ],
        tools={},
        conversation_id="c1",
        pending_favourites=store,
        settings=settings,
    )
    assert calls == [("m1", True)]
    assert "now a favourite" in r2.content.casefold()
    assert not store.has_favourite("c1")


def test_ambiguous_unfavourite_preserves_verb(tmp_path: Path, monkeypatch: Any) -> None:
    store = PendingFavouriteStore()
    settings = _settings(tmp_path)

    class FakeJF:
        def find_media(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
            return (
                [
                    _movie_raw(item_id="m1", name="Dune", year=2021),
                    _series_raw(item_id="s1", name="Dune", year=2000),
                ],
                False,
            )

        def set_favorite(self, item_id: str, *, favorite: bool) -> bool:
            return favorite

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "brain.jellyfin_favourite.client_from_settings", lambda s: FakeJF()
    )
    monkeypatch.setattr(
        "brain.jellyfin_favourite._client_from_settings", lambda s: FakeJF()
    )

    run_turn(
        _FakeChat(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="unfavourite Dune"),
        ],
        tools={},
        conversation_id="c1",
        pending_favourites=store,
        settings=settings,
    )
    amb = store.get_ambiguous("c1")
    assert amb is not None
    assert amb.favorite is False

    r2 = run_turn(
        _FakeChat(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="1"),
        ],
        tools={},
        conversation_id="c1",
        pending_favourites=store,
        settings=settings,
    )
    pending = store.get_favourite("c1")
    assert pending is not None
    assert pending.favorite is False
    assert "remove favourite" in r2.content.casefold() or "favoriet" in r2.content.casefold()


def test_set_favorite_http_transport() -> None:
    posts: list[Any] = []
    deletes: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(str(request.url))
            assert "UserFavoriteItems/m1" in str(request.url)
            assert "userId=user-1" in str(request.url)
            return httpx.Response(200, json={"IsFavorite": True, "ItemId": "m1"})
        if request.method == "DELETE":
            deletes.append(str(request.url))
            return httpx.Response(200, json={"IsFavorite": False, "ItemId": "m1"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://jellyfin.test/",
        headers={"X-Emby-Token": "key"},
    )
    jf = JellyfinClient(
        "http://jellyfin.test", "key", user_id="user-1", client=http
    )
    assert jf.set_favorite("m1", favorite=True) is True
    assert jf.set_favorite("m1", favorite=False) is False
    assert len(posts) == 1 and len(deletes) == 1


def test_set_favorite_empty_body_followup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method in {"POST", "DELETE"}:
            return httpx.Response(204)
        if "Items/m1" in str(request.url):
            return httpx.Response(
                200,
                json={"Id": "m1", "UserData": {"IsFavorite": True}},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://jellyfin.test/",
        headers={"X-Emby-Token": "key"},
    )
    jf = JellyfinClient(
        "http://jellyfin.test", "key", user_id="user-1", client=http
    )
    assert jf.set_favorite("m1", favorite=True) is True


def test_jellyfin_down_on_confirm(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.set_favorite.side_effect = JellyfinError("jellyfin unavailable (timeout)")
    pending = PendingFavourite(
        item_id="m1",
        title="Inception",
        year=2010,
        kind="movie",
        favorite=True,
        dutch=False,
    )
    ok, reply = execute_favourite_write(_settings(tmp_path), pending, client=jf)
    assert not ok
    assert "could not" in reply.casefold() or "unavailable" in reply.casefold()
    assert "done" not in reply.casefold()


def test_disambiguation_pick_by_kind() -> None:
    amb = PendingFavouriteDisambiguation(
        query="Dune",
        favorite=True,
        candidates=[
            FavouriteCandidate("m1", "movie", "Dune", 2021),
            FavouriteCandidate("s1", "series", "Dune", 2000),
        ],
        dutch=False,
    )
    pick = resolve_favourite_disambiguation_pick("movie", amb)
    assert pick is not None and pick.item_id == "m1"


def test_user_message_requests_favourite() -> None:
    assert user_message_requests_favourite("favorite Inception")
    assert not user_message_requests_favourite("play Inception on the TV")


def test_extract_rejects_edge_intents() -> None:
    assert extract_favourite_request("not mark Inception as a favourite") is None
    assert extract_favourite_request("should I unfavourite Inception?") is None
    assert extract_favourite_request("favourite all movies") is None
    assert extract_favourite_request("favourite every show") is None


def test_unfavourite_confirm_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    store = PendingFavouriteStore()
    settings = _settings(tmp_path)
    calls: list[tuple[str, bool]] = []

    class FakeJF:
        def find_media(self, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
            return ([_movie_raw()], False)

        def set_favorite(self, item_id: str, *, favorite: bool) -> bool:
            calls.append((item_id, favorite))
            return favorite

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "brain.jellyfin_favourite.client_from_settings", lambda s: FakeJF()
    )
    monkeypatch.setattr(
        "brain.jellyfin_favourite._client_from_settings", lambda s: FakeJF()
    )
    run_turn(
        _FakeChat(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="unfavourite Inception"),
        ],
        tools={},
        conversation_id="c1",
        pending_favourites=store,
        settings=settings,
    )
    assert calls == []
    r2 = run_turn(
        _FakeChat(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="yes"),
        ],
        tools={},
        conversation_id="c1",
        pending_favourites=store,
        settings=settings,
    )
    assert calls == [("m1", False)]
    assert "not a favourite" in r2.content.casefold()


def test_unrelated_clears_pending() -> None:
    store = PendingFavouriteStore()
    store.set_favourite(
        "c1",
        PendingFavourite(
            item_id="m1",
            title="Inception",
            year=2010,
            kind="movie",
            favorite=True,
            dutch=False,
        ),
    )
    from brain.jellyfin_favourite import should_keep_pending_favourite

    assert not should_keep_pending_favourite("what's the weather?")
    # Simulate agent clear path
    if not should_keep_pending_favourite("what's the weather?"):
        store.clear("c1")
    assert not store.has_favourite("c1")


def test_jellyfin_down_on_resolve(tmp_path: Path) -> None:
    jf = MagicMock()
    jf.find_media.side_effect = JellyfinError("jellyfin unavailable (timeout)")
    reply, pending, amb = stage_favourite_from_request(
        _settings(tmp_path),
        extract_favourite_request("mark Inception as a favourite"),  # type: ignore[arg-type]
        client=jf,
    )
    assert pending is None and amb is None
    assert "unavailable" in reply.casefold() or "could not" in reply.casefold()
    jf.set_favorite.assert_not_called()
