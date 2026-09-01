"""Tests for light device_id resolution before lights.set_state."""

from __future__ import annotations

from brain.mcp.lights import (
    light_not_found_error,
    looks_like_dirigera_device_id,
    parse_lights_list,
    pick_light_id,
    present_lights_set_state_json,
    resolve_light,
    set_state_tool_succeeded,
)


def _lights() -> list[dict]:
    return [
        {
            "id": "e1fb890c-1111-2222-3333-444444444444_1",
            "name": "Ballon",
            "room": "Kantoor",
            "isOn": True,
        },
        {
            "id": "e1fb890c-aaaa-bbbb-cccc-dddddddddddd_1",
            "name": "Desk",
            "room": "Kantoor",
            "isOn": False,
        },
        {
            "id": "e1fb890c-5555-6666-7777-888888888888_1",
            "name": "Sofa",
            "room": "Woonkamer",
            "isOn": True,
        },
    ]


def test_parse_lights_list_accepts_array() -> None:
    raw = '[{"id": "x", "name": "Ballon", "room": "Kantoor", "isOn": true}]'
    parsed = parse_lights_list(raw)
    assert parsed is not None and len(parsed) == 1


def test_looks_like_dirigera_device_id() -> None:
    assert looks_like_dirigera_device_id("e1fb890c-1111-2222-3333-444444444444_1")
    assert not looks_like_dirigera_device_id("Ballon")
    assert not looks_like_dirigera_device_id("")


def test_pick_light_id_exact_name() -> None:
    assert pick_light_id(_lights(), "Ballon") == "e1fb890c-1111-2222-3333-444444444444_1"


def test_pick_light_id_exact_room_single() -> None:
    lights = [_lights()[2]]
    assert pick_light_id(lights, "Woonkamer") == "e1fb890c-5555-6666-7777-888888888888_1"


def test_resolve_light_room_ambiguous() -> None:
    result = resolve_light(_lights(), "Kantoor")
    assert result.status == "ambiguous"
    assert len(result.matches) == 2


def test_resolve_light_not_found() -> None:
    assert resolve_light(_lights(), "Garage").status == "not_found"


def test_resolve_light_accepts_device_id() -> None:
    device_id = "e1fb890c-1111-2222-3333-444444444444_1"
    result = resolve_light(_lights(), device_id)
    assert result.status == "found"
    assert result.device_id == device_id


def test_present_lights_set_state_json_adds_note() -> None:
    raw = '{"success": true, "device_id": "x", "on": false}'
    out = present_lights_set_state_json(raw)
    assert "success" in out
    assert out.startswith("Note:")


def test_set_state_tool_succeeded() -> None:
    ok = present_lights_set_state_json('{"success": true, "device_id": "x", "on": false}')
    assert set_state_tool_succeeded(ok)
    assert not set_state_tool_succeeded('{"success": false, "error": "offline"}')
    assert not set_state_tool_succeeded(light_not_found_error("x"))


def test_light_not_found_error_uses_error_prefix() -> None:
    assert light_not_found_error("Ballon").startswith("error:")


def test_extract_lamp_name_hint() -> None:
    from brain.mcp.lights import (
        extract_lamp_name_hint,
        extract_room_all_hint,
        prefer_device_id_for_set_state,
        resolve_set_state_device_ids,
    )

    assert extract_lamp_name_hint("zet de ballon lamp aan") == "ballon"
    assert extract_lamp_name_hint("Turn off Ballon") == "Ballon"
    assert extract_room_all_hint("zet de woonkamer lampen aan") == "woonkamer"
    assert extract_lamp_name_hint("zet de woonkamer lampen aan") is None
    assert prefer_device_id_for_set_state(
        "zet de ballon lamp aan",
        "e47ca80f-4d4c-4317-9e94-48ce765131d9_1",
    ) == "ballon"
    assert prefer_device_id_for_set_state(
        "zet de woonkamer lampen aan",
        "e47ca80f-4d4c-4317-9e94-48ce765131d9_1",
    ) == "room:woonkamer"


def test_infer_light_on_from_user_message() -> None:
    from brain.mcp.lights import infer_light_on_from_user_message, light_set_state_args_from_user_message

    assert infer_light_on_from_user_message("zet de woonkamer lampen uit") is False
    assert infer_light_on_from_user_message("zet de ballon lamp aan") is True
    args = light_set_state_args_from_user_message("zet de woonkamer lampen uit")
    assert args == {"device_id": "room:woonkamer", "on": False}


def test_resolve_set_state_device_ids_room_all() -> None:
    from brain.mcp.lights import resolve_set_state_device_ids

    lights = [
        {"id": "w1", "name": "eettafel", "room": "Woonkamer "},
        {"id": "w2", "name": "paarse", "room": "Woonkamer"},
        {"id": "w3", "name": "bank", "room": "Woonkamer"},
        {"id": "k1", "name": "Ballon", "room": "Kantoor"},
    ]
    ids, err = resolve_set_state_device_ids(lights, "room:woonkamer")
    assert err is None
    assert set(ids) == {"w1", "w2", "w3"}
