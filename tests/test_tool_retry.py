"""Preference / budget empty-tools retry helpers (T-090)."""

from __future__ import annotations

from brain.ollama import ChatMessage
from brain.tool_retry import (
    user_message_is_budget_person_followup,
    user_message_requests_preference_write,
)


def test_preference_write_intent() -> None:
    assert user_message_requests_preference_write(
        "Please remember that my favorite genres are sci-fi and drama."
    )
    assert user_message_requests_preference_write(
        "Set my tone preference to dry understatement."
    )
    assert not user_message_requests_preference_write("What is the weather?")


def test_budget_person_followup() -> None:
    prior = [
        ChatMessage(role="user", content="wat heeft ilse deze maand uitgegeven aan boodschappen"),
        ChatMessage(role="assistant", content="Ilse heeft deze maand 157 euro uitgegeven."),
    ]
    assert user_message_is_budget_person_followup("en wim?", prior)
    assert not user_message_is_budget_person_followup("en wim?", [])
    assert not user_message_is_budget_person_followup("wat is het weer?", prior)
