"""Tests for T-052 capability discovery (live tools only)."""

from __future__ import annotations

from typing import Any

from brain.agent import StoppedReason, run_turn
from brain.capability_discovery import (
    all_overview_tool_names,
    build_capability_explain,
    build_capability_overview,
    build_capability_reply,
    capability_locale,
    extract_explain_target,
    is_capability_explain,
    is_capability_overview,
    live_capability_entries,
    probe_unavailable_services,
    should_short_circuit_capability,
)
from brain.config import McpServiceSettings, Settings
from brain.mcp.write_guard import user_message_requests_write
from brain.morning_brief import is_morning_greeting
from brain.ollama import ChatMessage, ChatResponse
from brain.recipe_import import user_message_requests_recipe_save
from brain.tools import Tool


def _tool(
    name: str,
    *,
    description: str = "A test tool.",
    service: str | None = None,
    required: list[str] | None = None,
) -> Tool:
    props = {k: {"type": "string"} for k in (required or [])}
    return Tool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": props,
            "required": list(required or []),
            "additionalProperties": False,
        },
        execute=lambda **_kwargs: "{}",
        service=service,
    )


def _registry() -> dict[str, Tool]:
    return {
        "echo": _tool("echo", description="Echo text."),
        "get_server_time": _tool("get_server_time"),
        "get_weather": _tool("get_weather", description="Current weather."),
        "get_calendar": _tool("get_calendar", description="Today's calendar."),
        "web.fetch": _tool("web.fetch", description="Fetch a URL."),
        "homebase.shopping_list.list": _tool(
            "homebase.shopping_list.list",
            description="List shopping items.",
            service="homebase",
        ),
        "homebase.lights.list": _tool(
            "homebase.lights.list",
            description="List lights.",
            service="homebase",
        ),
        "homebase.recipes.search": _tool(
            "homebase.recipes.search",
            description="Search recipes.",
            service="homebase",
        ),
        "homebase.changes.list": _tool(
            "homebase.changes.list",
            description="List Homebase audit changes.",
            service="homebase",
        ),
        "budgettracker.transactions.search": _tool(
            "budgettracker.transactions.search",
            description="Search expenses.",
            service="budgettracker",
            required=["query"],
        ),
        "budgettracker.summary.by_category": _tool(
            "budgettracker.summary.by_category",
            description="Spend per category.",
            service="budgettracker",
        ),
        "budgettracker.changes.list": _tool(
            "budgettracker.changes.list",
            description="List BudgetTracker audit changes.",
            service="budgettracker",
        ),
    }


def test_overview_intent_en_nl() -> None:
    assert is_capability_overview("What can you do?")
    assert is_capability_overview("what can you do")
    assert is_capability_overview("Wat kun je?")
    assert is_capability_overview("wat kun je doen")
    assert is_capability_overview("welke tools heb je")
    assert not is_capability_overview("what can you do and what's the weather")
    assert not is_capability_overview("good morning")


def test_explain_intent_and_locale() -> None:
    assert is_capability_explain("How does shopping list work?")
    assert extract_explain_target("How does shopping list work?") == "shopping list"
    assert extract_explain_target("Hoe werkt de boodschappenlijst?") == "boodschappenlijst"
    assert capability_locale("Wat kun je?") == "nl"
    assert capability_locale("What can you do?") == "en"


def test_overview_provenance_subset_of_registry() -> None:
    tools = _registry()
    entries = live_capability_entries(tools)
    names = all_overview_tool_names(entries)
    assert names <= set(tools)
    assert "echo" not in names
    assert "web.fetch" not in names
    assert "homebase.shopping_list.list" in names
    assert "get_weather" in names


def test_overview_omits_audit_changes_and_labels_summary() -> None:
    tools = _registry()
    reply = build_capability_overview(locale="nl", tools=tools)
    assert "uitgaven per categorie" in reply
    assert "weer" in reply
    # Audit/undo clusters stay out of the brochure.
    assert "wijzigingsgeschiedenis" not in reply
    assert "\n- changes\n" not in f"\n{reply}\n"
    assert "\n- summary\n" not in f"\n{reply}\n"
    names = all_overview_tool_names(live_capability_entries(tools))
    assert "homebase.changes.list" not in names
    assert "budgettracker.changes.list" not in names
    assert "budgettracker.summary.by_category" in names
    # Sensible order: weather before shopping before expenses.
    assert reply.index("weer") < reply.index("boodschappenlijsten")
    assert reply.index("boodschappenlijsten") < reply.index("uitgaven")


def test_overview_omits_down_service_and_parked_words() -> None:
    tools = _registry()
    reply = build_capability_overview(
        locale="en",
        tools=tools,
        unavailable=["budgettracker"],
        configured_services=["homebase", "budgettracker"],
    )
    assert "shopping" in reply.lower()
    assert "lights" in reply.lower()
    assert "recipes" in reply.lower()
    assert "BudgetTracker" in reply
    assert "unreachable" in reply.lower()
    assert "expenses" not in reply.split("Not available:")[0].lower()
    for banned in ("Personality", "Council", "wake-word", "M6b", "FUTURE"):
        assert banned not in reply

    names = all_overview_tool_names(
        live_capability_entries(tools, unavailable=["budgettracker"])
    )
    assert "budgettracker.transactions.search" not in names


def test_overview_nl() -> None:
    reply = build_capability_overview(
        locale="nl",
        tools=_registry(),
        unavailable=["homebase"],
        configured_services=["homebase", "budgettracker"],
    )
    assert "Dit kan ik nu" in reply
    assert "weer" in reply
    assert "Homebase" in reply
    assert "niet bereikbaar" in reply
    assert "boodschappen" in reply.lower() or "lampen" in reply.lower()


def test_explain_live_tool_no_json_dump() -> None:
    tools = _registry()
    reply = build_capability_explain(
        locale="en",
        tools=tools,
        phrase="shopping list",
        unavailable=[],
    )
    assert "homebase.shopping_list.list" in reply
    assert "{" not in reply
    assert "shopping" in reply.lower()

    nl = build_capability_explain(
        locale="nl",
        tools=tools,
        phrase="boodschappenlijst",
        unavailable=[],
    )
    assert "homebase.shopping_list.list" in nl
    assert "{" not in nl


def test_explain_down_service_and_unknown() -> None:
    tools = {
        "get_weather": _tool("get_weather", description="Weather."),
    }
    down = build_capability_explain(
        locale="en",
        tools=tools,
        phrase="shopping list",
        unavailable=["homebase"],
    )
    assert "unreachable" in down.lower()
    assert "Homebase" in down

    unknown = build_capability_explain(
        locale="en",
        tools=tools,
        phrase="personality council",
        unavailable=[],
    )
    assert "don't have a live tool" in unknown.lower()
    assert "Personality" not in unknown or "don't have" in unknown.lower()


def test_should_short_circuit_precedence() -> None:
    assert should_short_circuit_capability(
        "What can you do?",
        is_morning=is_morning_greeting,
        requests_write=user_message_requests_write,
        requests_recipe_save=user_message_requests_recipe_save,
    )
    assert not should_short_circuit_capability(
        "Good morning",
        is_morning=is_morning_greeting,
        requests_write=user_message_requests_write,
        requests_recipe_save=user_message_requests_recipe_save,
    )
    assert not should_short_circuit_capability(
        "What can you do?",
        pending_confirmable=True,
        is_morning=is_morning_greeting,
        requests_write=user_message_requests_write,
        requests_recipe_save=user_message_requests_recipe_save,
    )
    assert not should_short_circuit_capability(
        "Add milk to the shopping list",
        is_morning=is_morning_greeting,
        requests_write=user_message_requests_write,
        requests_recipe_save=user_message_requests_recipe_save,
    )
    assert not should_short_circuit_capability(
        "Repeat that",
        is_repeat=lambda t: t.strip().lower().startswith("repeat that"),
        is_morning=is_morning_greeting,
        requests_write=user_message_requests_write,
        requests_recipe_save=user_message_requests_recipe_save,
    )


def test_probe_unavailable_uses_health_status() -> None:
    settings = Settings(
        location={
            "latitude": 52.0,
            "longitude": 5.0,
            "timezone": "Europe/Amsterdam",
        },
        services={
            "homebase": McpServiceSettings(
                host="127.0.0.1",
                port=3000,
                enabled=True,
                token="test-token",
            ),
            "budgettracker": McpServiceSettings(
                host="127.0.0.1",
                port=8080,
                enabled=True,
                token="test-token",
            ),
        },
    )

    def probe(service_id: str, _svc: McpServiceSettings) -> bool:
        return service_id == "homebase"

    down = probe_unavailable_services(settings, health_probe=probe)
    assert down == ["budgettracker"]


class _FakeClient:
    def chat(self, *_args: Any, **_kwargs: Any) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="I can do Personality and Council forever.",
            )
        )


def test_run_turn_short_circuits_capability() -> None:
    tools = _registry()
    result = run_turn(
        _FakeClient(),
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="What can you do?"),
        ],
        tools=tools,
        unavailable_services=["budgettracker"],
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert result.steps and result.steps[0].anomaly == "capability_discovery"
    assert "Personality" not in result.content
    assert "Council" not in result.content
    assert "shopping" in result.content.lower()
    assert "BudgetTracker" in result.content


def test_run_turn_explain_short_circuit() -> None:
    result = run_turn(
        _FakeClient(),
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="How does shopping list work?"),
        ],
        tools=_registry(),
    )
    assert result.steps[0].anomaly == "capability_discovery"
    assert "homebase.shopping_list.list" in result.content
    assert "{" not in result.content


def test_run_turn_morning_not_swallowed() -> None:
    """Morning greeting must reach the normal loop, not capability short-circuit."""

    class TrackingClient(_FakeClient):
        called = False

        def chat(self, *_args: Any, **_kwargs: Any) -> ChatResponse:
            type(self).called = True
            return ChatResponse(
                message=ChatMessage(role="assistant", content="Good morning.")
            )

    TrackingClient.called = False
    result = run_turn(
        TrackingClient(),
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="Good morning"),
        ],
        tools={"get_weather": _tool("get_weather"), "get_calendar": _tool("get_calendar")},
        max_iterations=1,
    )
    assert TrackingClient.called
    assert not any(s.anomaly == "capability_discovery" for s in result.steps)


def test_build_capability_reply_none_for_unrelated() -> None:
    assert build_capability_reply("what's the weather", tools=_registry()) is None
