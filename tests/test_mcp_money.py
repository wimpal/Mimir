"""Tests for MCP money presentation layer."""

from __future__ import annotations

import json

from brain.mcp.money import present_money_json


def test_present_money_json_adds_euros_fields() -> None:
    raw = json.dumps(
        {
            "rows": [{"category": "Eten buiten de deur", "spent": 29202, "currency": "EUR"}],
            "totals": {"spent": 243340, "currency": "EUR"},
        }
    )
    out = present_money_json(raw)
    assert "spent_euros" in out
    assert "292.02" in out
    data = json.loads(out.split("\n", 1)[1])
    assert data["rows"][0]["spent_euros"] == 292.02
    assert data["totals"]["spent_euros"] == 2433.4


def test_present_money_json_passthrough_non_json() -> None:
    assert present_money_json("plain text") == "plain text"
