"""Tests for party mode helpers (T-039 / T-041)."""

from __future__ import annotations

from brain.mcp.party_mode import (
    LightsWriteIntent,
    build_party_mode_args_from_user_message,
    classify_lights_write_intent,
    extract_party_duration_seconds,
    party_mode_disallowed_this_turn,
    party_mode_tool_succeeded,
    should_reroute_party_to_house_wide,
    user_message_refuses_party_mode,
    user_message_requests_house_wide_lights,
    user_message_requests_party_mode,
)


def test_party_mode_phrases() -> None:
    assert user_message_requests_party_mode("Party mode!")
    assert user_message_requests_party_mode("Let's party")
    assert user_message_requests_party_mode("feest")
    assert user_message_requests_party_mode("party tijd")
    assert user_message_requests_party_mode("feestmodus")
    assert user_message_requests_party_mode("disco mode")
    assert not user_message_requests_party_mode("Which lights are on?")
    assert not user_message_requests_party_mode("Turn on every light in the house")
    assert not user_message_requests_party_mode("doe alle lampen aan")


def test_house_wide_phrases() -> None:
    assert user_message_requests_house_wide_lights("Turn on every light in the house")
    assert user_message_requests_house_wide_lights("turn off all the lights")
    assert user_message_requests_house_wide_lights("doe alle lampen aan")
    assert user_message_requests_house_wide_lights("alle lichten uit")
    assert not user_message_requests_house_wide_lights("zet de woonkamer lampen aan")
    assert not user_message_requests_house_wide_lights("turn on the living room lights")
    assert not user_message_requests_house_wide_lights(
        "Turn on all lights in the living room"
    )
    assert not user_message_requests_house_wide_lights("Are all the lights in the house on?")
    assert not user_message_requests_house_wide_lights("Don't turn on all the lights")
    assert not user_message_requests_house_wide_lights("Party mode!")
    assert not user_message_requests_house_wide_lights("yes")
    assert not user_message_requests_house_wide_lights("ja")


def test_refusal_phrases() -> None:
    assert user_message_refuses_party_mode("No, not party mode")
    assert user_message_refuses_party_mode("No party mode")
    assert user_message_refuses_party_mode("Don't use party mode")
    assert user_message_refuses_party_mode(
        "No, not party mode. Simply turn on all of the lights."
    )
    assert user_message_refuses_party_mode("geen feest")
    assert not user_message_refuses_party_mode("Party mode!")
    assert not user_message_refuses_party_mode("yes")


def test_classify_lights_write_intent_precedence() -> None:
    assert (
        classify_lights_write_intent(
            "No, not party mode. Simply turn on all of the lights."
        )
        == LightsWriteIntent.REFUSED_PARTY
    )
    assert classify_lights_write_intent("Party mode!") == LightsWriteIntent.PARTY
    assert (
        classify_lights_write_intent("Turn on every light in the house")
        == LightsWriteIntent.HOUSE_WIDE
    )
    assert classify_lights_write_intent("yes") == LightsWriteIntent.OTHER
    # Explicit party mentioning lights stays party (not house-wide).
    assert (
        classify_lights_write_intent("put all lights in party mode")
        == LightsWriteIntent.PARTY
    )


def test_party_mode_disallowed_and_reroute() -> None:
    assert party_mode_disallowed_this_turn("Turn on every light in the house")
    assert should_reroute_party_to_house_wide("Turn on every light in the house")
    assert party_mode_disallowed_this_turn(
        "No, not party mode. Simply turn on all of the lights."
    )
    assert should_reroute_party_to_house_wide(
        "No, not party mode. Simply turn on all of the lights."
    )
    assert not party_mode_disallowed_this_turn("Party mode!")
    assert not should_reroute_party_to_house_wide("Party mode!")
    # Ordinary lamp toggles must not allow party_mode.
    assert party_mode_disallowed_this_turn("Turn off Ballon")
    assert not should_reroute_party_to_house_wide("Turn off Ballon")
    assert party_mode_disallowed_this_turn("yes")
    assert not should_reroute_party_to_house_wide("yes")


def test_extract_party_duration_seconds() -> None:
    assert extract_party_duration_seconds("30 second party") == 30
    assert extract_party_duration_seconds("party for 45 seconden") == 45
    assert extract_party_duration_seconds("90 sec party") == 60
    assert extract_party_duration_seconds("120 second party") == 60
    assert extract_party_duration_seconds("party mode") is None


def test_build_party_mode_args_clamps_120_second_party() -> None:
    args = build_party_mode_args_from_user_message("120 second party", {})
    assert args == {"duration_seconds": 60}


def test_build_party_mode_args_from_user_message() -> None:
    args = build_party_mode_args_from_user_message("30 second party", {})
    assert args == {"duration_seconds": 30}
    args_clamp = build_party_mode_args_from_user_message("", {"duration_seconds": 90})
    assert args_clamp == {"duration_seconds": 60}
    args_model = build_party_mode_args_from_user_message("party mode", {"duration_seconds": 20})
    assert args_model == {"duration_seconds": 20}


def test_party_mode_tool_succeeded() -> None:
    assert party_mode_tool_succeeded('{"success": true, "devices_affected": 3}')
    assert not party_mode_tool_succeeded('{"success": false, "error": "busy"}')
    assert not party_mode_tool_succeeded("error: timeout")
