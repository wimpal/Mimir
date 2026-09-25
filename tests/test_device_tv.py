"""T-112 / T-113 TV / SSAP phrase + resolve smoke (extend like test_device_wake.py)."""

from __future__ import annotations

from brain.device_inventory import (
    DEVICE_GO_HOME_TOOL,
    DEVICE_POWER_OFF_TOOL,
    build_device_confirm_reply,
    device_dispatch_args,
    device_locale_dutch,
    user_message_requests_device_go_home,
    user_message_requests_device_hdmi,
    user_message_requests_device_jellyfin,
    user_message_requests_device_power_off,
)
from brain.mcp.devices import resolve_tv_device_id


def test_go_home_phrases():
    assert user_message_requests_device_go_home("Open Home on the TV")
    assert user_message_requests_device_go_home("TV naar Home")
    assert user_message_requests_device_go_home("switch the tv to home")
    assert user_message_requests_device_go_home("Switch the TV to Home")
    assert user_message_requests_device_go_home("zet de tv op home")
    assert user_message_requests_device_go_home("wake the tv on home")
    assert user_message_requests_device_go_home("wake the TV")
    assert user_message_requests_device_go_home("wek de tv")
    assert user_message_requests_device_go_home("turn on the TV")
    assert user_message_requests_device_go_home("turn the tv on")
    assert user_message_requests_device_go_home("Turn the TV on")
    assert not user_message_requests_device_go_home("wake the NAS")
    assert not user_message_requests_device_go_home("don't turn the TV on")
    assert not user_message_requests_device_go_home("turn the tv off")


def test_power_off_phrases():
    assert user_message_requests_device_power_off("turn off the TV")
    assert user_message_requests_device_power_off("Turn off the television")
    assert user_message_requests_device_power_off("turn the tv off")
    assert user_message_requests_device_power_off("Turn the TV off")
    assert user_message_requests_device_power_off("switch the TV off")
    assert user_message_requests_device_power_off("TV uit")
    assert user_message_requests_device_power_off("zet de TV uit")
    assert user_message_requests_device_power_off("Zet de televisie uit")
    assert not user_message_requests_device_power_off("turn off the lights")
    assert not user_message_requests_device_power_off("turn off Ballon")
    assert not user_message_requests_device_power_off("Good night")
    assert not user_message_requests_device_power_off("Welterusten")
    assert not user_message_requests_device_power_off("don't turn off the TV")
    assert not user_message_requests_device_power_off("don't turn the TV off")
    assert not user_message_requests_device_power_off("is the TV off?")
    assert user_message_requests_device_power_off("Good night, turn off the TV")


def test_power_off_confirm_dutch():
    assert device_locale_dutch("TV uit") is True
    reply = build_device_confirm_reply(
        {"name": "OLED"},
        DEVICE_POWER_OFF_TOOL,
        user_message="TV uit",
        dutch=True,
    )
    assert "uitzetten" in reply.lower()
    assert "Confirm" in reply or "ja" in reply.lower()


def test_go_home_intent_overrides_set_input_live_tv() -> None:
    """Model inventing set_input=live_tv must still stage go_home."""
    import json

    from brain.agent import StoppedReason, run_turn
    from brain.device_inventory import (
        DEVICE_SET_INPUT_TOOL,
        PendingDeviceStore,
    )
    from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
    from brain.tools import Tool

    class _ScriptedClient:
        def __init__(self, responses: list[ChatMessage]) -> None:
            self._responses = list(responses)

        def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
            return ChatResponse(message=self._responses.pop(0))

    store = PendingDeviceStore()
    cid = "conv-home-remap"
    registry = {
        "homebase.devices.list": Tool(
            name="homebase.devices.list",
            description="list",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: json.dumps(
                [{"id": "c1tv", "name": "TV", "tv_capable": True}]
            ),
            service="homebase",
        ),
        DEVICE_SET_INPUT_TOOL: Tool(
            name=DEVICE_SET_INPUT_TOOL,
            description="input",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: '{"ok":true}',
            service="homebase",
        ),
        DEVICE_GO_HOME_TOOL: Tool(
            name=DEVICE_GO_HOME_TOOL,
            description="home",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: '{"ok":true}',
            service="homebase",
        ),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.devices.list", arguments={}
                        )
                    )
                ],
            ),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name=DEVICE_SET_INPUT_TOOL,
                            arguments={
                                "device_id": "c1tv",
                                "input": "live_tv",
                            },
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="should not run"),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="switch the tv to home")],
        tools=registry,
        conversation_id=cid,
        pending_devices=store,
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert store.get_tool_name(cid) == DEVICE_GO_HOME_TOOL
    content = (result.content or "").lower()
    assert "home" in content
    assert "live_tv" not in content


def test_power_off_remaps_lights_set_state() -> None:
    """Model inventing lights.set_state must still stage power_off."""
    import json

    from brain.agent import StoppedReason, run_turn
    from brain.device_inventory import PendingDeviceStore
    from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
    from brain.tools import Tool

    class _ScriptedClient:
        def __init__(self, responses: list[ChatMessage]) -> None:
            self._responses = list(responses)

        def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
            return ChatResponse(message=self._responses.pop(0))

    store = PendingDeviceStore()
    cid = "conv-power-off-remap"
    registry = {
        "homebase.devices.list": Tool(
            name="homebase.devices.list",
            description="list",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: json.dumps(
                [{"id": "c1tv", "name": "TV", "tv_capable": True}]
            ),
            service="homebase",
        ),
        "homebase.lights.set_state": Tool(
            name="homebase.lights.set_state",
            description="lights",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: '{"success":true}',
            service="homebase",
        ),
        DEVICE_POWER_OFF_TOOL: Tool(
            name=DEVICE_POWER_OFF_TOOL,
            description="off",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: '{"ok":true}',
            service="homebase",
        ),
        DEVICE_GO_HOME_TOOL: Tool(
            name=DEVICE_GO_HOME_TOOL,
            description="home",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: '{"ok":true}',
            service="homebase",
        ),
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.devices.list", arguments={}
                        )
                    )
                ],
            ),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.lights.set_state",
                            arguments={"device_id": "c1tv", "on": False},
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="should not run"),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="turn off the TV")],
        tools=registry,
        conversation_id=cid,
        pending_devices=store,
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert store.get_tool_name(cid) == DEVICE_POWER_OFF_TOOL
    content = (result.content or "").lower()
    assert "off" in content or "uit" in content


def test_jellyfin_phrases():
    assert user_message_requests_device_jellyfin("Start Jellyfin on the TV")


def test_hdmi_phrases_map_console():
    assert user_message_requests_device_hdmi("Switch the TV to HDMI 1")
    assert user_message_requests_device_hdmi("PlayStation on the TV")


def test_resolve_prefers_tv_capable():
    devices = [
        {"id": "a", "name": "OLED", "tv_capable": False},
        {"id": "b", "name": "OLED", "tv_capable": True},
    ]
    assert resolve_tv_device_id(devices, "OLED") == "b"


def test_dispatch_strips_secrets():
    args = device_dispatch_args(
        "homebase.devices.go_home",
        {
            "device_id": "c123",
            "name": "OLED",
            "client_key": "secret",
            "wake_if_needed": True,
        },
    )
    assert args == {"device_id": "c123", "wake_if_needed": True}


def test_power_off_dispatch_device_id_only():
    args = device_dispatch_args(
        DEVICE_POWER_OFF_TOOL,
        {
            "device_id": "c123",
            "name": "OLED",
            "client_key": "secret",
            "wake_if_needed": True,
            "target": "home",
            "input": "hdmi1",
        },
    )
    assert args == {"device_id": "c123"}


def test_tv_success_copy_not_enroll():
    from brain.device_inventory import (
        DEVICE_SET_INPUT_TOOL,
        build_device_success_reply,
    )

    home = build_device_success_reply(
        {"name": "TV"}, DEVICE_GO_HOME_TOOL, dutch=False
    )
    assert "Home" in home
    assert "enrolled" not in home.lower()
    hdmi = build_device_success_reply(
        {"name": "TV", "input": "hdmi1"}, DEVICE_SET_INPUT_TOOL, dutch=False
    )
    assert "hdmi1" in hdmi
    assert "enrolled" not in hdmi.lower()
    off = build_device_success_reply(
        {"name": "TV"}, DEVICE_POWER_OFF_TOOL, dutch=False
    )
    assert "off" in off.lower()
    assert "enrolled" not in off.lower()
