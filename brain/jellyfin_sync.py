"""Catalogue Sync — single-flight Jellyfin → SQLite with atomic generation publish."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from brain.config import Settings, jellyfin_sync_configured
from brain.db import Database
from brain.jellyfin_client import JellyfinClient, JellyfinError, apply_box_sets

logger = logging.getLogger("mimir.jellyfin")

UPSERT_BATCH_SIZE = 50
HTTP_REQUEST_TIMEOUT_CAP_S = 60.0


@dataclass(frozen=True)
class SyncResult:
    ok: bool
    busy: bool
    configured: bool
    message: str
    state: dict[str, Any]


def catalogue_status_dict(
    db: Database,
    settings: Settings,
    *,
    configured: bool | None = None,
) -> dict[str, Any]:
    """Shared Catalogue Sync status for health, Sync API, and recommend_movies."""
    state = db.get_sync_state()
    if configured is None:
        configured = jellyfin_sync_configured(settings)
    interval_h = settings.jellyfin.sync_interval_hours
    stale = False
    if state.last_success_at is None:
        stale = state.active_generation is not None
    elif interval_h > 0 and state.last_success_at:
        try:
            success = datetime.strptime(
                state.last_success_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=UTC)
            age_h = (datetime.now(UTC) - success).total_seconds() / 3600.0
            stale = age_h >= interval_h
        except ValueError:
            stale = True
    if state.last_ok is False:
        stale = True

    return {
        "configured": configured,
        "in_progress": state.in_progress,
        "active_generation": state.active_generation,
        "movie_count": db.count_active_movies(),
        "last_success_at": state.last_success_at,
        "last_finished_at": state.last_finished_at,
        "last_ok": state.last_ok,
        "last_error": state.last_error,
        "items_upserted": state.items_upserted,
        "stale": stale,
    }


class SyncManager:
    """Owns the Jellyfin client and runs Catalogue Sync with a process-wide lock."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self._lock = threading.Lock()
        self._client: JellyfinClient | None = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _ensure_client(self) -> JellyfinClient:
        if self._client is None:
            jf = self.settings.jellyfin
            assert jf.api_key is not None
            self._client = JellyfinClient(
                jf.url,
                jf.api_key,
                user_id=jf.user_id,
                page_size=jf.page_size,
                request_timeout_s=min(
                    HTTP_REQUEST_TIMEOUT_CAP_S,
                    self.settings.timeouts.jellyfin_sync_s,
                ),
            )
        return self._client

    def sync_status_dict(self) -> dict[str, Any]:
        return catalogue_status_dict(self.db, self.settings)

    def needs_sync(self) -> bool:
        if not jellyfin_sync_configured(self.settings):
            return False
        status = self.sync_status_dict()
        if status["movie_count"] == 0:
            return True
        return bool(status["stale"])

    def run_sync(self, *, force: bool = False) -> SyncResult:
        if not jellyfin_sync_configured(self.settings):
            return SyncResult(
                ok=False,
                busy=False,
                configured=False,
                message="jellyfin sync not configured "
                "(need url, api_key, user_id, library_ids)",
                state=self.sync_status_dict(),
            )

        if not self._lock.acquire(blocking=False):
            return SyncResult(
                ok=False,
                busy=True,
                configured=True,
                message="sync already in progress",
                state=self.sync_status_dict(),
            )

        try:
            return self._run_sync_locked(force=force)
        finally:
            self._lock.release()

    def _run_sync_locked(self, *, force: bool) -> SyncResult:
        if not force and not self.needs_sync():
            return SyncResult(
                ok=True,
                busy=False,
                configured=True,
                message="catalogue already fresh",
                state=self.sync_status_dict(),
            )

        generation = self.db.begin_sync_attempt()
        if generation is None:
            return SyncResult(
                ok=False,
                busy=True,
                configured=True,
                message="sync already in progress",
                state=self.sync_status_dict(),
            )

        deadline = time.monotonic() + self.settings.timeouts.jellyfin_sync_s
        total = 0
        try:
            client = self._ensure_client()
            by_id: dict[str, Any] = {}
            for library_id in self.settings.jellyfin.library_ids:
                for movie in client.iter_library_movies(
                    library_id,
                    deadline_monotonic=deadline,
                ):
                    by_id[movie.jellyfin_id] = movie
                    if time.monotonic() >= deadline:
                        raise JellyfinError("sync deadline exceeded")

            membership: dict = {}
            try:
                membership = client.build_box_set_membership(
                    set(by_id),
                    deadline_monotonic=deadline,
                )
            except JellyfinError as exc:
                logger.warning("jellyfin box set sync skipped: %s", exc)
            except Exception:  # noqa: BLE001 — soft-fail Box sets; keep movies
                logger.exception("jellyfin box set sync crashed; continuing without")

            enriched = apply_box_sets(by_id, membership)
            batch: list = []
            for movie in enriched:
                batch.append(movie)
                if len(batch) >= UPSERT_BATCH_SIZE:
                    total += self.db.upsert_movies_batch(generation, batch)
                    batch = []
            if batch:
                total += self.db.upsert_movies_batch(generation, batch)

            self.db.publish_sync_generation(generation, items_upserted=total)
            logger.info(
                "jellyfin sync ok generation=%s items=%s box_set_links=%s",
                generation,
                total,
                sum(1 for m in enriched if m.box_sets),
            )
            return SyncResult(
                ok=True,
                busy=False,
                configured=True,
                message=f"synced {total} movies",
                state=self.sync_status_dict(),
            )
        except JellyfinError as exc:
            msg = str(exc)
            self.db.abandon_sync_generation(generation, error=msg)
            logger.warning("jellyfin sync failed: %s", msg)
            return SyncResult(
                ok=False,
                busy=False,
                configured=True,
                message=msg,
                state=self.sync_status_dict(),
            )
        except Exception as exc:  # noqa: BLE001 — never leave in_progress stuck
            msg = f"jellyfin unavailable ({type(exc).__name__})"
            self.db.abandon_sync_generation(generation, error=msg)
            logger.exception("jellyfin sync crashed")
            return SyncResult(
                ok=False,
                busy=False,
                configured=True,
                message=msg,
                state=self.sync_status_dict(),
            )
