"""Present CONVENTIONS money (integer cents) in forms the model can read aloud."""

from __future__ import annotations

import json
from typing import Any

_MONEY_INT_FIELDS = frozenset({"amount", "spent", "budgeted", "remaining"})

_MONEY_NOTE = (
    "Note: integer fields amount/spent/budgeted/remaining are minor units (cents). "
    "Use the matching *_euros fields in user-facing replies."
)


def _annotate_money(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out[key] = _annotate_money(item)
        for key in _MONEY_INT_FIELDS:
            raw = value.get(key)
            if isinstance(raw, int):
                out[f"{key}_euros"] = round(raw / 100, 2)
        return out
    if isinstance(value, list):
        return [_annotate_money(item) for item in value]
    return value


def present_money_json(text: str) -> str:
    """If text is JSON from a budget tool, add *_euros fields for the model."""
    stripped = text.strip()
    if not stripped or not stripped[:1] in "{[":
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    annotated = _annotate_money(parsed)
    body = json.dumps(annotated, ensure_ascii=False)
    return f"{_MONEY_NOTE}\n{body}"
