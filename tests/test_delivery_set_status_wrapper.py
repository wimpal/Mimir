"""Unit tests for delivery.set_status id resolution (T-096)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from brain.mcp.delivery import (
    confirm_reply_from_set_status_result,
    normalize_delivery_status,
    present_delivery_set_status_json,
    resolve_delivery_ids,
)
from brain.mcp.errors import tool_result_is_error
from tests.test_mcp_client import _BridgeRunner, _settings


def test_normalize_delivery_status() -> None:
    assert normalize_delivery_status("delivered") == "DELIVERED"
    assert normalize_delivery_status("bezorgd") == "DELIVERED"
    assert normalize_delivery_status("in transit") == "IN_TRANSIT"
    assert normalize_delivery_status("IN_TRANSIT") == "IN_TRANSIT"
    assert normalize_delivery_status("nope") is None


def test_resolve_exact_description_and_sole_package() -> None:
    pkgs = [
        {"id": "c111111111111111111111111", "description": "test pakkie", "status": "PENDING"},
    ]
    ids, err = resolve_delivery_ids(pkgs, "test pakkie")
    assert err is None and ids == ["c111111111111111111111111"]
    ids, err = resolve_delivery_ids(pkgs, "pakkie")
    assert err is None and ids == ["c111111111111111111111111"]
    ids, err = resolve_delivery_ids(pkgs, "mystery label")
    assert err is None and ids == ["c111111111111111111111111"]  # sole package


def test_resolve_ambiguous_partial() -> None:
    pkgs = [
        {"id": "c111111111111111111111111", "description": "Amazon box"},
        {"id": "c222222222222222222222222", "description": "Amazon envelope"},
    ]
    ids, err = resolve_delivery_ids(pkgs, "Amazon")
    assert ids == [] and err == "ambiguous"


def test_resolve_stale_cuid_no_sole_fallback() -> None:
    pkgs = [
        {"id": "c111111111111111111111111", "description": "test pakkie"},
    ]
    stale = "c999999999999999999999999"
    ids, err = resolve_delivery_ids(pkgs, stale)
    assert ids == [] and err is None


def _make_delivery_server() -> tuple[MCPServer, dict[str, Any]]:
    mcp = MCPServer("Homebase-delivery-test")
    state: dict[str, Any] = {
        "packages": [
            {
                "id": "c111111111111111111111111",
                "description": "test pakkie",
                "status": "PENDING",
                "tracking_number": "123",
            },
        ],
        "set_calls": [],
    }

    @mcp.tool(name="homebase.delivery.list")
    def delivery_list(status: str | None = None) -> list[dict[str, Any]]:
        rows = list(state["packages"])
        if status:
            rows = [p for p in rows if p.get("status") == status]
        return rows

    @mcp.tool(name="homebase.delivery.set_status")
    def delivery_set_status(id: str, status: str) -> dict[str, Any]:  # noqa: A002
        state["set_calls"].append({"id": id, "status": status})
        for pkg in state["packages"]:
            if pkg["id"] == id:
                pkg["status"] = status
                return dict(pkg)
        raise ValueError(
            json.dumps({"error": {"code": "not_found", "message": "missing"}})
        )

    return mcp, state


def test_set_status_by_description_calls_homebase(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, state = _make_delivery_server()
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        out = runner.call(
            "homebase.delivery.set_status",
            {"id": "test pakkie", "status": "delivered"},
        )
        assert state["set_calls"] == [
            {"id": "c111111111111111111111111", "status": "DELIVERED"}
        ]
        assert "status_updated" in out
        assert not tool_result_is_error(out)


def test_present_and_confirm_helpers() -> None:
    raw = json.dumps(
        {
            "id": "c111111111111111111111111",
            "description": "test pakkie",
            "status": "DELIVERED",
        }
    )
    presented = present_delivery_set_status_json(raw)
    assert "status_updated" in presented
    confirm = confirm_reply_from_set_status_result(presented)
    assert confirm is not None
    assert "test pakkie" in confirm
    assert "DELIVERED" in confirm
