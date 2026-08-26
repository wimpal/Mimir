"""SyncManager — atomic publish, abandon, single-flight."""

from __future__ import annotations

from pathlib import Path

import httpx

from brain.config import Settings
from brain.db import Database, Movie
from brain.jellyfin_client import JellyfinClient
from brain.jellyfin_sync import SyncManager


def _settings(tmp_path: Path, **jellyfin_extra) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    jf = {
        "url": "http://jellyfin.test",
        "api_key": "sekrit",
        "user_id": "user-1",
        "library_ids": ["lib-1"],
        "sync_interval_hours": 24,
        "page_size": 50,
    }
    jf.update(jellyfin_extra)
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        ollama={"url": "http://test", "model": "qwen3:8b"},
        jellyfin=jf,
        runtime={"data_dir": data_dir},
        timeouts={"jellyfin_sync_s": 30, "tool_s": 5, "turn_s": 60, "ollama_s": 30},
    )


def test_failed_sync_keeps_previous_generation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    db.seed_catalogue_for_tests(
        [Movie(jellyfin_id="old", name="Old Film", genres=["drama"])]
    )
    assert db.count_active_movies() == 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    mgr = SyncManager(settings, db)
    mgr._client = JellyfinClient(
        settings.jellyfin.url,
        settings.jellyfin.api_key or "",
        user_id=settings.jellyfin.user_id,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://jellyfin.test/",
        ),
    )
    result = mgr.run_sync(force=True)
    assert result.ok is False
    assert db.count_active_movies() == 1
    assert db.list_active_movies()[0].name == "Old Film"
    mgr.close()


def test_successful_sync_replaces_catalogue(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    db.seed_catalogue_for_tests(
        [Movie(jellyfin_id="gone", name="Removed", genres=["drama"])]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "Items": [
                    {
                        "Id": "new",
                        "Name": "Fresh",
                        "Genres": ["Comedy"],
                        "UserData": {"Played": False, "PlaybackPositionTicks": 0},
                    }
                ],
                "TotalRecordCount": 1,
            },
        )

    mgr = SyncManager(settings, db)
    mgr._client = JellyfinClient(
        settings.jellyfin.url,
        settings.jellyfin.api_key or "",
        user_id=settings.jellyfin.user_id,
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://jellyfin.test/",
        ),
    )
    result = mgr.run_sync(force=True)
    assert result.ok is True
    names = [m.name for m in db.list_active_movies()]
    assert names == ["Fresh"]
    mgr.close()


def test_single_flight_busy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(settings.runtime.data_dir / "mimir.db")
    assert db.begin_sync_attempt() == 1
    mgr = SyncManager(settings, db)
    # DB already in_progress — begin inside run_sync returns busy
    result = mgr.run_sync(force=True)
    assert result.busy is True
    assert result.ok is False
    db.clear_sync_in_progress()
    mgr.close()


def test_not_configured(tmp_path: Path) -> None:
    settings = _settings(tmp_path, api_key=None, library_ids=[])
    # api_key None — rebuild settings without key
    settings = Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        jellyfin={"url": "http://x", "user_id": "u", "library_ids": []},
        runtime={"data_dir": tmp_path / "d"},
    )
    (tmp_path / "d").mkdir()
    db = Database(settings.runtime.data_dir / "mimir.db")
    mgr = SyncManager(settings, db)
    result = mgr.run_sync(force=True)
    assert result.configured is False
    assert result.ok is False
