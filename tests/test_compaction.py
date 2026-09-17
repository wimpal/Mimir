"""T-057 conversation history compaction."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from brain.compaction import (
    RECENT_USER_MARK,
    SUMMARY_SYSTEM_MARK,
    assemble_persist_messages,
    should_bypass_compaction,
)
from brain.config import Settings
from brain.db import SCHEMA_VERSION, Database
from brain.ollama import ChatMessage, ChatResponse
from brain.recipe_import import PendingRecipeStore


class ScriptedOllama:
    def __init__(self, responses: list[ChatMessage | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        think: bool = False,
        stream: bool = False,
    ) -> ChatResponse:
        self.calls.append({"messages": list(messages), "tools": tools, "think": think})
        if not self._responses:
            raise AssertionError("no scripted responses left")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return ChatResponse(message=nxt)


def _settings(tmp_path: Path, **memory: Any) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    mem = {
        "history_pairs": 2,
        "compaction_enabled": True,
        "compaction_batch_pairs": 1,
        "compaction_max_summary_chars": 500,
        "compaction_timeout_s": 5.0,
        "compaction_min_verbatim_pairs": 1,
    }
    mem.update(memory)
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        ollama={"url": "http://test", "model": "qwen3:8b", "num_ctx": 2048},
        runtime={"data_dir": data_dir, "log_level": "WARNING"},
        agent={"max_iterations": 3},
        timeouts={"ollama_s": 30, "tool_s": 5, "turn_s": 60},
        memory=mem,
    )


def _seed_pairs(db: Database, cid: str, n_pairs: int, *, prefix: str = "t") -> None:
    for i in range(n_pairs):
        db.append_message(cid, "user", f"{prefix}-user-{i}")
        db.append_message(cid, "assistant", f"{prefix}-assistant-{i}")


def test_schema_version_is_5(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    assert db.schema_version() == SCHEMA_VERSION
    assert SCHEMA_VERSION == 5


def test_v4_migrates_to_v5_with_compactions_table(tmp_path: Path) -> None:
    path = tmp_path / "v4.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (4)")
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
        conn.commit()

    db = Database(path)
    assert db.schema_version() == 5
    db.ensure_conversation("c1")
    assert db.upsert_compaction(
        "c1",
        summary_text="hello",
        covered_through_message_id=1,
    )
    row = db.get_compaction("c1")
    assert row is not None
    assert row.summary_text == "hello"
    assert row.covered_through_message_id == 1


def test_over_threshold_injects_summary_and_recent_tail(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(tmp_path / "mimir.db")
    cid = "c-compact"
    db.ensure_conversation(cid)
    # 3 pairs; history_pairs=2 → 1 aged pair; batch_pairs=1 → summarize
    _seed_pairs(db, cid, 3, prefix="old")

    client = ScriptedOllama(
        [ChatMessage(role="assistant", content="User talked about old-user-0.")]
    )
    messages = assemble_persist_messages(
        db=db,
        client=client,
        settings=settings,
        conversation_id=cid,
        system="You are Mimir.",
        user_text="what did we say earlier?",
    )

    assert len(client.calls) == 1
    assert client.calls[0]["tools"] is None
    assert messages[0].role == "system"
    assert SUMMARY_SYSTEM_MARK in messages[0].content
    assert "old-user-0" in messages[0].content
    # Aged-out content should appear in the system summary, not as a raw history turn
    # after successful fold (covered through the aged pair).
    history_roles = [
        m for m in messages if m.role in ("user", "assistant")
    ]
    history_contents = [m.content for m in history_roles]
    assert "old-user-0" not in history_contents
    assert "old-user-1" in history_contents or "old-user-2" in history_contents
    assert history_contents[-1] == "what did we say earlier?"
    # Verbatim-window user lines are pinned into system for recall reliability.
    assert RECENT_USER_MARK in messages[0].content

    row = db.get_compaction(cid)
    assert row is not None
    assert row.covered_through_message_id > 0
    assert "old-user-0" in row.summary_text or "User talked" in row.summary_text


def test_batch_gate_skips_second_summarize(tmp_path: Path) -> None:
    settings = _settings(tmp_path, compaction_batch_pairs=2)
    db = Database(tmp_path / "mimir.db")
    cid = "c-batch"
    db.ensure_conversation(cid)
    _seed_pairs(db, cid, 4, prefix="b")  # 2 aged pairs with history_pairs=2

    client = ScriptedOllama(
        [ChatMessage(role="assistant", content="Summary of early turns.")]
    )
    assemble_persist_messages(
        db=db,
        client=client,
        settings=settings,
        conversation_id=cid,
        system="sys",
        user_text="next",
    )
    assert len(client.calls) == 1
    covered = db.get_compaction(cid)
    assert covered is not None

    # One more pair — only 0 new uncovered aged pairs beyond covered after first fold
    # of both aged pairs; next assemble should not call summarizer.
    db.append_message(cid, "user", "fresh-user")
    db.append_message(cid, "assistant", "fresh-assistant")
    assemble_persist_messages(
        db=db,
        client=client,
        settings=settings,
        conversation_id=cid,
        system="sys",
        user_text="again",
    )
    assert len(client.calls) == 1


def test_recent_user_statements_pinned_in_system(tmp_path: Path) -> None:
    """Newer user facts in the verbatim window must appear in the system block."""
    settings = _settings(tmp_path, compaction_batch_pairs=1)
    db = Database(tmp_path / "mimir.db")
    cid = "c-heim"
    db.ensure_conversation(cid)
    _seed_pairs(db, cid, 3, prefix="early")
    db.append_message(cid, "user", "Nog iets: het project heet Heim.")
    db.append_message(cid, "assistant", "Ok, Heim.")

    client = ScriptedOllama(
        [ChatMessage(role="assistant", content="early facts summarized")]
    )
    messages = assemble_persist_messages(
        db=db,
        client=client,
        settings=settings,
        conversation_id=cid,
        system="You are Mimir.",
        user_text="Herhaal de drie feiten en de projectnaam.",
    )
    system = messages[0].content
    assert RECENT_USER_MARK in system
    assert "het project heet Heim" in system


def test_pending_confirm_bypasses_summarize(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(tmp_path / "mimir.db")
    cid = "c-confirm"
    db.ensure_conversation(cid)
    _seed_pairs(db, cid, 3, prefix="r")
    pending = PendingRecipeStore()
    pending.set(
        cid,
        {
            "title": "Pannenkoeken",
            "ingredients": ["ei"],
            "steps": ["bak"],
        },
        dutch=True,
    )
    assert should_bypass_compaction("ja", pending, cid)

    client = ScriptedOllama(
        [ChatMessage(role="assistant", content="should not run")]
    )
    messages = assemble_persist_messages(
        db=db,
        client=client,
        settings=settings,
        conversation_id=cid,
        system="sys",
        user_text="ja",
        pending_recipes=pending,
    )
    assert client.calls == []
    assert db.get_compaction(cid) is None
    assert SUMMARY_SYSTEM_MARK not in messages[0].content
    # Recent confirm-era turns remain available in the window
    assert any("r-user-2" in m.content for m in messages)


def test_summarizer_failure_falls_back(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(tmp_path / "mimir.db")
    cid = "c-fail"
    db.ensure_conversation(cid)
    _seed_pairs(db, cid, 3, prefix="f")

    client = ScriptedOllama([RuntimeError("ollama down")])
    messages = assemble_persist_messages(
        db=db,
        client=client,
        settings=settings,
        conversation_id=cid,
        system="sys",
        user_text="hello",
    )
    assert len(client.calls) == 1
    assert db.get_compaction(cid) is None
    assert messages[0].role == "system"
    assert messages[-1].content == "hello"
    # Sliding window + uncovered aged still present
    assert any("f-user" in m.content for m in messages)
    assert any("f-user-0" in m.content for m in messages)


def test_upsert_compaction_rejects_regression(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    db.ensure_conversation("c1")
    assert db.upsert_compaction(
        "c1", summary_text="a", covered_through_message_id=10
    )
    assert not db.upsert_compaction(
        "c1", summary_text="b", covered_through_message_id=5
    )
    row = db.get_compaction("c1")
    assert row is not None
    assert row.covered_through_message_id == 10
    assert row.summary_text == "a"


def test_cas_rejects_stale_expected(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    db.ensure_conversation("c1")
    assert db.upsert_compaction(
        "c1", summary_text="a", covered_through_message_id=3
    )
    assert not db.upsert_compaction(
        "c1",
        summary_text="b",
        covered_through_message_id=6,
        expected_covered_through=1,
    )


def test_empty_summary_does_not_advance_coverage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    db = Database(tmp_path / "mimir.db")
    cid = "c-empty"
    db.ensure_conversation(cid)
    _seed_pairs(db, cid, 3, prefix="e")
    client = ScriptedOllama([ChatMessage(role="assistant", content="   ")])
    messages = assemble_persist_messages(
        db=db,
        client=client,
        settings=settings,
        conversation_id=cid,
        system="sys",
        user_text="hi",
    )
    assert db.get_compaction(cid) is None
    # Uncovered aged turns remain in the prompt when fold fails.
    assert any("e-user-0" in m.content for m in messages)
