"""SQLite migration and persistence helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from brain.db import SCHEMA_VERSION, Database, Movie


def test_fresh_db_migrates_to_current(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    assert db.schema_version() == SCHEMA_VERSION
    assert SCHEMA_VERSION == 4
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
    assert db.schema_version() == 4
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
    assert db.schema_version() == 4
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


def test_list_conversations_order_preview_and_empty_filter(tmp_path: Path) -> None:
    from brain.db import CONVERSATION_PREVIEW_CHARS, clamp_conversations_limit

    path = tmp_path / "mimir.db"
    db = Database(path)
    db.ensure_conversation("empty-only")
    db.append_message("older", "user", "first thread")
    db.append_message("older", "assistant", "ok")
    db.append_message("newer", "user", "second thread")
    db.append_message("newer", "assistant", "ok")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            ("2026-01-01T10:00:00Z", "older"),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            ("2026-01-01T11:00:00Z", "newer"),
        )
        conn.commit()

    listed = db.list_conversations(limit=50)
    assert [c.id for c in listed] == ["newer", "older"]
    assert listed[0].preview == "second thread"
    assert listed[0].message_count == 2
    assert listed[1].preview == "first thread"
    assert all(c.created_at and c.updated_at for c in listed)

    long = "x" * (CONVERSATION_PREVIEW_CHARS + 40)
    db.append_message("long", "user", long)
    long_row = next(c for c in db.list_conversations(limit=10) if c.id == "long")
    assert len(long_row.preview) == CONVERSATION_PREVIEW_CHARS
    assert long_row.preview.endswith("…")

    db.append_message("asst-only", "assistant", "orphan reply")
    asst = next(c for c in db.list_conversations(limit=10) if c.id == "asst-only")
    assert asst.preview == "orphan reply"

    assert clamp_conversations_limit(0) == 1
    assert clamp_conversations_limit(-1) == 1
    assert clamp_conversations_limit(999) == 200
    assert clamp_conversations_limit(50) == 50
    assert len(db.list_conversations(limit=1)) == 1
    assert len(db.list_conversations(limit=0)) == 1


def test_list_conversations_tiebreak_by_id(tmp_path: Path) -> None:
    path = tmp_path / "mimir.db"
    db = Database(path)
    db.append_message("aaa", "user", "a")
    db.append_message("zzz", "user", "z")
    stamp = "2026-01-01T12:00:00Z"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE conversations SET created_at = ?, updated_at = ?",
            (stamp, stamp),
        )
        conn.commit()
    listed = db.list_conversations(limit=10)
    assert [c.id for c in listed] == ["zzz", "aaa"]


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


def test_v2_db_migrates_to_v3_last_played_at(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (2)")
        conn.executescript(
            """
            CREATE TABLE movies (
                jellyfin_id TEXT NOT NULL,
                sync_generation INTEGER NOT NULL,
                name TEXT NOT NULL,
                year INTEGER,
                overview TEXT,
                director TEXT,
                cast_json TEXT,
                genres_json TEXT,
                community_rating REAL,
                official_rating TEXT,
                played INTEGER NOT NULL DEFAULT 0,
                playback_position_ticks INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (jellyfin_id, sync_generation)
            );
            CREATE TABLE jellyfin_sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active_generation INTEGER,
                last_started_at TEXT,
                last_finished_at TEXT,
                last_success_at TEXT,
                last_ok INTEGER,
                last_error TEXT,
                items_upserted INTEGER NOT NULL DEFAULT 0,
                in_progress INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO jellyfin_sync_state (
                id, active_generation, last_started_at, last_finished_at,
                last_success_at, last_ok, last_error, items_upserted, in_progress
            ) VALUES (1, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0);
            """
        )
        conn.commit()

    db = Database(path)
    assert db.schema_version() == 4
    db.seed_catalogue_for_tests(
        [
            Movie(
                jellyfin_id="a",
                name="Alpha",
                genres=["sci-fi"],
                played=True,
                last_played_at="2026-08-20T12:00:00Z",
            ),
            Movie(
                jellyfin_id="b",
                name="Beta",
                genres=["drama"],
                played=True,
                last_played_at="2026-07-01T12:00:00Z",
            ),
        ]
    )
    assert db.has_any_last_played_at() is True
    assert db.has_played_without_dates() is False
    recent = db.list_recently_watched(
        14,
        now=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )
    assert [m.name for m in recent] == ["Alpha"]


def test_v3_db_migrates_to_v4_box_sets(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.executescript(
            """
            CREATE TABLE movies (
                jellyfin_id TEXT NOT NULL,
                sync_generation INTEGER NOT NULL,
                name TEXT NOT NULL,
                year INTEGER,
                overview TEXT,
                director TEXT,
                cast_json TEXT,
                genres_json TEXT,
                community_rating REAL,
                official_rating TEXT,
                played INTEGER NOT NULL DEFAULT 0,
                playback_position_ticks INTEGER NOT NULL DEFAULT 0,
                last_played_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (jellyfin_id, sync_generation)
            );
            CREATE TABLE jellyfin_sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active_generation INTEGER,
                last_started_at TEXT,
                last_finished_at TEXT,
                last_success_at TEXT,
                last_ok INTEGER,
                last_error TEXT,
                items_upserted INTEGER NOT NULL DEFAULT 0,
                in_progress INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO jellyfin_sync_state (
                id, active_generation, last_started_at, last_finished_at,
                last_success_at, last_ok, last_error, items_upserted, in_progress
            ) VALUES (1, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0);
            """
        )
        conn.commit()

    from brain.db import BoxSetRef

    db = Database(path)
    assert db.schema_version() == 4
    db.seed_catalogue_for_tests(
        [
            Movie(
                jellyfin_id="m1",
                name="Iron Man",
                year=2008,
                genres=["action"],
                box_sets=(BoxSetRef(id="mcu", name="MCU"),),
            )
        ]
    )
    movie = db.list_active_movies()[0]
    assert movie.box_sets == (BoxSetRef(id="mcu", name="MCU"),)


def test_played_without_dates_detected(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    db.seed_catalogue_for_tests(
        [Movie(jellyfin_id="a", name="Old Sync", played=True)]
    )
    assert db.has_played_without_dates() is True
    assert db.has_any_last_played_at() is False
