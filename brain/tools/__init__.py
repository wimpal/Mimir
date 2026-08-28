"""Tool registry: dummy tools plus config-bound real tools (weather, …)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brain.config import Settings
    from brain.db import Database


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., str]
    timeout_s: float | None = None
    service: str | None = None  # MCP service id when remote

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _get_server_time() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _echo(*, text: str) -> str:
    return str(text)


def validate_arguments(tool: Tool, arguments: dict[str, Any]) -> str | None:
    """Return an error string if args violate the tool's JSON Schema subset, else None."""
    params = tool.parameters
    props = params.get("properties") or {}
    required = params.get("required") or []
    additional = params.get("additionalProperties", True)

    for key in required:
        if key not in arguments:
            return f"error: missing required argument '{key}' for '{tool.name}'"

    if additional is False:
        unknown = [k for k in arguments if k not in props]
        if unknown:
            return (
                f"error: unexpected argument(s) {unknown} for '{tool.name}'"
            )

    for key, value in arguments.items():
        if key not in props:
            continue
        expected = props[key].get("type")
        if expected == "string" and not isinstance(value, str):
            return f"error: '{key}' must be a string for '{tool.name}'"
        if expected == "number" and not isinstance(value, (int, float)):
            return f"error: '{key}' must be a number for '{tool.name}'"
        if expected == "integer" and not isinstance(value, int):
            return f"error: '{key}' must be an integer for '{tool.name}'"
        if expected == "boolean" and not isinstance(value, bool):
            return f"error: '{key}' must be a boolean for '{tool.name}'"

    return None


GET_SERVER_TIME = Tool(
    name="get_server_time",
    description=(
        "Return the current UTC date and time on the Mimir server as an ISO-8601 string. "
        "Use when the user asks what time it is or for the server clock."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    execute=_get_server_time,
)

ECHO = Tool(
    name="echo",
    description=(
        "Return the given text unchanged. Use when the user asks to echo, repeat, "
        "or mirror a specific string."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Exact text to echo back",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    execute=_echo,
)

# Baseline dummy tools (always present). Real tools join via build_registry.
TOOLS: dict[str, Tool] = {
    GET_SERVER_TIME.name: GET_SERVER_TIME,
    ECHO.name: ECHO,
}


def build_registry(
    settings: Settings,
    *,
    db: Database | None = None,
    weather_fetch_override: Callable[[], str] | None = None,
    calendar_fetch_override: Callable[[], str] | None = None,
    data_dir: Path | None = None,
    mcp: Any | None = None,
) -> dict[str, Tool]:
    """Dummy tools + weather + calendar + optional preference / recommend / MCP tools."""
    from brain.tools.calendar import calendar_tools
    from brain.tools.preferences import preference_tools
    from brain.tools.recently_watched import recently_watched_tools
    from brain.tools.recommend import recommend_tools
    from brain.tools.weather import weather_tools

    resolved_data = data_dir if data_dir is not None else Path(settings.runtime.data_dir)

    registry: dict[str, Tool] = {
        **TOOLS,
        **weather_tools(
            settings,
            fetch_override=weather_fetch_override,
            data_dir=resolved_data,
        ),
        **calendar_tools(
            settings,
            fetch_override=calendar_fetch_override,
            data_dir=resolved_data,
        ),
    }
    if db is not None:
        registry.update(preference_tools(db))
        registry.update(recommend_tools(settings, db))
        registry.update(recently_watched_tools(settings, db))
    if mcp is not None:
        from brain.mcp.tools import build_mcp_tools

        registry.update(build_mcp_tools(mcp, settings))
    return registry


def tool_schemas(tools: dict[str, Tool] | None = None) -> list[dict[str, Any]]:
    registry = TOOLS if tools is None else tools
    return [t.schema() for t in registry.values()]


def dispatch(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    tools: dict[str, Tool] | None = None,
) -> str:
    """Execute a tool by name. Unknown/invalid tools return a short error string."""
    registry = TOOLS if tools is None else tools
    tool = registry.get(name)
    if tool is None:
        return f"error: unknown tool '{name}'"
    args = arguments or {}
    schema_err = validate_arguments(tool, args)
    if schema_err is not None:
        return schema_err
    try:
        return tool.execute(**args)
    except TypeError as exc:
        return f"error: invalid arguments for '{name}': {exc}"
    except Exception as exc:  # noqa: BLE001 — tools must never crash the loop
        return f"error: tool '{name}' failed: {exc}"
