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
