"""Tests for ELI5 intent, system append, and fluff sanitizer (T-055)."""

from __future__ import annotations

from brain.eli5 import (
    apply_eli5_system_append,
    is_eli5_intent,
    sanitize_eli5_reply,
)
from brain.ollama import ChatMessage


def test_eli5_intent_en_nl() -> None:
    assert is_eli5_intent("Explain like I'm five: why does bread rise?")
    assert is_eli5_intent("ELI5: what is a VPN?")
    assert is_eli5_intent("Explain simply: how does a heat pump work?")
    assert is_eli5_intent("Leg het uit alsof ik vijf ben: waarom rijst brood?")
    assert is_eli5_intent("Eenvoudig uitgelegd: wat is wifi?")
    assert not is_eli5_intent("Why did you do that?")
    assert not is_eli5_intent("What's the weather?")
    assert not is_eli5_intent("Leg uit")  # standalone → explain-yourself


def test_sanitize_strips_emoji_and_sparkles() -> None:
    raw = (
        "Yeast makes gas bubbles that inflate the dough, like a cloud! "
        "That's why bread is soft. \U0001f35e\u2728"
    )
    cleaned = sanitize_eli5_reply(raw)
    assert "\U0001f35e" not in cleaned
    assert "\u2728" not in cleaned
    assert "Yeast" in cleaned
    assert cleaned == cleaned.strip()


def test_apply_eli5_system_append() -> None:
    msgs = [ChatMessage(role="system", content="You are Mimir.\n")]
    apply_eli5_system_append(msgs)
    assert "ACTIVE MODE THIS TURN — ELI5" in msgs[0].content
    assert "Never use emoji" in msgs[0].content
