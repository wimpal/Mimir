"""Tests for party mode helpers (T-039)."""

from __future__ import annotations

from brain.mcp.party_mode import (
    build_party_mode_args_from_user_message,
    extract_party_duration_seconds,
    party_mode_tool_succeeded,
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
