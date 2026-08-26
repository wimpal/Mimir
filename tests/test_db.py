"""SQLite migration and persistence helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from brain.db import SCHEMA_VERSION, Database, Movie


def test_fresh_db_migrates_to_current(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    assert db.schema_version() == SCHEMA_VERSION
    assert SCHEMA_VERSION == 2
    db.ensure_conversation("probe")
    db.append_message("probe", "user", "x")
    db.set_preference("tone", "dry")
    assert db.message_count("probe") == 1
    assert db.get_preference("tone") == "dry"
    assert db.count_active_movies() == 0


def test_v0_db_migrates_on_open(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()

    db = Database(path)
    assert db.schema_version() == 2
    db.ensure_conversation("c1")
    db.append_message("c1", "user", "hi")
    assert db.message_count("c1") == 1
    assert db.get_sync_state().active_generation is None


def test_v1_db_migrates_to_v2(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.execute(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

    db = Database(path)
    assert db.schema_version() == 2
    state = db.get_sync_state()
    assert state.active_generation is None
    assert state.in_progress is False


def test_messages_and_prefs_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    db.ensure_conversation("abc")
    db.append_message("abc", "user", "hello")
    db.append_message("abc", "assistant", "hi there")
    recent = db.list_recent_messages("abc", limit=10)
    assert [(m.role, m.content) for m in recent] == [
        ("user", "hello"),
        ("assistant", "hi there"),
    ]
    assert db.list_messages("abc") == recent
    assert db.list_messages("missing") == []
    db.set_preference("tone", "dry")
    assert db.get_preference("tone") == "dry"
    assert db.get_preferences() == {"tone": "dry"}


def test_catalogue_publish_and_abandon(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    gen1 = db.begin_sync_attempt()
    assert gen1 == 1
    db.upsert_movies_batch(
        gen1,
        [
            Movie(jellyfin_id="a", name="Alpha", genres=["sci-fi"], played=False),
            Movie(jellyfin_id="b", name="Beta", genres=["drama"], played=True),
        ],
    )
    db.publish_sync_generation(gen1, items_upserted=2)
    assert db.count_active_movies() == 2
    assert db.get_sync_state().active_generation == 1

    gen2 = db.begin_sync_attempt()
    assert gen2 == 2
    db.upsert_movies_batch(
        gen2,
        [Movie(jellyfin_id="c", name="Gamma", genres=["comedy"])],
    )
    db.abandon_sync_generation(gen2, error="upstream down")
    assert db.count_active_movies() == 2
    assert db.get_sync_state().active_generation == 1
    assert db.get_sync_state().last_ok is False
    assert "upstream" in (db.get_sync_state().last_error or "")

    gen3 = db.begin_sync_attempt()
    assert gen3 == 2
    db.upsert_movies_batch(
        gen3,
        [Movie(jellyfin_id="c", name="Gamma", genres=["comedy"])],
    )
    db.publish_sync_generation(gen3, items_upserted=1)
    assert db.count_active_movies() == 1
    assert db.list_active_movies()[0].name == "Gamma"


def test_busy_begin_returns_none(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    assert db.begin_sync_attempt() == 1
    assert db.begin_sync_attempt() is None
    db.clear_sync_in_progress(error="cancelled")
    assert db.begin_sync_attempt() == 1
