"""T-101 Wake-on-LAN write-guard + resolve."""

from __future__ import annotations

from brain.device_inventory import (
    DEVICE_WAKE_TOOL,
    build_device_confirm_reply,
    may_stage_device_wake,
    user_message_requests_device_wake,
    user_message_requests_device_write,
)
from brain.mcp.devices import resolve_wake_device_id
from brain.mcp.write_guard import check_write_allowed, user_message_requests_write


def test_wake_phrases() -> None:
    assert user_message_requests_device_wake("Wake the NAS")
    assert user_message_requests_device_wake("Wek de NAS")
    assert user_message_requests_device_wake("Wake the TV")
    assert user_message_requests_write("Wake the NAS")
    assert not user_message_requests_device_wake("Turn off the office light")
    assert not user_message_requests_device_wake("Which lights are on?")


def test_wake_pending_unlock() -> None:
    assert check_write_allowed(DEVICE_WAKE_TOOL, "hello") is not None
    assert (
        check_write_allowed(DEVICE_WAKE_TOOL, "yes", device_pending=True) is None
    )
    assert (
        check_write_allowed(DEVICE_WAKE_TOOL, "ja", device_pending=True) is None
    )


def test_may_stage_wake_and_confirm() -> None:
    assert may_stage_device_wake("Wake the NAS", has_pending=False)
    assert not may_stage_device_wake("yes", has_pending=True)
    reply = build_device_confirm_reply(
        {"name": "NAS", "device_id": "c123"}, DEVICE_WAKE_TOOL, dutch=False
    )
    assert "Wake" in reply
    assert "Confirm" in reply or "yes" in reply.lower()


def test_device_dispatch_args_strips_wake_name() -> None:
    from brain.device_inventory import device_dispatch_args

    assert device_dispatch_args(
        DEVICE_WAKE_TOOL, {"device_id": "c1", "name": "TV"}
    ) == {"device_id": "c1"}
    assert device_dispatch_args(
        "homebase.devices.add", {"name": "NAS", "type": "pc"}
    ) == {"name": "NAS", "type": "pc"}


def test_wake_confirm_dispatches_device_id_only() -> None:
    """Staged name is for confirm copy; MCP wake must not receive it."""
    import json

    from brain.agent import StoppedReason, run_turn
    from brain.device_inventory import PendingDeviceStore
    from brain.ollama import ChatMessage, ChatResponse
    from brain.tools import Tool

    class _ScriptedClient:
        def __init__(self, responses: list[ChatMessage]) -> None:
            self._responses = list(responses)

        def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
            return ChatResponse(message=self._responses.pop(0))

    wake_calls: list[dict] = []

    def wake_execute(**kwargs: object) -> str:
        wake_calls.append(dict(kwargs))
        return json.dumps({"status": "sent", "name": "TV", "id": "c1"})

    store = PendingDeviceStore()
    cid = "conv-wake-confirm"
    store.set(
        cid,
        {"device_id": "c1", "name": "TV"},
        tool_name=DEVICE_WAKE_TOOL,
    )
    result = run_turn(
        _ScriptedClient([ChatMessage(role="assistant", content="should not run")]),
        [ChatMessage(role="user", content="yes")],
        tools={
            DEVICE_WAKE_TOOL: Tool(
                name=DEVICE_WAKE_TOOL,
                description="wake",
                parameters={
                    "type": "object",
                    "properties": {"device_id": {"type": "string"}},
                    "required": ["device_id"],
                },
                execute=wake_execute,
                service="homebase",
            )
        },
        conversation_id=cid,
        pending_devices=store,
        max_iterations=2,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert wake_calls == [{"device_id": "c1"}]
    assert "TV" in (result.content or "")
    assert not store.has(cid)


def test_resolve_wake_device_id() -> None:
    devices = [
        {"id": "c1", "name": "NAS", "wake_capable": True},
        {"id": "c2", "name": "Office PC", "wake_capable": False},
    ]
    assert resolve_wake_device_id(devices, "NAS") == "c1"
    assert resolve_wake_device_id(devices, "c1") == "c1"
    assert resolve_wake_device_id(devices, "missing") is None
    assert resolve_wake_device_id(devices, "Office") == "c2"


def test_wake_blocks_lights_set_state() -> None:
    err = check_write_allowed("homebase.lights.set_state", "Wake the tv")
    assert err is not None
    assert "devices.wake" in err
    err2 = check_write_allowed(
        "homebase.lights.set_state", "Wake the tv in the woonkamer"
    )
    assert err2 is not None
    assert check_write_allowed(DEVICE_WAKE_TOOL, "Wake the tv") is None


def test_wake_stage_forces_confirm_not_max_iterations() -> None:
    """list → wake must return confirm copy (recipe-style), not burn max_iterations.

    Uses NAS (not TV): T-112 remaps bare \"wake the TV\" to go_home + wake_if_needed.
    """
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

    wake_calls: list[dict] = []

    def list_execute(**_: object) -> str:
        return json.dumps(
            [
                {
                    "id": "c1naswake0000000000001",
                    "name": "NAS",
                    "wake_capable": True,
                }
            ]
        )

    def wake_execute(**kwargs: object) -> str:
        wake_calls.append(dict(kwargs))
        return json.dumps({"status": "sent", "name": "NAS"})

    registry = {
        "homebase.devices.list": Tool(
            name="homebase.devices.list",
            description="list",
            parameters={"type": "object", "properties": {}},
            execute=list_execute,
            service="homebase",
        ),
        DEVICE_WAKE_TOOL: Tool(
            name=DEVICE_WAKE_TOOL,
            description="wake",
            parameters={"type": "object", "properties": {}},
            execute=wake_execute,
            service="homebase",
        ),
    }
    store = PendingDeviceStore()
    cid = "conv-wake-1"
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.devices.list",
                            arguments={},
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
                            name=DEVICE_WAKE_TOOL,
                            arguments={"device_id": "c1naswake0000000000001"},
                        )
                    )
                ],
            ),
            # Would be a 3rd Ollama round if confirm were not forced — must not be used.
            ChatMessage(role="assistant", content="I woke the NAS."),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="wake the NAS")],
        tools=registry,
        conversation_id=cid,
        pending_devices=store,
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert wake_calls == []
    assert store.has(cid)
    assert store.get_tool_name(cid) == DEVICE_WAKE_TOOL
    content = result.content or ""
    assert "Wake" in content or "wakker" in content.lower()
    assert "not done" in content.lower() or "nog niet" in content.lower()
    assert "NAS" in content
    assert any(s.anomaly == "device_confirm_forced" for s in result.steps)
    assert len(client._responses) == 1  # third Ollama reply unused
