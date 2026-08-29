"""Chore id resolution for homebase.tasks.complete."""

from __future__ import annotations

import json
import re
from typing import Any

from brain.mcp.errors import tool_result_is_error

_CUID_LIKE = re.compile(r"^c[a-z0-9]{20,}$", re.IGNORECASE)


def looks_like_chore_id(value: str) -> bool:
    """True when value matches Prisma cuid shape (not a human title)."""
    return bool(_CUID_LIKE.match(value.strip()))


def resolve_complete_ids(
    tasks: list[dict[str, Any]], raw: str
) -> list[str]:
    """Map user/model id or title to active chore id(s). Completes all exact title matches."""
    needle = raw.strip()
    if not needle:
        return []
    active_by_id = {str(t["id"]): t for t in tasks if t.get("id")}
    if needle in active_by_id:
        return [needle]
    lower = needle.lower()
    exact = [
        str(t["id"])
        for t in tasks
        if (t.get("title") or "").strip().lower() == lower
    ]
    if exact:
        return exact
    partial = [
        t
        for t in tasks
        if lower in (t.get("title") or "").strip().lower()
    ]
    if len(partial) == 1:
        return [str(partial[0]["id"])]
    return []


def pick_chore_id(tasks: list[dict[str, Any]], title_or_id: str) -> str | None:
    """Return one chore id for title_or_id (first when duplicates)."""
    ids = resolve_complete_ids(tasks, title_or_id)
    return ids[0] if ids else None


def parse_tasks_list(raw: str) -> list[dict[str, Any]] | None:
    if raw.startswith("error:"):
        return None
    body = raw.strip()
    if "\n" in body and body.split("\n", 1)[0].startswith("Note:"):
        body = body.split("\n", 1)[1].strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and parsed.get("id") and parsed.get("title"):
        return [parsed]
    return None


def complete_tool_succeeded(text: str) -> bool:
    """True only when tasks.complete actually recorded a completion."""
    if tool_result_is_error(text):
        return False
    return "completion_recorded" in text


def chore_resolve_error(code: str, message: str) -> str:
    payload = {"error": {"code": code, "message": message, "retryable": False}}
    return f"error: {json.dumps(payload, ensure_ascii=False)}"


def chore_not_found_error(title: str, *, active_titles: list[str] | None = None) -> str:
    hint = (
        f"No active chore matching '{title}'. Pass the chore **title** as `id` "
        "(e.g. dweilen), not a cuid from an earlier turn."
    )
    if active_titles:
        hint += f" Active chores now: {', '.join(active_titles)}."
    return chore_resolve_error("not_found", hint)


_TASK_COMPLETE_NOTE = (
    "Note: homebase.tasks.complete succeeded. completion_recorded is true. "
    "Ignore done:false on chores — it does not mean the user's complete request failed. "
    "One-off chores leave the active list; recurring chores roll nextDue forward."
)


def present_task_complete_json(text: str) -> str:
    """Add completion hint so the model does not misread done:false as failure."""
    stripped = text.strip()
    if not stripped or stripped.startswith("error:") or stripped[:1] != "{":
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict) or "error" in parsed:
        return text
    enriched = dict(parsed)
    enriched["completion_recorded"] = True
    body = json.dumps(enriched, ensure_ascii=False)
    return f"{_TASK_COMPLETE_NOTE}\n{body}"
