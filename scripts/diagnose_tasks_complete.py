"""Red-capable loop: tasks.complete must log outcome=success (in-memory Homebase mock).

Usage: uv run python scripts/diagnose_tasks_complete.py
Exit 0 = pass, 1 = fail (bug present).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mcp.server import MCPServer

from brain.mcp.errors import tool_result_is_error
from brain.mcp.log import mcp_tools_log_path
from brain.mcp.tasks import complete_tool_succeeded, parse_tasks_list
from tests.test_mcp_client import _BridgeRunner, _settings

TITLE = "dweilen"
MARKER = "diag-complete-probe"


def _make_server(state: dict[str, Any]) -> MCPServer:
    mcp = MCPServer("diag-homebase")

    @mcp.tool(name="homebase.tasks.list")
    def tasks_list() -> list[dict[str, Any]]:
        return list(state["chores"])

    @mcp.tool(name="homebase.tasks.add")
    def tasks_add(title: str) -> dict[str, Any]:
        chore = {"id": f"clxyz{title}123456789012345678", "title": title, "done": False}
        state["chores"].append(chore)
        return chore

    @mcp.tool(name="homebase.tasks.complete")
    def tasks_complete(id: str) -> dict[str, Any]:  # noqa: A002
        state["complete_calls"].append(id)
        for chore in state["chores"]:
            if chore["id"] == id:
                state["chores"] = [c for c in state["chores"] if c["id"] != id]
                return {**chore, "done": False}
        raise ValueError("not found")

    return mcp


def _tail_complete_lines(data_dir: Path) -> list[dict[str, Any]]:
    path = mcp_tools_log_path(data_dir)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("tool") == "homebase.tasks.complete":
            out.append(row)
    return out


def main() -> int:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="mimir-diag-"))
    settings = _settings(tmp)
    state: dict[str, Any] = {"chores": [], "complete_calls": []}
    server = _make_server(state)
    before = 0

    with _BridgeRunner(settings, {"homebase": server}) as runner:
        before = len(_tail_complete_lines(settings.runtime.data_dir))

        probe_title = f"{MARKER}-{TITLE}"
        add_out = runner.call("homebase.tasks.add", {"title": probe_title})
        if tool_result_is_error(add_out):
            print(f"FAIL add: {add_out[:200]}")
            return 1

        stale = "clxyz123456789012345678901"
        false_out = runner.call("homebase.tasks.complete", {"id": stale})
        if complete_tool_succeeded(false_out):
            print("FAIL stale cuid treated as complete success")
            return 1
        if state["complete_calls"]:
            print("FAIL stale cuid triggered homebase.tasks.complete")
            return 1

        title_out = runner.call("homebase.tasks.complete", {"id": probe_title})
        if not complete_tool_succeeded(title_out):
            print(f"FAIL title complete: {title_out[:300]}")
            return 1

        remaining = parse_tasks_list(runner.call("homebase.tasks.list", {})) or []
        if any(probe_title in (c.get("title") or "") for c in remaining):
            print("FAIL chore still active after complete")
            return 1

        logs = _tail_complete_lines(settings.runtime.data_dir)
        success_logs = [r for r in logs[before:] if r.get("outcome") == "success"]
        if not success_logs:
            print("FAIL no homebase.tasks.complete outcome=success in mcp_tools.jsonl")
            return 1

    print("PASS wrapper completes by title; stale cuid fails loud")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
