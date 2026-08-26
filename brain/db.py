"""SQLite persistence — schema_version migrations, conversations, messages, prefs, Catalogue."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class StoredMessage:
    role: str
    content: str
    created_at: str | None = None


@dataclass(frozen=True)
class Movie:
    """One Catalogue film (write upsert or read row)."""

    jellyfin_id: str
    name: str
    year: int | None = None
    overview: str | None = None
    director: str | None = None
    cast: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()
    community_rating: float | None = None
    official_rating: str | None = None
    played: bool = False
    playback_position_ticks: int = 0


@dataclass(frozen=True)
class SyncState:
    active_generation: int | None
    last_started_at: str | None
    last_finished_at: str | None
    last_success_at: str | None
    last_ok: bool | None
    last_error: str | None
    items_upserted: int
    in_progress: bool


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data if str(x).strip()]


def _row_to_movie(row: sqlite3.Row) -> Movie:
    return Movie(
        jellyfin_id=str(row["jellyfin_id"]),
        name=str(row["name"]),
        year=int(row["year"]) if row["year"] is not None else None,
        overview=str(row["overview"]) if row["overview"] is not None else None,
        director=str(row["director"]) if row["director"] is not None else None,
        cast=tuple(_parse_json_list(row["cast_json"])),
        genres=tuple(_parse_json_list(row["genres_json"])),
        community_rating=(
            float(row["community_rating"]) if row["community_rating"] is not None else None
        ),
        official_rating=(
            str(row["official_rating"]) if row["official_rating"] is not None else None
        ),
        played=bool(row["played"]),
        playback_position_ticks=int(row["playback_position_ticks"] or 0),
    )


class Database:
    """Thin wrapper around ``{data_dir}/mimir.db`` with hand-rolled migrations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                )
                """
            )
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (0,),
                )
                current = 0
            else:
                current = int(row[0])

            while current < SCHEMA_VERSION:
                migrate = _MIGRATIONS.get(current)
                if migrate is None:
                    raise RuntimeError(
                        f"no migration from schema_version {current} (target {SCHEMA_VERSION})"
                    )
                migrate(conn)
                current += 1
                conn.execute("UPDATE schema_version SET version = ?", (current,))
            conn.commit()

    def ping(self) -> bool:
        """Return True if the DB is readable and has a schema_version row."""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
                return row is not None
        except sqlite3.Error:
            return False

    def schema_version(self) -> int | None:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
                return int(row[0]) if row else None
        except sqlite3.Error:
            return None

    def ensure_conversation(self, conversation_id: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (conversation_id, now, now),
            )
            conn.commit()

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (conversation_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, role, content, now),
            )
            conn.commit()

    def list_recent_messages(
        self,
        conversation_id: str,
        *,
        limit: int,
    ) -> list[StoredMessage]:
        """Return up to ``limit`` most recent messages in chronological order."""
        if limit <= 0:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        rows.reverse()
        return [
            StoredMessage(
                role=r["role"],
                content=r["content"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def list_messages(self, conversation_id: str) -> list[StoredMessage]:
        """Return all messages for a Conversation in chronological order.

        Unknown ids yield an empty list (does not create the Conversation).
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [
            StoredMessage(
                role=r["role"],
                content=r["content"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def message_count(self, conversation_id: str | None = None) -> int:
        with self._connect() as conn:
            if conversation_id is None:
                row = conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
            return int(row["n"]) if row else 0

    def get_preferences(self) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM preferences ORDER BY key").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_preference(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM preferences WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else None

    def set_preference(self, key: str, value: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO preferences (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            conn.commit()

    # --- Catalogue / Sync ---

    def get_sync_state(self) -> SyncState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jellyfin_sync_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return SyncState(
                active_generation=None,
                last_started_at=None,
                last_finished_at=None,
                last_success_at=None,
                last_ok=None,
                last_error=None,
                items_upserted=0,
                in_progress=False,
            )
        last_ok_raw = row["last_ok"]
        return SyncState(
            active_generation=(
                int(row["active_generation"])
                if row["active_generation"] is not None
                else None
            ),
            last_started_at=row["last_started_at"],
            last_finished_at=row["last_finished_at"],
            last_success_at=row["last_success_at"],
            last_ok=None if last_ok_raw is None else bool(last_ok_raw),
            last_error=row["last_error"],
            items_upserted=int(row["items_upserted"] or 0),
            in_progress=bool(row["in_progress"]),
        )

    def begin_sync_attempt(self) -> int | None:
        """Mark Sync in progress and allocate a staging generation.

        Returns the new generation number, or None if a Sync is already in progress.
        """
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT active_generation, in_progress FROM jellyfin_sync_state WHERE id = 1"
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO jellyfin_sync_state (
                        id, active_generation, last_started_at, last_finished_at,
                        last_success_at, last_ok, last_error, items_upserted, in_progress
                    ) VALUES (1, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0)
                    """
                )
                active = None
                in_progress = False
            else:
                active = (
                    int(row["active_generation"])
                    if row["active_generation"] is not None
                    else None
                )
                in_progress = bool(row["in_progress"])

            if in_progress:
                return None

            generation = 1 if active is None else active + 1
            conn.execute(
                """
                UPDATE jellyfin_sync_state SET
                    last_started_at = ?,
                    last_error = NULL,
                    items_upserted = 0,
                    in_progress = 1
                WHERE id = 1
                """,
                (now,),
            )
            conn.commit()
            return generation

    def upsert_movies_batch(
        self,
        generation: int,
        movies: Sequence[Movie],
    ) -> int:
        if not movies:
            return 0
        now = _utc_now()
        rows = [
            (
                m.jellyfin_id,
                generation,
                m.name,
                m.year,
                m.overview,
                m.director,
                json.dumps(list(m.cast), separators=(",", ":")),
                json.dumps([g.casefold() for g in m.genres], separators=(",", ":")),
                m.community_rating,
                m.official_rating,
                1 if m.played else 0,
                int(m.playback_position_ticks),
                now,
            )
            for m in movies
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO movies (
                    jellyfin_id, sync_generation, name, year, overview, director,
                    cast_json, genres_json, community_rating, official_rating,
                    played, playback_position_ticks, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jellyfin_id, sync_generation) DO UPDATE SET
                    name = excluded.name,
                    year = excluded.year,
                    overview = excluded.overview,
                    director = excluded.director,
                    cast_json = excluded.cast_json,
                    genres_json = excluded.genres_json,
                    community_rating = excluded.community_rating,
                    official_rating = excluded.official_rating,
                    played = excluded.played,
                    playback_position_ticks = excluded.playback_position_ticks,
                    updated_at = excluded.updated_at
                """,
                rows,
            )
            conn.execute(
                """
                UPDATE jellyfin_sync_state SET
                    items_upserted = items_upserted + ?
                WHERE id = 1
                """,
                (len(rows),),
            )
            conn.commit()
        return len(rows)

    def publish_sync_generation(self, generation: int, *, items_upserted: int) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jellyfin_sync_state SET
                    active_generation = ?,
                    last_finished_at = ?,
                    last_success_at = ?,
                    last_ok = 1,
                    last_error = NULL,
                    items_upserted = ?,
                    in_progress = 0
                WHERE id = 1
                """,
                (generation, now, now, items_upserted),
            )
            conn.execute(
                "DELETE FROM movies WHERE sync_generation != ?",
                (generation,),
            )
            conn.commit()

    def abandon_sync_generation(self, generation: int, *, error: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM movies WHERE sync_generation = ?",
                (generation,),
            )
            conn.execute(
                """
                UPDATE jellyfin_sync_state SET
                    last_finished_at = ?,
                    last_ok = 0,
                    last_error = ?,
                    in_progress = 0
                WHERE id = 1
                """,
                (now, error[:500]),
            )
            conn.commit()

    def clear_sync_in_progress(self, *, error: str | None = None) -> None:
        """Clear a stuck in_progress flag without abandoning a generation."""
        now = _utc_now()
        with self._connect() as conn:
            if error is None:
                conn.execute(
                    "UPDATE jellyfin_sync_state SET in_progress = 0 WHERE id = 1"
                )
            else:
                conn.execute(
                    """
                    UPDATE jellyfin_sync_state SET
                        last_finished_at = ?,
                        last_ok = 0,
                        last_error = ?,
                        in_progress = 0
                    WHERE id = 1
                    """,
                    (now, error[:500]),
                )
            conn.commit()

    def count_active_movies(self) -> int:
        state = self.get_sync_state()
        if state.active_generation is None:
            return 0
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM movies WHERE sync_generation = ?",
                (state.active_generation,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def list_active_movies(self) -> list[Movie]:
        state = self.get_sync_state()
        if state.active_generation is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM movies
                WHERE sync_generation = ?
                ORDER BY name COLLATE NOCASE
                """,
                (state.active_generation,),
            ).fetchall()
        return [_row_to_movie(r) for r in rows]

    def seed_catalogue_for_tests(self, movies: Sequence[Movie]) -> None:
        """Publish a generation with the given movies (suite / unit fixtures)."""
        gen = self.begin_sync_attempt()
        if gen is None:
            self.clear_sync_in_progress()
            gen = self.begin_sync_attempt()
        assert gen is not None
        n = self.upsert_movies_batch(gen, movies)
        self.publish_sync_generation(gen, items_upserted=n)


def _migrate_0_to_1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
            ON messages (conversation_id, id);

        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS movies (
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

        CREATE INDEX IF NOT EXISTS idx_movies_generation_name
            ON movies (sync_generation, name);

        CREATE TABLE IF NOT EXISTS jellyfin_sync_state (
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

        INSERT OR IGNORE INTO jellyfin_sync_state (
            id, active_generation, last_started_at, last_finished_at,
            last_success_at, last_ok, last_error, items_upserted, in_progress
        ) VALUES (1, NULL, NULL, NULL, NULL, NULL, NULL, 0, 0);
        """
    )


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    0: _migrate_0_to_1,
    1: _migrate_1_to_2,
}
