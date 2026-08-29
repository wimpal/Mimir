"""Tests for tool result error detection."""

from brain.mcp.errors import tool_result_is_error


def test_tool_result_is_error_prefix() -> None:
    assert tool_result_is_error("error: tool timed out")


def test_tool_result_is_error_json_body() -> None:
    assert tool_result_is_error('{"error":{"code":"not_found"}}')


def test_tool_result_is_error_json_with_note_prefix() -> None:
    text = 'Note: something\n{"error":{"code":"not_found"}}'
    assert tool_result_is_error(text)


def test_tool_result_is_error_success_complete() -> None:
    text = 'Note: ok\n{"id":"c1","title":"x","completion_recorded":true}'
    assert not tool_result_is_error(text)
