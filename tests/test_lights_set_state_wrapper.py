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


def test_set_state_includes_prior_is_on_from_list(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    device_id = "e1fb890c-1111-2222-3333-444444444444_1"
    mcp = MCPServer("Homebase-lights-prior-test")
    state: dict[str, Any] = {
        "lights": [
            {
                "id": device_id,
                "name": "Ballon",
                "room": "Kantoor",
                "isOn": True,
                "reachable": False,
            }
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
            {"device_id": "Ballon", "on": False},
        )
        assert state["set_calls"] == [{"device_id": device_id, "on": False}]
        assert "prior_isOn" in out
        assert "reachable" in out
        assert set_state_tool_succeeded(out)


def test_set_state_room_all_via_en_alias(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("Homebase-lights-alias-test")
    state: dict[str, Any] = {
        "lights": [
            {"id": "w1", "name": "eettafel", "room": "Woonkamer", "isOn": False},
            {"id": "w2", "name": "paarse", "room": "Woonkamer", "isOn": False},
            {"id": "k1", "name": "Ballon", "room": "Kantoor", "isOn": True},
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
            {"device_id": "room:living room", "on": False},
        )
        assert {c["device_id"] for c in state["set_calls"]} == {"w1", "w2"}
        assert set_state_tool_succeeded(out)

        state["set_calls"].clear()
        out2 = runner.call(
            "homebase.lights.set_state",
            {"device_id": "office", "on": False},
        )
        assert state["set_calls"] == [{"device_id": "k1", "on": False}]
        assert set_state_tool_succeeded(out2)


def test_set_state_success_false_stops_batch(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("Homebase-lights-fail-test")
    state: dict[str, Any] = {
        "lights": [
            {"id": "w1", "name": "eettafel", "room": "Woonkamer", "isOn": True},
            {"id": "w2", "name": "paarse", "room": "Woonkamer", "isOn": True},
        ],
        "set_calls": [],
    }

    @mcp.tool(name="homebase.lights.list")
    def lights_list() -> list[dict[str, Any]]:
        return list(state["lights"])

    @mcp.tool(name="homebase.lights.set_state")
    def lights_set_state(device_id: str, on: bool, brightness: float | None = None) -> dict[str, Any]:
        state["set_calls"].append({"device_id": device_id, "on": on})
        if device_id == "w1":
            return {
                "success": False,
                "error": "Device unreachable (Zigbee mesh)",
                "device_id": device_id,
                "on": on,
            }
        return {"success": True, "device_id": device_id, "on": on}

    with _BridgeRunner(settings, {"homebase": mcp}) as runner:
        out = runner.call(
            "homebase.lights.set_state",
            {"device_id": "room:woonkamer", "on": False},
        )
        assert len(state["set_calls"]) == 1
        assert not set_state_tool_succeeded(out)
        assert "Device unreachable (Zigbee mesh)" in out
        assert "eettafel" in out
        assert "failed" in out.lower()


def test_set_state_house_wide_skips_unreachable_and_continues(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("Homebase-lights-house-wide-test")
    state: dict[str, Any] = {
        "lights": [
            {"id": "k1", "name": "Ballon", "room": "Kantoor", "isOn": False, "reachable": True},
            {
                "id": "w1",
                "name": "ghost",
                "room": "Woonkamer",
                "isOn": False,
                "reachable": False,
            },
            {"id": "e1", "name": "eettafel", "room": "Woonkamer", "isOn": False},
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
            {"device_id": "all:", "on": True},
        )
        assert {c["device_id"] for c in state["set_calls"]} == {"k1", "e1"}
        assert set_state_tool_succeeded(out)
        assert "house_wide" in out
        assert "ghost" in out
        assert "devices_toggled" in out


def test_set_state_stale_id_relists_and_retries(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("Homebase-lights-stale-test")
    state: dict[str, Any] = {
        "lights": [
            {"id": "old-id", "name": "Ballon", "room": "Kantoor", "isOn": True},
        ],
        "set_calls": [],
        "list_calls": 0,
    }

    @mcp.tool(name="homebase.lights.list")
    def lights_list() -> list[dict[str, Any]]:
        state["list_calls"] += 1
        if state["list_calls"] >= 2:
            state["lights"] = [
                {"id": "new-id", "name": "Ballon", "room": "Kantoor", "isOn": True},
            ]
        return list(state["lights"])

    @mcp.tool(name="homebase.lights.set_state")
    def lights_set_state(device_id: str, on: bool, brightness: float | None = None) -> dict[str, Any]:
        state["set_calls"].append({"device_id": device_id, "on": on})
        if device_id == "old-id":
            return {
                "success": False,
                "error": "Unknown or stale device_id",
                "device_id": device_id,
                "on": on,
            }
        return {"success": True, "device_id": device_id, "on": on}

    with _BridgeRunner(settings, {"homebase": mcp}) as runner:
        out = runner.call(
            "homebase.lights.set_state",
            {"device_id": "Ballon", "on": False},
        )
        assert [c["device_id"] for c in state["set_calls"]] == ["old-id", "new-id"]
        assert state["list_calls"] >= 2
        assert set_state_tool_succeeded(out)


def test_t040_forwards_appearance_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    device_id = "e1fb890c-1111-2222-3333-444444444444_1"
    mcp = MCPServer("Homebase-lights-t040-fwd")
    state: dict[str, Any] = {
        "lights": [
            {
                "id": device_id,
                "name": "paarse lamp",
                "room": "Woonkamer",
                "isOn": True,
                "supports_color": True,
                "supports_color_temp": True,
            }
        ],
        "set_calls": [],
    }

    @mcp.tool(name="homebase.lights.list")
    def lights_list() -> list[dict[str, Any]]:
        return list(state["lights"])

    @mcp.tool(name="homebase.lights.set_state")
    def lights_set_state(
        device_id: str,
        on: bool,
        brightness: float | None = None,
        color_temp_kelvin: float | None = None,
        color_preset: str | None = None,
        color_hex: str | None = None,
    ) -> dict[str, Any]:
        state["set_calls"].append(
            {
                "device_id": device_id,
                "on": on,
                "brightness": brightness,
                "color_temp_kelvin": color_temp_kelvin,
                "color_preset": color_preset,
                "color_hex": color_hex,
            }
        )
        return {"success": True, "device_id": device_id, "on": on}

    with _BridgeRunner(settings, {"homebase": mcp}) as runner:
        out = runner.call(
            "homebase.lights.set_state",
            {
                "device_id": "paarse",
                "on": True,
                "brightness": 40,
                "color_preset": "saturated_red",
            },
        )
        assert set_state_tool_succeeded(out)
        assert state["set_calls"] == [
            {
                "device_id": device_id,
                "on": True,
                "brightness": 40,
                "color_temp_kelvin": None,
                "color_preset": "saturated_red",
                "color_hex": None,
            }
        ]

        state["set_calls"].clear()
        out2 = runner.call(
            "homebase.lights.set_state",
            {
                "device_id": "paarse",
                "on": True,
                "color_temp_kelvin": 2700,
            },
        )
        assert set_state_tool_succeeded(out2)
        assert state["set_calls"][0]["color_temp_kelvin"] == 2700

        state["set_calls"].clear()
        out3 = runner.call(
            "homebase.lights.set_state",
            {
                "device_id": "paarse",
                "on": True,
                "color_hex": "#DC4B31",
            },
        )
        assert set_state_tool_succeeded(out3)
        assert state["set_calls"][0]["color_hex"] == "#DC4B31"


def test_t040_room_all_colour_fans_out(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mcp = MCPServer("Homebase-lights-t040-room-colour")
    state: dict[str, Any] = {
        "lights": [
            {
                "id": "w1",
                "name": "eettafel",
                "room": "Woonkamer",
                "isOn": False,
                "supports_color": True,
            },
            {
                "id": "w2",
                "name": "paarse",
                "room": "Woonkamer",
                "isOn": False,
                "supports_color": True,
            },
            {
                "id": "w3",
                "name": "ct-only",
                "room": "Woonkamer",
                "isOn": False,
                "supports_color": False,
                "supports_color_temp": True,
            },
        ],
        "set_calls": [],
    }

    @mcp.tool(name="homebase.lights.list")
    def lights_list() -> list[dict[str, Any]]:
        return list(state["lights"])

    @mcp.tool(name="homebase.lights.set_state")
    def lights_set_state(
        device_id: str,
        on: bool,
        color_preset: str | None = None,
        color_temp_kelvin: float | None = None,
    ) -> dict[str, Any]:
        state["set_calls"].append(
            {
                "device_id": device_id,
                "on": on,
                "color_preset": color_preset,
            }
        )
        return {"success": True, "device_id": device_id, "on": on}

    with _BridgeRunner(settings, {"homebase": mcp}) as runner:
        out = runner.call(
            "homebase.lights.set_state",
            {
                "device_id": "room:woonkamer",
                "on": True,
                "color_preset": "saturated_red",
            },
        )
        assert {c["device_id"] for c in state["set_calls"]} == {"w1", "w2"}
        assert all(c["color_preset"] == "saturated_red" for c in state["set_calls"])
        assert set_state_tool_succeeded(out)
        assert "devices_toggled" in out
        assert "ct-only" in out or "skipped" in out


def test_t040_capability_gate_no_colour(tmp_path: Path) -> None:
    from brain.mcp.lights import DEVICE_NO_COLOUR_ERROR

    settings = _settings(tmp_path)
    device_id = "ballon-id"
    mcp = MCPServer("Homebase-lights-t040-cap")
    state: dict[str, Any] = {
        "lights": [
            {
                "id": device_id,
                "name": "Ballon",
                "room": "Kantoor",
                "isOn": True,
                "supports_color": False,
                "supports_color_temp": True,
            }
        ],
        "set_calls": [],
    }

    @mcp.tool(name="homebase.lights.list")
    def lights_list() -> list[dict[str, Any]]:
        return list(state["lights"])

    @mcp.tool(name="homebase.lights.set_state")
    def lights_set_state(
        device_id: str,
        on: bool,
        color_preset: str | None = None,
    ) -> dict[str, Any]:
        state["set_calls"].append({"device_id": device_id, "on": on})
        return {"success": True, "device_id": device_id, "on": on}

    with _BridgeRunner(settings, {"homebase": mcp}) as runner:
        out = runner.call(
            "homebase.lights.set_state",
            {
                "device_id": "Ballon",
                "on": True,
                "color_preset": "saturated_red",
            },
        )
        assert state["set_calls"] == []
        assert not set_state_tool_succeeded(out)
        assert DEVICE_NO_COLOUR_ERROR in out
        assert "Ballon" in out