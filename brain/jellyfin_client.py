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
            headers={"X-Emby-Token": api_key, "Accept": "application/json"},
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
