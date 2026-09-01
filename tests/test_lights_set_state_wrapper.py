"""Integration tests: lights.set_state wrapper resolves names before Homebase call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from brain.mcp.errors import tool_result_is_error
from brain.mcp.lights import set_state_tool_succeeded
from tests.test_mcp_client import _BridgeRunner, _settings


def _make_homebase_lights_server() -> tuple[MCPServer, dict[str, Any]]:
    mcp = MCPServer("Homebase-lights-test")
    device_id = "e1fb890c-1111-2222-3333-444444444444_1"
    state: dict[str, Any] = {
        "lights": [
            {
                "id": device_id,
                "name": "Ballon",
                "room": "Kantoor",
                "isOn": True,
            }
        ],
        "set_calls": [],
    }

    @mcp.tool(name="homebase.lights.list")
    def lights_list() -> list[dict[str, Any]]:
        return list(state["lights"])

    @mcp.tool(name="homebase.lights.set_state")
    def lights_set_state(device_id: str, on: bool, brightness: float | None = None) -> dict[str, Any]:
        state["set_calls"].append({"device_id": device_id, "on": on, "brightness": brightness})
        return {"success": True, "device_id": device_id, "on": on}

    return mcp, state


def test_set_state_by_lamp_name_resolves_and_succeeds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, state = _make_homebase_lights_server()
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        out = runner.call("homebase.lights.set_state", {"device_id": "Ballon", "on": False})
        assert state["set_calls"] == [
            {
                "device_id": "e1fb890c-1111-2222-3333-444444444444_1",
                "on": False,
                "brightness": None,
            }
        ]
        assert set_state_tool_succeeded(out)
        assert not tool_result_is_error(out)


def test_set_state_unknown_name_does_not_call_homebase(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, state = _make_homebase_lights_server()
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        out = runner.call("homebase.lights.set_state", {"device_id": "Garage", "on": False})
        assert state["set_calls"] == []
        assert tool_result_is_error(out)
        assert not set_state_tool_succeeded(out)


def test_set_state_room_all_calls_homebase_for_each(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("Homebase-lights-room-test")
    state: dict[str, Any] = {
        "lights": [
            {"id": "w1", "name": "eettafel", "room": "Woonkamer", "isOn": False},
            {"id": "w2", "name": "paarse", "room": "Woonkamer", "isOn": False},
            {"id": "w3", "name": "bank", "room": "Woonkamer", "isOn": False},
        ],
        "set_calls": [],
    }

    @mcp.tool(name="homebase.lights.list")
    def lights_list() -> list[dict[str, Any]]:
        return list(state["lights"])

    @mcp.tool(name="homebase.lights.set_state")
    def lights_set_state(device_id: str, on: bool, brightness: float | None = None) -> dict[str, Any]:
        state["set_calls"].append({"device_id": device_id, "on": on})
        return {"success": True, "device_id": device_id, "on": on}

    with _BridgeRunner(settings, {"homebase": mcp}) as runner:
        out = runner.call(
            "homebase.lights.set_state",
            {"device_id": "room:woonkamer", "on": True},
        )
        assert len(state["set_calls"]) == 3
        assert {c["device_id"] for c in state["set_calls"]} == {"w1", "w2", "w3"}
        assert set_state_tool_succeeded(out)
        assert "devices_toggled" in out
