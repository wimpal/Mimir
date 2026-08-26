"""Unit tests for dummy tools registry."""

from __future__ import annotations

from brain.tools import ECHO, GET_SERVER_TIME, TOOLS, dispatch, tool_schemas


def test_default_tools_registered() -> None:
    assert set(TOOLS) == {"get_server_time", "echo"}


def test_schemas_ollama_shape() -> None:
    schemas = tool_schemas()
    assert len(schemas) == 2
    for s in schemas:
        assert s["type"] == "function"
        assert "name" in s["function"]
        assert "description" in s["function"]
        assert "parameters" in s["function"]
        assert s["function"]["parameters"]["type"] == "object"


def test_echo_returns_text() -> None:
    assert ECHO.execute(text="ping-42") == "ping-42"
    assert dispatch("echo", {"text": "ping-42"}) == "ping-42"


def test_get_server_time_iso_utc() -> None:
    out = GET_SERVER_TIME.execute()
    assert out.endswith("Z")
    assert "T" in out
    assert len(out) >= 20


def test_unknown_tool_returns_error_string() -> None:
    out = dispatch("not_a_tool", {})
    assert out.startswith("error:")
    assert "not_a_tool" in out


def test_echo_missing_text_returns_error() -> None:
    out = dispatch("echo", {})
    assert out.startswith("error:")
    assert "text" in out


def test_echo_rejects_extra_args() -> None:
    out = dispatch("echo", {"text": "x", "extra": 1})
    assert out.startswith("error:")
    assert "unexpected" in out


def test_get_server_time_rejects_extra_args() -> None:
    out = dispatch("get_server_time", {"timezone": "UTC"})
    assert out.startswith("error:")
