"""T-099: Notes secret-like body refusal is surfaced as a tool error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from brain.mcp.errors import parse_conventions_error, tool_result_is_error
from tests.test_mcp_client import _BridgeRunner, _settings

_SECRET_REFUSAL = {
    "error": {
        "code": "invalid_input",
        "message": (
            "Notes cannot store passwords, Wi-Fi keys, or API tokens. "
            "Use a password manager instead."
        ),
        "retryable": False,
    }
}


def _make_notes_server() -> tuple[MCPServer, dict[str, Any]]:
    mcp = MCPServer("Homebase-notes-test")
    state: dict[str, Any] = {"add_calls": []}

    @mcp.tool(name="homebase.notes.add")
    def notes_add(body: str, title: str | None = None) -> dict[str, Any]:
        state["add_calls"].append({"body": body, "title": title})
        lower = (body or "").lower()
        if any(tok in lower for tok in ("password", "wifi", "wi-fi", "api token", "apikey")):
            raise ToolError(json.dumps(_SECRET_REFUSAL))
        return {
            "id": "c111111111111111111111111",
            "title": title,
            "body": body,
            "created_at": "2026-09-29T12:00:00Z",
        }

    return mcp, state


def test_secret_refusal_json_is_tool_error() -> None:
    text = json.dumps(_SECRET_REFUSAL)
    assert tool_result_is_error(text)
    code, retryable = parse_conventions_error(text)
    assert code == "invalid_input"
    assert retryable is False


def test_notes_add_password_like_body_surfaces_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, state = _make_notes_server()
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        out = runner.call(
            "homebase.notes.add",
            {"body": "Wi-Fi password is hunter2"},
        )
        assert tool_result_is_error(out)
        code, _ = parse_conventions_error(out)
        assert code == "invalid_input"
        assert "password" in out.lower() or "wi-fi" in out.lower() or "wifi" in out.lower()
        # Call reached Homebase (Mimir does not client-side secret-scan) but failed.
        assert len(state["add_calls"]) == 1


def test_notes_add_non_secret_body_succeeds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    server, state = _make_notes_server()
    with _BridgeRunner(settings, {"homebase": server}) as runner:
        out = runner.call(
            "homebase.notes.add",
            {"body": "Guest parking is behind the shed"},
        )
        assert not tool_result_is_error(out)
        parsed = json.loads(out)
        assert parsed["body"] == "Guest parking is behind the shed"
        assert len(state["add_calls"]) == 1
