"""Shopping-list helpers — filter MCP smoke-test rows from operator-facing output."""

from __future__ import annotations

import json
import re
from typing import Any

_SMOKE_PRODUCT_NAME = re.compile(r"^mcp-smoke", re.IGNORECASE)


def is_shopping_list_smoke_name(name: str) -> bool:
    """True for Homebase MCP smoke-test product names (catalog noise on list)."""
    return bool(_SMOKE_PRODUCT_NAME.match((name or "").strip()))


def filter_shopping_list_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep needed list slots; drop smoke-test names and checked-off rows."""
    kept: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if is_shopping_list_smoke_name(name):
            continue
        if item.get("checked") is True:
            continue
        kept.append(item)
    return kept


def filter_shopping_list_tool_result(raw: str) -> str:
    """Rewrite shopping_list.list JSON for the agent and fixup layers."""
    text = (raw or "").strip()
    if not text or text.startswith("error:"):
        return raw
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return raw
    if not isinstance(data, list):
        return raw
    filtered = filter_shopping_list_items([item for item in data if isinstance(item, dict)])
    return json.dumps(filtered)
