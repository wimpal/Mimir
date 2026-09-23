"""Strict tool-call markup normalizer (T-090)."""

from __future__ import annotations

from brain.ollama import ChatMessage
from brain.tool_format import (
    extract_tool_calls_from_content,
    normalize_message_tool_calls,
    strip_tool_markup,
)


def test_extract_allowlisted_granite_envelope() -> None:
    content = (
        "Sure.\n"
        "<tool_call>\n"
        "<name>get_weather</name>\n"
        "<arguments>{}</arguments>\n"
        "</tool_call>\n"
    )
    calls = extract_tool_calls_from_content(
        content, allowed_names={"get_weather", "echo"}
    )
    assert len(calls) == 1
    assert calls[0].function.name == "get_weather"
    assert calls[0].function.arguments == {}


def test_extract_rejects_non_allowlisted() -> None:
    content = (
        "<tool_call><name>set_preference</name>"
        "<arguments>{\"key\":\"tone\",\"value\":\"dry\"}</arguments></tool_call>"
    )
    assert extract_tool_calls_from_content(content, allowed_names={"echo"}) == []
    assert extract_tool_calls_from_content(content, allowed_names=None) == []
    assert extract_tool_calls_from_content(content, allowed_names=set()) == []


def test_normalize_promotes_and_strips() -> None:
    msg = ChatMessage(
        role="assistant",
        content="<tool_call><name>echo</name><arguments>{\"text\":\"hi\"}</arguments></tool_call>",
    )
    out = normalize_message_tool_calls(msg, allowed_names={"echo"})
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].function.name == "echo"
    assert out.tool_calls[0].function.arguments == {"text": "hi"}
    assert "<tool_call>" not in out.content


def test_strip_tool_markup() -> None:
    assert strip_tool_markup("Hi <tool_call><name>x</name><arguments>{}</arguments></tool_call>") == "Hi"

def test_extract_granite_function_eq_form() -> None:
    content = (
        "<tool_call>\n"
        "<function=list_recently_watched>\n"
        "</function>\n"
        "</tool_call>"
    )
    calls = extract_tool_calls_from_content(
        content, allowed_names={"list_recently_watched"}
    )
    assert len(calls) == 1
    assert calls[0].function.name == "list_recently_watched"
