"""Compare summary vs search via raw MCP HTTP (no brain loop)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

URL = "http://192.168.1.142:8080/mcp"
TOKEN = os.environ.get("BUDGETTRACKER_TOKEN", "")
HEADERS = {
    "Host": "localhost",
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def mcp_call(tool: str, arguments: dict) -> str:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    with httpx.Client(timeout=30.0) as client:
        client.post(URL, headers=HEADERS, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        init = client.post(
            URL,
            headers=HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "compare-script", "version": "1.0"},
                },
            },
        )
        if init.status_code not in (200, 202):
            sys.exit(f"initialize failed: {init.status_code} {init.text[:200]}")
        client.post(URL, headers=HEADERS, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        res = client.post(URL, headers=HEADERS, json=body)
        res.raise_for_status()
        data = res.json()
        content = data["result"]["content"][0]["text"]
        if data["result"].get("isError"):
            return f"ERROR: {content}"
        return content


def boodschappen_summary(raw: str) -> int:
    data = json.loads(raw)
    row = next((r for r in data.get("rows", []) if r.get("category") == "Boodschappen"), None)
    return int(row["spent"]) if row else 0


def search_sum(raw: str) -> tuple[int, int]:
    items = json.loads(raw)
    if not isinstance(items, list):
        return 0, 0
    total = sum(int(x.get("amount") or 0) for x in items if not x.get("planned"))
    return total, len(items)


def show_period(label: str, fr: str, to: str) -> None:
    print(f"=== {label} ({fr} .. {to}) ===")
    summary = mcp_call("budgettracker.summary.by_category", {"from": fr, "to": to, "top_n": 50})
    search = mcp_call(
        "budgettracker.transactions.search",
        {"category": "Boodschappen", "from": fr, "to": to, "limit": 200},
    )
    if summary.startswith("ERROR:"):
        print(f"  summary: {summary}")
        return
    if search.startswith("ERROR:"):
        print(f"  search: {search}")
        return
    s = boodschappen_summary(summary)
    t, n = search_sum(search)
    print(f"  summary Boodschappen: {s:>6} cents  ({s/100:>8.2f} EUR)")
    print(f"  search sum:           {t:>6} cents  ({t/100:>8.2f} EUR)  [{n} rows]")
    print(f"  tools agree: {'yes' if s == t else 'NO'}")
    print()


def main() -> None:
    if not TOKEN:
        sys.exit("Set BUDGETTRACKER_TOKEN in .env")

    show_period("August 2026 — deze maand", "2026-08-01", "2026-08-31")
    show_period("July–Aug — Mimir 07:22 log range", "2026-07-01", "2026-08-31")
    show_period("July 2026 — vorige maand", "2026-07-01", "2026-07-31")

    print("=== August Boodschappen by person (search only) ===")
    for person in ("Ilse", "Wim"):
        raw = mcp_call(
            "budgettracker.transactions.search",
            {
                "category": "Boodschappen",
                "from": "2026-08-01",
                "to": "2026-08-31",
                "person": person,
                "limit": 200,
            },
        )
        if raw.startswith("ERROR:"):
            print(f"  {person}: {raw}")
        else:
            t, n = search_sum(raw)
            print(f"  {person}: {t} cents ({t/100:.2f} EUR), {n} rows")
    print()

    print("=== Mimir 06:50 session used summary aug only ===")
    raw = mcp_call("budgettracker.summary.by_category", {"from": "2026-08-01", "to": "2026-08-31", "top_n": 15})
    s = boodschappen_summary(raw)
    print(f"  summary Boodschappen August: {s} cents ({s/100:.2f} EUR)")


if __name__ == "__main__":
    main()
