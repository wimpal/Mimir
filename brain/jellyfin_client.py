"""Jellyfin REST client — paginated movie library + BoxSet membership for Catalogue Sync."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx

from brain.db import BoxSetRef, Movie, parse_jellyfin_datetime

logger = logging.getLogger("mimir.jellyfin")

_FIELDS = "Overview,Genres,People,CommunityRating,OfficialRating,ProductionYear"
_SERIES_FIELDS = "Overview,Genres,ProductionYear"
_EPISODE_FIELDS = "Overview,ProductionYear"
# Live media lookup (T-128): RunTimeTicks for films; series ticks often missing/episode-length.
_MEDIA_LOOKUP_FIELDS = "Overview,Genres,ProductionYear,RunTimeTicks,UserData"
_MEDIA_LOOKUP_MAX_PAGES = 3
# Media watch stats (T-131): played films, last-played date per film.
_STATS_FIELDS = "Overview,Genres,ProductionYear,RunTimeTicks,UserData"
_STATS_MAX_PAGES = 20


class JellyfinError(RuntimeError):
    """Upstream Jellyfin request failed or timed out."""


class JellyfinClient:
    """Thin httpx client. Auth: API key via ``X-Emby-Token``."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        user_id: str,
        page_size: int = 100,
        request_timeout_s: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self.user_id = user_id
        self.page_size = max(1, page_size)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            headers={
                "X-Emby-Token": api_key,
                "Accept": "application/json",
                # Identify API client so Sessions matching can ignore it.
                "X-Emby-Authorization": (
                    'MediaBrowser Client="MimirBrain", Device="Mimir", '
                    'DeviceId="mimir-brain", Version="1.0"'
                ),
            },
            timeout=request_timeout_s,
        )
        self._warned_missing_userdata = False

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def iter_library_movies(
        self,
        library_id: str,
        *,
        deadline_monotonic: float | None = None,
    ) -> Iterator[Movie]:
        """Yield movies from one library, paginated."""
        yield from self._iter_movies(
            parent_id=library_id,
            deadline_monotonic=deadline_monotonic,
        )

    def iter_box_sets(
        self,
        *,
        deadline_monotonic: float | None = None,
    ) -> Iterator[BoxSetRef]:
        """Yield Box sets visible to the configured user."""
        start = 0
        while True:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise JellyfinError("sync deadline exceeded")

            params: dict[str, Any] = {
                "IncludeItemTypes": "BoxSet",
                "Recursive": "true",
                "StartIndex": start,
                "Limit": self.page_size,
            }
            payload = self._get_items(params)
            items = payload.get("Items") or []
            if not isinstance(items, list):
                raise JellyfinError("jellyfin unavailable (bad Items)")

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item_id = raw.get("Id")
                name = raw.get("Name")
                if not item_id or not name:
                    continue
                yield BoxSetRef(id=str(item_id), name=str(name))

            total = payload.get("TotalRecordCount")
            got = len(items)
            start += got
            if got == 0:
                break
            if isinstance(total, int) and start >= total:
                break
            if got < self.page_size:
                break

    def iter_box_set_movie_ids(
        self,
        box_set_id: str,
        *,
        deadline_monotonic: float | None = None,
    ) -> Iterator[str]:
        """Yield movie Jellyfin ids that are members of a Box set."""
        start = 0
        while True:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise JellyfinError("sync deadline exceeded")

            params: dict[str, Any] = {
                "ParentId": box_set_id,
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "StartIndex": start,
                "Limit": self.page_size,
            }
            payload = self._get_items(params)
            items = payload.get("Items") or []
            if not isinstance(items, list):
                raise JellyfinError("jellyfin unavailable (bad Items)")

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item_id = raw.get("Id")
                if item_id:
                    yield str(item_id)

            total = payload.get("TotalRecordCount")
            got = len(items)
            start += got
            if got == 0:
                break
            if isinstance(total, int) and start >= total:
                break
            if got < self.page_size:
                break

    def build_box_set_membership(
        self,
        catalogue_ids: set[str],
        *,
        deadline_monotonic: float | None = None,
    ) -> dict[str, tuple[BoxSetRef, ...]]:
        """Map Catalogue movie id → Box sets (only ids present in catalogue_ids)."""
        membership: dict[str, list[BoxSetRef]] = {}
        for box in self.iter_box_sets(deadline_monotonic=deadline_monotonic):
            for movie_id in self.iter_box_set_movie_ids(
                box.id,
                deadline_monotonic=deadline_monotonic,
            ):
                if movie_id not in catalogue_ids:
                    continue
                bucket = membership.setdefault(movie_id, [])
                if any(b.id == box.id for b in bucket):
                    continue
                bucket.append(box)
        return {mid: tuple(refs) for mid, refs in membership.items()}

    def _iter_movies(
        self,
        *,
        parent_id: str,
        deadline_monotonic: float | None = None,
    ) -> Iterator[Movie]:
        start = 0
        while True:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise JellyfinError("sync deadline exceeded")

            params: dict[str, Any] = {
                "ParentId": parent_id,
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "EnableUserData": "true",
                "Fields": _FIELDS,
                "StartIndex": start,
                "Limit": self.page_size,
            }
            payload = self._get_items(params)
            items = payload.get("Items") or []
            if not isinstance(items, list):
                raise JellyfinError("jellyfin unavailable (bad Items)")

            for raw in items:
                if isinstance(raw, dict):
                    movie = normalize_item(raw)
                    if movie is not None:
                        if (
                            not self._warned_missing_userdata
                            and "UserData" not in raw
                        ):
                            logger.warning(
                                "Jellyfin Items missing UserData; treating as unwatched"
                            )
                            self._warned_missing_userdata = True
                        yield movie

            total = payload.get("TotalRecordCount")
            got = len(items)
            start += got
            if got == 0:
                break
            if isinstance(total, int) and start >= total:
                break
            if got < self.page_size:
                break

    def list_sessions(self) -> list[dict[str, Any]]:
        """Active Jellyfin sessions (for remote PlayNow targeting)."""
        try:
            resp = self._client.get("Sessions")
        except httpx.TimeoutException as exc:
            raise JellyfinError("jellyfin unavailable (timeout)") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("jellyfin unavailable (network)") from exc
        if resp.status_code >= 400:
            raise JellyfinError(f"jellyfin unavailable (HTTP {resp.status_code})")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise JellyfinError("jellyfin unavailable (bad JSON)") from exc
        if not isinstance(payload, list):
            raise JellyfinError("jellyfin unavailable (bad JSON)")
        return [s for s in payload if isinstance(s, dict)]

    def play_items(
        self,
        session_id: str,
        item_ids: list[str],
        *,
        play_command: str = "PlayNow",
    ) -> None:
        """Instruct a session to play items (POST /Sessions/{id}/Playing)."""
        sid = (session_id or "").strip()
        ids = [i.strip() for i in item_ids if (i or "").strip()]
        if not sid:
            raise JellyfinError("jellyfin play missing session id")
        if not ids:
            raise JellyfinError("jellyfin play missing item ids")
        try:
            resp = self._client.post(
                f"Sessions/{sid}/Playing",
                params={
                    "playCommand": play_command,
                    "itemIds": ",".join(ids),
                },
            )
        except httpx.TimeoutException as exc:
            raise JellyfinError("jellyfin unavailable (timeout)") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("jellyfin unavailable (network)") from exc
        if resp.status_code >= 400:
            raise JellyfinError(f"jellyfin unavailable (HTTP {resp.status_code})")

    def find_series(self, title: str) -> list[dict[str, Any]]:
        """Series items matching ``title`` in every library the user can see.

        Deliberately not scoped to ``library_ids`` — that setting is the movie
        sync scope and may point at a Movies-only library, while a live title
        resolve must see the TV library too (live-proven 2026-10-01).
        """
        needle = (title or "").strip()
        if not needle:
            return []
        params: dict[str, Any] = {
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "SearchTerm": needle,
            "Fields": _SERIES_FIELDS,
            "EnableUserData": "true",
        }
        payload = self._get_items(params)
        items = payload.get("Items") or []
        if not isinstance(items, list):
            raise JellyfinError("jellyfin unavailable (bad Items)")
        return [raw for raw in items if isinstance(raw, dict)]

    def find_media(
        self,
        *,
        title: str | None = None,
        include_series: bool = True,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Live Movie/Series items across all libraries the user can see.

        Never passes ``ParentId`` / ``library_ids`` — those are the movie-sync
        scope and miss the TV library (T-127 / T-128).

        Returns ``(items, truncated)``. Exact title matches (case-insensitive
        ``Name``) are sorted ahead of substring hits after fetch.
        """
        needle = (title or "").strip() or None
        types = "Movie,Series" if include_series else "Movie"
        collected: list[dict[str, Any]] = []
        start = 0
        total: int | None = None
        for _ in range(_MEDIA_LOOKUP_MAX_PAGES):
            params: dict[str, Any] = {
                "IncludeItemTypes": types,
                "Recursive": "true",
                "EnableUserData": "true",
                "Fields": _MEDIA_LOOKUP_FIELDS,
                "StartIndex": start,
                "Limit": self.page_size,
            }
            if needle is not None:
                params["SearchTerm"] = needle
            payload = self._get_items(params)
            items = payload.get("Items") or []
            if not isinstance(items, list):
                raise JellyfinError("jellyfin unavailable (bad Items)")
            trc = payload.get("TotalRecordCount")
            if isinstance(trc, int):
                total = trc
            page_items = [raw for raw in items if isinstance(raw, dict)]
            collected.extend(page_items)
            got = len(page_items)
            start += got
            if got < self.page_size:
                break
            if total is not None and start >= total:
                break

        truncated = total is not None and len(collected) < total
        if not truncated and len(collected) >= self.page_size * _MEDIA_LOOKUP_MAX_PAGES:
            # Full page cap without a smaller TotalRecordCount — assume more may exist.
            truncated = total is None or total > len(collected)

        if needle is not None:
            lower = needle.casefold()

            def _exact_first(raw: dict[str, Any]) -> tuple[int, str]:
                name = str(raw.get("Name") or "")
                exact = 0 if name.casefold() == lower else 1
                return (exact, name.casefold())

            collected.sort(key=_exact_first)

        return collected, truncated

    def list_episodes(self, series_id: str) -> list[dict[str, Any]]:
        """Episodes of one series (raw items, user data included)."""
        sid = (series_id or "").strip()
        if not sid:
            return []
        params: dict[str, Any] = {
            "ParentId": sid,
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "EnableUserData": "true",
            "Fields": _EPISODE_FIELDS,
        }
        payload = self._get_items(params)
        items = payload.get("Items") or []
        if not isinstance(items, list):
            raise JellyfinError("jellyfin unavailable (bad Items)")
        return [raw for raw in items if isinstance(raw, dict)]

    def list_resume_movies(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Part-watched films for the configured user (``Items/Resume``).

        Live-proven 2026-10-06: ``GET Users/{userId}/Items/Resume`` with
        ``IncludeItemTypes=Movie`` returns 200. Never passes ``ParentId`` /
        ``library_ids``.
        """
        lim = max(1, int(limit))
        payload = self._get_path(
            f"Users/{self.user_id}/Items/Resume",
            {
                "IncludeItemTypes": "Movie",
                "EnableUserData": "true",
                "Fields": _MEDIA_LOOKUP_FIELDS,
                "Limit": lim,
            },
        )
        items = payload.get("Items") or []
        if not isinstance(items, list):
            raise JellyfinError("jellyfin unavailable (bad Items)")
        out: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("Type") or "") != "Movie":
                continue
            ud = raw.get("UserData")
            ticks = 0
            if isinstance(ud, dict):
                try:
                    ticks = int(ud.get("PlaybackPositionTicks") or 0)
                except (TypeError, ValueError):
                    ticks = 0
            if ticks <= 0:
                continue
            out.append(raw)
        return out

    def list_next_up(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Global NextUp episodes (no ``SeriesId``) for continue-watching.

        Live-proven 2026-10-06: ``GET Shows/NextUp?UserId=…`` returns Episode
        items with ``SeriesId`` / ``SeriesName`` / season+episode numbers.
        """
        lim = max(1, int(limit))
        payload = self._get_path(
            "Shows/NextUp",
            {
                "UserId": self.user_id,
                "Limit": lim,
                "Fields": _EPISODE_FIELDS,
                "EnableUserData": "true",
            },
        )
        items = payload.get("Items") or []
        if not isinstance(items, list):
            raise JellyfinError("jellyfin unavailable (bad Items)")
        return [raw for raw in items if isinstance(raw, dict)]

    def list_played_movies(
        self,
        *,
        max_pages: int = _STATS_MAX_PAGES,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Played films for the configured user, newest play first.

        Live-proven 2026-10-06: ``GET Users/{userId}/Items`` with
        ``Filters=IsPlayed`` + ``IncludeItemTypes=Movie`` returns one row per
        film (a rewatched film appears once, with its most recent
        ``LastPlayedDate``). Never passes ``ParentId`` / ``library_ids``.
        Sorted ``DatePlayed`` desc so callers can early-stop once the window
        start passes. Returns ``(items, truncated)``.
        """
        collected: list[dict[str, Any]] = []
        start = 0
        total: int | None = None
        for _ in range(max(1, int(max_pages))):
            params: dict[str, Any] = {
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "Filters": "IsPlayed",
                "EnableUserData": "true",
                "Fields": _STATS_FIELDS,
                "SortBy": "DatePlayed",
                "SortOrder": "Descending",
                "StartIndex": start,
                "Limit": self.page_size,
            }
            payload = self._get_items(params)
            items = payload.get("Items") or []
            if not isinstance(items, list):
                raise JellyfinError("jellyfin unavailable (bad Items)")
            trc = payload.get("TotalRecordCount")
            if isinstance(trc, int):
                total = trc
            page_items = [raw for raw in items if isinstance(raw, dict)]
            collected.extend(page_items)
            got = len(page_items)
            start += got
            if got == 0:
                break
            if total is not None and start >= total:
                break
            if got < self.page_size:
                break

        truncated = total is not None and len(collected) < total
        if not truncated and len(collected) >= self.page_size * max(1, int(max_pages)):
            truncated = total is None or total > len(collected)
        return collected, truncated

    def next_up_episode(self, series_id: str) -> dict[str, Any] | None:
        """Jellyfin continue-watching episode for one series, or None."""
        sid = (series_id or "").strip()
        if not sid:
            return None
        payload = self._get_path(
            "Shows/NextUp",
            {
                "UserId": self.user_id,
                "SeriesId": sid,
                "Limit": 1,
                "Fields": _EPISODE_FIELDS,
                "EnableUserData": "true",
            },
        )
        items = payload.get("Items") or []
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        return first if isinstance(first, dict) else None

    def _get_path(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise JellyfinError("jellyfin unavailable (timeout)") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("jellyfin unavailable (network)") from exc
        if resp.status_code >= 400:
            raise JellyfinError(f"jellyfin unavailable (HTTP {resp.status_code})")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise JellyfinError("jellyfin unavailable (bad JSON)") from exc
        if not isinstance(payload, dict):
            raise JellyfinError("jellyfin unavailable (bad JSON)")
        return payload

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        """One item by id; None when the server reports 404."""
        iid = (item_id or "").strip()
        if not iid:
            return None
        try:
            resp = self._client.get(f"Users/{self.user_id}/Items/{iid}")
        except httpx.TimeoutException as exc:
            raise JellyfinError("jellyfin unavailable (timeout)") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("jellyfin unavailable (network)") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise JellyfinError(f"jellyfin unavailable (HTTP {resp.status_code})")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise JellyfinError("jellyfin unavailable (bad JSON)") from exc
        if not isinstance(payload, dict):
            raise JellyfinError("jellyfin unavailable (bad JSON)")
        return payload

    def set_favorite(self, item_id: str, *, favorite: bool) -> bool:
        """Mark or unmark favourite. Returns verified ``IsFavorite``.

        Live-proven 2026-10-06: ``POST``/``DELETE``
        ``UserFavoriteItems/{id}?userId=…`` returns 200 with top-level
        ``IsFavorite``. Empty 2xx bodies fall back to ``get_item``.
        """
        iid = (item_id or "").strip()
        if not iid:
            raise JellyfinError("jellyfin unavailable (missing item id)")
        path = f"UserFavoriteItems/{iid}"
        params = {"userId": self.user_id}
        try:
            if favorite:
                resp = self._client.post(path, params=params)
            else:
                resp = self._client.delete(path, params=params)
        except httpx.TimeoutException as exc:
            raise JellyfinError("jellyfin unavailable (timeout)") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("jellyfin unavailable (network)") from exc
        if resp.status_code >= 400:
            raise JellyfinError(f"jellyfin unavailable (HTTP {resp.status_code})")

        parsed = _favorite_flag_from_payload(resp)
        if parsed is not None:
            return parsed

        item = self.get_item(iid)
        if item is None:
            raise JellyfinError(
                "jellyfin unavailable (item missing after favourite write)"
            )
        flag = _favorite_flag_from_item(item)
        if flag is None:
            raise JellyfinError("jellyfin unavailable (favourite state unverified)")
        return flag

    def _get_items(self, params: dict[str, Any]) -> dict[str, Any]:
        path = f"Users/{self.user_id}/Items"
        try:
            resp = self._client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise JellyfinError("jellyfin unavailable (timeout)") from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("jellyfin unavailable (network)") from exc

        if resp.status_code >= 400:
            raise JellyfinError(f"jellyfin unavailable (HTTP {resp.status_code})")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise JellyfinError("jellyfin unavailable (bad JSON)") from exc

        if not isinstance(payload, dict):
            raise JellyfinError("jellyfin unavailable (bad JSON)")
        return payload


def _favorite_flag_from_payload(resp: httpx.Response) -> bool | None:
    if not resp.content:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if "IsFavorite" in payload:
        return bool(payload.get("IsFavorite"))
    ud = payload.get("UserData")
    if isinstance(ud, dict) and "IsFavorite" in ud:
        return bool(ud.get("IsFavorite"))
    return None


def _favorite_flag_from_item(item: dict[str, Any]) -> bool | None:
    if "IsFavorite" in item:
        return bool(item.get("IsFavorite"))
    ud = item.get("UserData")
    if isinstance(ud, dict) and "IsFavorite" in ud:
        return bool(ud.get("IsFavorite"))
    return None


def normalize_item(raw: dict[str, Any]) -> Movie | None:
    """Map a Jellyfin Item to Movie; return None if not usable."""
    item_id = raw.get("Id")
    name = raw.get("Name")
    if not item_id or not name:
        return None

    year = raw.get("ProductionYear")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None

    overview = raw.get("Overview")
    if overview is not None:
        overview = str(overview)

    genres_raw = raw.get("Genres") or []
    genres = [str(g).strip() for g in genres_raw if str(g).strip()]

    director, cast = _people(raw.get("People") or [])

    rating = raw.get("CommunityRating")
    if rating is not None:
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            rating = None

    official = raw.get("OfficialRating")
    if official is not None:
        official = str(official)

    user_data = raw.get("UserData") or {}
    played = bool(user_data.get("Played")) if isinstance(user_data, dict) else False
    ticks = 0
    last_played_at: str | None = None
    if isinstance(user_data, dict):
        try:
            ticks = int(user_data.get("PlaybackPositionTicks") or 0)
        except (TypeError, ValueError):
            ticks = 0
        last_played_at = parse_jellyfin_datetime(user_data.get("LastPlayedDate"))

    return Movie(
        jellyfin_id=str(item_id),
        name=str(name),
        year=year,
        overview=overview,
        director=director,
        cast=tuple(cast),
        genres=tuple(genres),
        community_rating=rating,
        official_rating=official,
        played=played,
        playback_position_ticks=ticks,
        last_played_at=last_played_at,
    )


def apply_box_sets(
    movies: dict[str, Movie],
    membership: dict[str, tuple[BoxSetRef, ...]],
) -> list[Movie]:
    """Return movies with Box set refs attached (Catalogue ids only)."""
    out: list[Movie] = []
    for mid, movie in movies.items():
        refs = membership.get(mid, ())
        out.append(replace(movie, box_sets=refs) if refs else movie)
    return out


def _people(people: list[Any]) -> tuple[str | None, list[str]]:
    director: str | None = None
    cast: list[str] = []
    for person in people:
        if not isinstance(person, dict):
            continue
        pname = str(person.get("Name") or "").strip()
        if not pname:
            continue
        role = str(person.get("Type") or "").casefold()
        if role == "director" and director is None:
            director = pname
        elif role == "actor" and len(cast) < 5:
            cast.append(pname)
    return director, cast
