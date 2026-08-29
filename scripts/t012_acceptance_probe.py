"""T-012 live acceptance probe — POST /v1/chat + MCP verification."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from brain.config import load_config
from brain.mcp.bridge import McpBridge
from brain.mcp.tools import build_mcp_tools

BRAIN = "http://127.0.0.1:8000"
CHAT_TIMEOUT_S = 200


@dataclass
class Case:
    name: str
    message: str
    expect_writes: set[str]
    forbid_writes: set[str]


CASES = [
    Case(
        "read_only_low_stock",
        "what's low on stock?",
        expect_writes=set(),
        forbid_writes={
            "homebase.shopping_list.add_item",
            "homebase.inventory.update",
            "budgettracker.transactions.add",
        },
    ),
    Case(
        "add_coffee",
        "Add coffee to the shopping list",
        expect_writes={"homebase.shopping_list.add_item"},
        forbid_writes=set(),
    ),
    Case(
        "record_expense",
        "We spent €62 at the supermarket",
        expect_writes={"budgettracker.transactions.add"},
        forbid_writes=set(),
    ),
    Case(
        "m3_demo",
        "We spent €62 at the supermarket, and we're out of coffee.",
        expect_writes={
            "homebase.shopping_list.add_item",
            "budgettracker.transactions.add",
        },
        forbid_writes=set(),
    ),
    Case(
        "tasks_read_only",
        "What household tasks are due before Friday?",
        expect_writes=set(),
        forbid_writes={"homebase.tasks.add", "homebase.tasks.complete"},
    ),
    Case(
        "tasks_add",
        "Add a task: take out bins, due tomorrow",
        expect_writes={"homebase.tasks.add"},
        forbid_writes=set(),
    ),
]


def chat(message: str) -> dict:
    body = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(
        f"{BRAIN}/v1/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_cases() -> list[tuple[Case, dict, list[str]]]:
    results: list[tuple[Case, dict, list[str]]] = []
    for case in CASES:
        print(f"\n=== {case.name} ===")
        print(f"User: {case.message}")
        t0 = time.perf_counter()
        try:
            out = chat(case.message)
        except urllib.error.URLError as exc:
            print(f"CHAT FAILED: {exc}")
            results.append((case, {"error": str(exc)}, []))
            continue
        elapsed = time.perf_counter() - t0
        tools = out.get("tools_used") or []
        print(f"Reply ({elapsed:.1f}s): {out.get('reply', '')[:400]}")
        print(f"tools_used: {tools}")
        print(f"stopped_reason: {out.get('stopped_reason')}")
        results.append((case, out, tools))
    return results


def evaluate(results: list[tuple[Case, dict, list[str]]]) -> bool:
    ok = True
    print("\n=== EVALUATION ===")
    for case, out, tools in results:
        if "error" in out:
            print(f"FAIL {case.name}: chat error")
            ok = False
            continue
        tool_set = set(tools)
        missing = case.expect_writes - tool_set
        forbidden = case.forbid_writes & tool_set
        blocked_in_reply = "write blocked" in (out.get("reply") or "").lower()
        if missing:
            print(f"FAIL {case.name}: missing tools {missing}")
            ok = False
        if forbidden:
            print(f"FAIL {case.name}: forbidden tools used {forbidden}")
            ok = False
        if case.expect_writes and blocked_in_reply:
            print(f"FAIL {case.name}: reply mentions write blocked")
            ok = False
        if not missing and not forbidden and "error" not in out:
            print(f"PASS {case.name}")
    return ok


async def verify_and_cleanup() -> None:
    settings = load_config(REPO / "config" / "config.yaml")
    loop = asyncio.get_running_loop()
    bridge = await McpBridge.connect(settings, data_dir=REPO / "data", loop=loop)
    tools = build_mcp_tools(bridge, settings)

    print("\n=== MCP STATE ===")
    shopping = json.loads(tools["homebase.shopping_list.list"].execute())
    coffee = [i for i in shopping if "coffee" in (i.get("name") or "").lower()]
    print(f"Shopping list coffee entries: {len(coffee)}")
    for item in coffee[:5]:
        print(f"  - {item.get('name')} qty={item.get('quantity')} id={item.get('id')}")

    fn = tools["budgettracker.transactions.search"].execute
    try:
        recent = json.loads(fn(query="supermarket", limit=10))
    except TypeError:
        from brain.tools import dispatch

        recent = json.loads(
            dispatch(
                "budgettracker.transactions.search",
                {"query": "supermarket", "limit": 10},
                tools=tools,
            )
        )
    print(f"BT transactions today: {len(recent)}")
    for row in recent[:5]:
        print(
            f"  - {row.get('description')} amount={row.get('amount')} "
            f"cat={row.get('category')}"
        )

    print("\n=== CLEANUP (revert recent MCP writes from audit log) ===")
    for svc, list_tool, revert_tool in (
        ("homebase", "homebase.changes.list", "homebase.changes.revert"),
        ("budgettracker", "budgettracker.changes.list", "budgettracker.changes.revert"),
    ):
        changes = json.loads(tools[list_tool].execute(limit=20))
        probe_rows = [
            c
            for c in changes
            if not c.get("reverted_at")
            and (
                "coffee" in (c.get("summary") or "").lower()
                or "supermarket" in (c.get("summary") or "").lower()
                or "mcp probe" in (c.get("summary") or "").lower()
                or "62" in (c.get("summary") or "")
            )
        ]
        for row in probe_rows[:10]:
            cid = row.get("change_id")
            if not cid:
                continue
            result = tools[revert_tool].execute(change_id=cid)
            print(f"  reverted {svc} {cid}: {result[:120]}")

    try:
        await bridge.close()
    except Exception:
        pass


def main() -> int:
    results = run_cases()
    passed = evaluate(results)

    async def _cleanup() -> None:
        await verify_and_cleanup()

    asyncio.run(_cleanup())
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
