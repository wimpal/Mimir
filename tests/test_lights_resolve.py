"""Tests for light device_id resolution before lights.set_state."""

from __future__ import annotations

import json

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
            "id": "e1fb890c-5555-6666-7777-888888888888_1",
            "name": "Sofa",
            "room": "Woonkamer",
            "isOn": True,
        },
    ]


def _kantoor_two_lamps() -> list[dict]:
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
    lights = [_lights()[1]]
    assert pick_light_id(lights, "Woonkamer") == "e1fb890c-5555-6666-7777-888888888888_1"


def test_pick_light_id_kantoor_single_lamp() -> None:
    assert pick_light_id(_lights(), "Kantoor") == "e1fb890c-1111-2222-3333-444444444444_1"


def test_resolve_light_room_ambiguous() -> None:
    result = resolve_light(_kantoor_two_lamps(), "Kantoor")
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
        extract_room_hint,
        prefer_device_id_for_set_state,
        resolve_set_state_device_ids,
    )

    assert extract_lamp_name_hint("zet de ballon lamp aan") == "ballon"
    assert extract_lamp_name_hint("Turn off Ballon") == "Ballon"
    assert extract_room_all_hint("zet de woonkamer lampen aan") == "woonkamer"
    assert extract_lamp_name_hint("zet de woonkamer lampen aan") is None
    assert extract_room_hint("doe het licht aan in het kantoor") == "kantoor"
    assert prefer_device_id_for_set_state(
        "doe het licht aan in het kantoor",
        "e47ca80f-4d4c-4317-9e94-48ce765131d9_1",
    ) == "kantoor"
    assert prefer_device_id_for_set_state(
        "zet de ballon lamp aan",
        "e47ca80f-4d4c-4317-9e94-48ce765131d9_1",
    ) == "ballon"
    assert prefer_device_id_for_set_state(
        "zet de woonkamer lampen aan",
        "e47ca80f-4d4c-4317-9e94-48ce765131d9_1",
    ) == "room:woonkamer"
    assert prefer_device_id_for_set_state(
        "Turn on every light in the house",
        "e47ca80f-4d4c-4317-9e94-48ce765131d9_1",
    ) == "all:"
    # Model-invented all: without house-wide intent is rejected / overridden by lamp hint.
    assert prefer_device_id_for_set_state("Turn off Ballon", "all:").lower() == "ballon"
    assert prefer_device_id_for_set_state("hello", "all:") == ""


def test_house_wide_set_state_args_and_resolve() -> None:
    from brain.mcp.lights import (
        house_wide_set_state_args_from_user_message,
        light_set_state_args_from_user_message,
        lights_for_house_wide,
        resolve_set_state_device_ids,
    )

    assert house_wide_set_state_args_from_user_message(
        "Turn on every light in the house"
    ) == {"device_id": "all:", "on": True}
    assert light_set_state_args_from_user_message("doe alle lampen uit") == {
        "device_id": "all:",
        "on": False,
    }
    # Room plural is still room:, not all:
    assert light_set_state_args_from_user_message("zet de woonkamer lampen aan") == {
        "device_id": "room:woonkamer",
        "on": True,
    }
    lights = [
        {"id": "1", "name": "A", "room": "Kantoor", "reachable": True},
        {"id": "2", "name": "B", "room": "Woonkamer", "reachable": False},
        {"id": "3", "name": "C", "room": "Keuken"},  # missing reachable → includable
    ]
    ids, err = resolve_set_state_device_ids(lights, "all:")
    assert err is None
    assert ids == ["1", "3"]
    assert [x["id"] for x in lights_for_house_wide(lights)] == ["1", "3"]


def test_infer_light_on_from_user_message() -> None:
    from brain.mcp.lights import infer_light_on_from_user_message, light_set_state_args_from_user_message

    assert infer_light_on_from_user_message("zet de woonkamer lampen uit") is False
    assert infer_light_on_from_user_message("zet de ballon lamp aan") is True
    args = light_set_state_args_from_user_message("zet de woonkamer lampen uit")
    assert args == {"device_id": "room:woonkamer", "on": False}
    kantoor_args = light_set_state_args_from_user_message("doe het licht aan in het kantoor")
    assert kantoor_args == {"device_id": "kantoor", "on": True}


def test_doubled_article_typos_still_resolve() -> None:
    from brain.mcp.lights import (
        extract_lamp_name_hint,
        light_set_state_args_from_user_message,
        pick_light_id,
        resolve_light,
    )

    assert extract_lamp_name_hint("zet de de kantoor lamp aan") == "kantoor"
    assert light_set_state_args_from_user_message("zet de de kantoor lamp aan") == {
        "device_id": "kantoor",
        "on": True,
    }
    assert light_set_state_args_from_user_message("doe de de kantoor lamp uit") == {
        "device_id": "kantoor",
        "on": False,
    }


def test_stt_compound_and_fuzzy_room_resolve() -> None:
    from brain.mcp.lights import (
        extract_lamp_name_hint,
        light_set_state_args_from_user_message,
        pick_light_id,
        resolve_light,
        resolve_set_state_device_ids,
    )

    lights = [
        {"id": "k1", "name": "Ballon", "room": "Kantoor", "isOn": False},
        {"id": "w1", "name": "eettafel lamp", "room": "Woonkamer", "isOn": False},
    ]

    assert extract_lamp_name_hint("Zet de kantoorlamp aan.") == "kantoor"
    assert light_set_state_args_from_user_message("Zet de kantoorlamp aan.") == {
        "device_id": "kantoor",
        "on": True,
    }

    assert resolve_light(lights, "kantor").status == "found"
    assert pick_light_id(lights, "kantor") == "k1"
    ids, err = resolve_set_state_device_ids(lights, "kantor")
    assert err is None and ids == ["k1"]


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


def test_nl_en_room_aliases_resolve_to_hub_rooms() -> None:
    from brain.mcp.lights import (
        canonical_room_key,
        extract_room_all_hint,
        extract_room_hint,
        light_set_state_args_from_user_message,
        pick_light_id,
        prefer_device_id_for_set_state,
        resolve_light,
        resolve_set_state_device_ids,
    )

    assert canonical_room_key("living room") == canonical_room_key("Woonkamer")
    assert canonical_room_key("office") == canonical_room_key("Kantoor")
    assert canonical_room_key("kitchen") == "keuken"

    lights = [
        {"id": "w1", "name": "eettafel", "room": "Woonkamer", "isOn": True},
        {"id": "w2", "name": "paarse", "room": "Woonkamer", "isOn": True},
        {"id": "w3", "name": "bank", "room": "Woonkamer", "isOn": False},
        {"id": "k1", "name": "Ballon", "room": "Kantoor", "isOn": True},
    ]

    assert extract_room_all_hint("Turn off the living room lights") == "living room"
    assert prefer_device_id_for_set_state(
        "Turn off the living room lights",
        "e47ca80f-4d4c-4317-9e94-48ce765131d9_1",
    ) == "room:living room"
    assert light_set_state_args_from_user_message(
        "Turn on the living room lights"
    ) == {"device_id": "room:living room", "on": True}

    ids_nl, err_nl = resolve_set_state_device_ids(lights, "room:woonkamer")
    ids_en, err_en = resolve_set_state_device_ids(lights, "room:living room")
    assert err_nl is None and err_en is None
    assert set(ids_nl) == set(ids_en) == {"w1", "w2", "w3"}

    assert extract_room_hint("Turn off the office light") == "office"
    assert light_set_state_args_from_user_message("Turn off the office light") == {
        "device_id": "office",
        "on": False,
    }
    assert pick_light_id(lights, "office") == "k1"
    assert resolve_light(lights, "office").status == "found"
    assert resolve_light(lights, "office").device_id == "k1"


def test_alias_room_ambiguous_and_not_found() -> None:
    from brain.mcp.lights import resolve_light, resolve_set_state_device_ids

    two = _kantoor_two_lamps()
    assert resolve_light(two, "office").status == "ambiguous"
    ids, err = resolve_set_state_device_ids(two, "office")
    assert ids == [] and err is not None and err.startswith("ambiguous:")

    assert resolve_light(_lights(), "Garage").status == "not_found"
    ids2, err2 = resolve_set_state_device_ids(_lights(), "room:garage")
    assert ids2 == [] and err2 is not None


def test_present_lights_list_treats_unreachable_as_off() -> None:
    from brain.mcp.lights import (
        light_is_effectively_on,
        parse_lights_list,
        present_lights_list_json,
    )

    raw = json.dumps(
        [
            {"id": "1", "name": "Ballon", "room": "Kantoor", "isOn": False},
            {
                "id": "2",
                "name": "plafond",
                "room": "Keuken",
                "isOn": True,
                "reachable": False,
            },
        ]
    )
    assert not light_is_effectively_on(
        {"name": "plafond", "isOn": True, "reachable": False}
    )
    out = present_lights_list_json(raw)
    assert "all_off=true" in out
    assert out.startswith("Note:")
    parsed = parse_lights_list(out)
    assert parsed is not None and len(parsed) == 2

    on_raw = json.dumps(
        [
            {"id": "1", "name": "Ballon", "room": "Kantoor", "isOn": True},
            {
                "id": "2",
                "name": "plafond",
                "room": "Keuken",
                "isOn": True,
                "reachable": False,
            },
        ]
    )
    on_out = present_lights_list_json(on_raw)
    assert "all_off=false" in on_out
    assert "Ballon" in on_out
    assert "effectively_on_count=1" in on_out

    from brain.mcp.lights import (
        MESH_UNREACHABLE_ERROR,
        STALE_DEVICE_ID_ERROR,
        extract_set_state_error_message,
        format_set_state_failure_for_model,
        is_stale_device_id_error,
        present_lights_set_state_json,
    )

    raw = '{"success": false, "error": "Failed to reach Dirigera hub"}'
    out = present_lights_set_state_json(raw)
    assert out.startswith("Note:")
    assert "failed" in out.lower()
    assert "Failed to reach Dirigera hub" in out
    assert extract_set_state_error_message(out) == "Failed to reach Dirigera hub"
    assert "Failed to reach Dirigera hub" in format_set_state_failure_for_model(raw)
    assert "do not claim" in format_set_state_failure_for_model(
        '{"success": false}'
    ).lower()

    stale = f'{{"success": false, "error": "{STALE_DEVICE_ID_ERROR}"}}'
    assert is_stale_device_id_error(stale)
    assert not is_stale_device_id_error(
        f'{{"success": false, "error": "{MESH_UNREACHABLE_ERROR}"}}'
    )
    mesh_out = present_lights_set_state_json(
        f'{{"success": false, "error": "{MESH_UNREACHABLE_ERROR}"}}',
        light={"name": "Ballon", "room": "Kantoor"},
    )
    assert MESH_UNREACHABLE_ERROR in mesh_out
    assert "Ballon" in mesh_out


def test_t040_appearance_phrase_to_set_state_args() -> None:
    from brain.mcp.lights import (
        build_set_state_args_from_user_message,
        extract_lamp_name_hint,
        light_set_state_args_from_user_message,
    )

    assert extract_lamp_name_hint("Zet Ballon op 40%") == "Ballon"
    assert light_set_state_args_from_user_message("Zet Ballon op 40%") == {
        "device_id": "Ballon",
        "on": True,
        "brightness": 40,
    }
    assert light_set_state_args_from_user_message("Dim Ballon to 40%") == {
        "device_id": "Ballon",
        "on": True,
        "brightness": 40,
    }
    assert light_set_state_args_from_user_message("Make Ballon warm white") == {
        "device_id": "Ballon",
        "on": True,
        "color_temp_kelvin": 2700,
    }
    assert light_set_state_args_from_user_message("Turn Ballon to 2700K") == {
        "device_id": "Ballon",
        "on": True,
        "color_temp_kelvin": 2700,
    }
    assert light_set_state_args_from_user_message(
        "Turn Ballon to 4000 kelvin"
    ) == {
        "device_id": "Ballon",
        "on": True,
        "color_temp_kelvin": 4000,
    }
    assert light_set_state_args_from_user_message("Zet paarse lamp op rood") == {
        "device_id": "paarse",
        "on": True,
        "color_preset": "saturated_red",
    }
    assert light_set_state_args_from_user_message("Make paarse lamp red") == {
        "device_id": "paarse",
        "on": True,
        "color_preset": "saturated_red",
    }
    assert light_set_state_args_from_user_message("Make purple lamp red") == {
        "device_id": "purple",
        "on": True,
        "color_preset": "saturated_red",
    }
    assert light_set_state_args_from_user_message(
        "zet de woonkamer lampen op rood"
    ) == {
        "device_id": "room:woonkamer",
        "on": True,
        "color_preset": "saturated_red",
    }
    assert light_set_state_args_from_user_message(
        "maak de woonkamer lampen blauw"
    ) == {
        "device_id": "room:woonkamer",
        "on": True,
        "color_preset": "blue",
    }
    assert light_set_state_args_from_user_message(
        "make the living room lights blue"
    ) == {
        "device_id": "room:living room",
        "on": True,
        "color_preset": "blue",
    }
    assert light_set_state_args_from_user_message("Maak Ballon warm wit") == {
        "device_id": "Ballon",
        "on": True,
        "color_temp_kelvin": 2700,
    }
    assert light_set_state_args_from_user_message("Turn Ballon to cool white") == {
        "device_id": "Ballon",
        "on": True,
        "color_temp_kelvin": 4000,
    }
    # Colour-named lamp + on/off must not infer colour.
    assert light_set_state_args_from_user_message("Turn on purple lamp") == {
        "device_id": "purple",
        "on": True,
    }

    # Off strips appearance.
    assert light_set_state_args_from_user_message("Turn off Ballon to 40%") == {
        "device_id": "Ballon",
        "on": False,
    }

    # Warmth wins over colour when both present in the message.
    merged = build_set_state_args_from_user_message(
        "Make Ballon warm white",
        {"device_id": "Ballon", "color_preset": "saturated_red"},
    )
    assert merged.get("color_temp_kelvin") == 2700
    assert "color_preset" not in merged


def test_capability_error_for_light_explicit_false_only() -> None:
    from brain.mcp.lights import (
        DEVICE_NO_COLOUR_ERROR,
        DEVICE_NO_COLOR_TEMP_ERROR,
        capability_error_for_light,
    )

    no_colour = {"name": "Ballon", "supports_color": False, "supports_color_temp": True}
    assert (
        capability_error_for_light(no_colour, {"color_preset": "saturated_red"})
        == DEVICE_NO_COLOUR_ERROR
    )
    assert capability_error_for_light(no_colour, {"color_temp_kelvin": 2700}) is None
    # Missing supports_* → fall through (None).
    assert (
        capability_error_for_light({"name": "X"}, {"color_preset": "blue"}) is None
    )
    no_ct = {"supports_color_temp": False}
    assert (
        capability_error_for_light(no_ct, {"color_temp_kelvin": 2700})
        == DEVICE_NO_COLOR_TEMP_ERROR
    )