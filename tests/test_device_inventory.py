"""T-108 Network device write-guard + staging."""

from __future__ import annotations

from brain.device_inventory import (
    DEVICE_ADD_TOOL,
    PendingDeviceStore,
    awaiting_device_confirmation_result,
    build_device_confirm_reply,
    may_stage_device_add,
    user_message_requests_device_add,
    user_message_requests_device_remove,
    user_message_requests_device_update,
    user_message_requests_device_write,
)
from brain.mcp.write_guard import check_write_allowed, user_message_requests_write


def test_device_add_phrases_request_write() -> None:
    assert user_message_requests_device_add("Add the NAS in the office")
    assert user_message_requests_device_add("Voeg de NAS toe in kantoor")
    assert user_message_requests_write("Enroll my laptop")
    assert not user_message_requests_device_add("Turn off the office light")
    assert not user_message_requests_device_write("Which lights are on?")


def test_device_update_remove_phrases() -> None:
    assert user_message_requests_device_update("Move the NAS to the living room")
    assert user_message_requests_device_update("Verplaats de NAS naar de woonkamer")
    assert user_message_requests_device_remove("Retire the old router")
    assert user_message_requests_device_remove("Verwijder de oude router")


def test_check_write_allowed_device_pending() -> None:
    err = check_write_allowed(DEVICE_ADD_TOOL, "hello")
    assert err is not None
    assert check_write_allowed(
        DEVICE_ADD_TOOL, "yes", device_pending=True
    ) is None
    assert check_write_allowed(
        "homebase.devices.remove", "ja", device_pending=True
    ) is None


def test_may_stage_and_confirm_copy() -> None:
    assert may_stage_device_add("Add the NAS in the office", has_pending=False)
    assert not may_stage_device_add("yes", has_pending=True)
    payload = {"name": "NAS", "type": "nas", "location": "office"}
    store = PendingDeviceStore()
    store.set("c1", payload, dutch=False, tool_name=DEVICE_ADD_TOOL)
    assert store.is_confirmable("c1")
    reply = build_device_confirm_reply(payload, DEVICE_ADD_TOOL, dutch=False)
    assert "Confirm" in reply or "yes" in reply.lower()
    assert "awaiting_confirmation" in awaiting_device_confirmation_result(
        payload, DEVICE_ADD_TOOL
    )


def test_lights_must_not_route_to_devices() -> None:
    assert not user_message_requests_device_add("Turn off Ballon")
    assert not user_message_requests_device_add("Doe het licht uit in kantoor")
