"""Tests for chore id resolution before tasks.complete."""

from __future__ import annotations

from brain.mcp.tasks import (
    chore_not_found_error,
    complete_tool_succeeded,
    looks_like_chore_id,
    parse_tasks_list,
    pick_chore_id,
    present_task_complete_json,
    resolve_complete_ids,
)
from brain.mcp.errors import tool_result_is_error


def test_parse_tasks_list_accepts_single_object() -> None:
    raw = '{"id": "c1", "title": "dweilen", "done": false}'
    tasks = parse_tasks_list(raw)
    assert tasks is not None and len(tasks) == 1
    assert tasks[0]["title"] == "dweilen"


def test_looks_like_chore_id() -> None:
    assert looks_like_chore_id("clxyz1234567890123456789")
    assert not looks_like_chore_id("stofzuigen")
    assert not looks_like_chore_id("")


def test_pick_chore_id_exact_title() -> None:
    tasks = [
        {"id": "cabc123456789012345678901", "title": "stofzuigen"},
        {"id": "cdef123456789012345678901", "title": "afwas"},
    ]
    assert pick_chore_id(tasks, "stofzuigen") == "cabc123456789012345678901"


def test_pick_chore_id_substring_unique() -> None:
    tasks = [{"id": "cid1", "title": "MCP smoke test"}]
    assert pick_chore_id(tasks, "smoke") == "cid1"


def test_pick_chore_id_ambiguous_returns_first() -> None:
    tasks = [
        {"id": "c1", "title": "test"},
        {"id": "c2", "title": "test"},
    ]
    assert pick_chore_id(tasks, "test") == "c1"


def test_resolve_complete_ids_all_exact_duplicates() -> None:
    tasks = [
        {"id": "c1", "title": "dweilen"},
        {"id": "c2", "title": "dweilen"},
        {"id": "c3", "title": "other"},
    ]
    assert resolve_complete_ids(tasks, "dweilen") == ["c1", "c2"]


def test_resolve_complete_ids_rejects_stale_cuid() -> None:
    tasks = [{"id": "c-active", "title": "dweilen"}]
    stale = "clxyz123456789012345678901"
    assert stale not in {t["id"] for t in tasks}
    assert resolve_complete_ids(tasks, stale) == []


def test_resolve_complete_ids_accepts_active_cuid() -> None:
    tasks = [{"id": "c-active", "title": "dweilen"}]
    assert resolve_complete_ids(tasks, "c-active") == ["c-active"]


def test_pick_chore_id_partial_unique() -> None:
    tasks = [
        {"id": "c1", "title": "stofzuigen"},
        {"id": "c2", "title": "stofzuigen woonkamer"},
    ]
    assert pick_chore_id(tasks, "stofzuigen") == "c1"


def test_present_task_complete_json_adds_hint() -> None:
    raw = '{"id": "c1", "title": "stofzuigen", "done": false}'
    out = present_task_complete_json(raw)
    assert "completion_recorded" in out
    assert "Ignore done:false" in out


def test_chore_not_found_error_uses_error_prefix() -> None:
    assert chore_not_found_error("dweilen").startswith("error:")


def test_complete_tool_succeeded() -> None:
    ok = present_task_complete_json('{"id": "c1", "title": "dweilen", "done": false}')
    assert complete_tool_succeeded(ok)
    assert not complete_tool_succeeded('[{"id":"c1","title":"dweilen"}]')
    assert not complete_tool_succeeded(chore_not_found_error("x"))


def test_present_task_complete_json_skips_errors() -> None:
    assert present_task_complete_json('error: not found') == "error: not found"
    assert present_task_complete_json(
        'error: {"error":{"code":"not_found"}}'
    ).startswith("error:")
