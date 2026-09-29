"""Tool-schema gating for chat-context turns (T-057)."""

from __future__ import annotations

from typing import Any

from brain.agent import run_turn
from brain.ollama import ChatMessage, ChatResponse
from brain.tool_gate import should_offer_tools
from brain.tools import Tool


def test_should_offer_tools_chat_facts() -> None:
    assert not should_offer_tools(
        "Onthoud voor deze chat: codewoord=zebra; plant=Mona; sleutel=blauwe schaal."
    )
    assert not should_offer_tools("Nog iets: het project heet Heim.")
    assert not should_offer_tools("Herhaal de drie feiten en de projectnaam.")
    assert not should_offer_tools("Dank je.")
    assert should_offer_tools("Wat is het weer vandaag?")
    assert should_offer_tools("Zet de lamp in de keuken aan")
    assert should_offer_tools("Onthoud: zet het licht in de woonkamer uit")
    assert should_offer_tools("How busy have you been?")
    assert should_offer_tools("Hoe druk ben je geweest?")
    assert should_offer_tools("Show me usage stats")
    assert should_offer_tools("Where's my package?")
    assert should_offer_tools("Waar staat mijn pakket?")
    assert should_offer_tools("Add a delivery: Amazon order")
    assert should_offer_tools("Where's the fuse box?")
    assert should_offer_tools("Waar zit de meterkast?")
    assert should_offer_tools("Waar staat de meterkast?")
    assert should_offer_tools("Add a note: guest parking is behind the shed")
    assert should_offer_tools("Maak een notitie: meterstand")
    assert should_offer_tools("Welke notities?")
    assert should_offer_tools("List notes")
    assert should_offer_tools("Where's the NAS?")
    assert should_offer_tools("Where's my package?")  # delivery still toolish
    # Chat-memory "waar staat" drills stay off tools when no live noun matches.
    assert not should_offer_tools("Waar staat de plant?")

def test_run_turn_omits_tools_for_chat_recall() -> None:
    calls: list[dict[str, Any]] = []

    class Client:
        def chat(
            self,
            messages: list[ChatMessage],
            tools: list[dict[str, Any]] | None = None,
            *,
            think: bool = False,
            stream: bool = False,
        ) -> ChatResponse:
            calls.append({"tools": tools, "think": think})
            return ChatResponse(
                message=ChatMessage(
                    role="assistant",
                    content="zebra, Mona, blauwe schaal, Heim",
                )
            )

    echo = Tool(
        name="echo",
        description="echo",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=lambda **_: "x",
    )
    result = run_turn(
        Client(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="You are Mimir."),
            ChatMessage(
                role="user",
                content="Onthoud voor deze chat: codewoord=zebra; plant=Mona.",
            ),
            ChatMessage(
                role="assistant",
                content="Ok: zebra, Mona.",
            ),
            ChatMessage(
                role="user",
                content="Herhaal de drie feiten en de projectnaam.",
            ),
        ],
        tools={"echo": echo},
        max_iterations=1,
    )
    assert calls
    assert calls[0]["tools"] in (None, [])
    assert "zebra" in (result.content or "")


def test_echo_exact_and_modes_surface() -> None:
    from brain.tool_gate import (
        filter_schemas_for_turn,
        is_echo_exact_intent,
        is_non_live_modes_turn,
        should_offer_tools,
    )

    assert is_echo_exact_intent("Echo exactly: no-weather-42")
    assert should_offer_tools("Echo exactly: no-weather-42")
    schemas = [
        {"type": "function", "function": {"name": "echo"}},
        {"type": "function", "function": {"name": "get_weather"}},
    ]
    filtered = filter_schemas_for_turn(schemas, "Echo exactly: no-weather-42")
    assert [s["function"]["name"] for s in filtered] == ["echo"]

    both = "Give me both sides of working from home versus the office."
    assert is_non_live_modes_turn(both)
    assert not should_offer_tools(both)
    assert filter_schemas_for_turn(schemas, both) == []

    # Live ELI5 still offers tools
    live = "ELI5: what is the weather today?"
    assert should_offer_tools(live)

def test_preference_remember_offers_tools() -> None:
    assert should_offer_tools(
        "Please remember that my favorite genres are sci-fi and drama."
    )
