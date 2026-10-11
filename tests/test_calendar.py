"""Tests for the Homebase-backed ``get_calendar`` tool (T-140 / ADR-029).

Local ICS parsing/feeding is retired as a source of truth; these tests exercise
the Homebase agenda adapter with a fake MCP bridge and keep the ``fetch_override``
suite path unchanged.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from brain.config import LocationSettings, RuntimeSettings, Settings
from brain.tools import build_registry
from brain.tools.calendar import (
    HOMEBASE_AGENDA_TOOL,
    _execute_get_calendar,
    calendar_tools,
    make_get_calendar_tool,
    window_bounds,
)


class FakeBridge:
    """Minimal stand-in for McpBridge (sync agenda call + discovery stub)."""

    def __init__(self, result: str = "{}") -> None:
        self.result = result
        self.calls: list[tuple[str, dict, float]] = []
        self.discovered: dict[str, list] = {}

    def call_tool_sync(
        self, tool_name: str, arguments: dict, *, timeout_s: float
    ) -> str:
        self.calls.append((tool_name, arguments, timeout_s))
        return self.result


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        location=LocationSettings(latitude=52.1, longitude=4.3, timezone="Europe/Amsterdam"),
        runtime=RuntimeSettings(data_dir=tmp_path),
    )


def _agenda(*events: dict, **extra: object) -> str:
    payload: dict = {
        "range": {"start": "2026-05-04", "end": "2026-05-04"},
        "event_count": len(events),
        "events": list(events),
        "schedule_lines": [],
        "stale": False,
    }
    payload.update(extra)
    return json.dumps(payload)


def test_window_bounds_full_calendar_day() -> None:
    tz = ZoneInfo("UTC")
    now = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)
    start, end = window_bounds(tz=tz, now=now)
    assert start == datetime(2026, 3, 10, 0, 0, tzinfo=tz)
    assert end == datetime(2026, 3, 11, 0, 0, tzinfo=tz)


def test_window_bounds_day_offset_tomorrow() -> None:
    tz = ZoneInfo("UTC")
    now = datetime(2026, 3, 10, 9, 30, tzinfo=UTC)
    start, end = window_bounds(tz=tz, now=now, day_offset=1)
    assert start == datetime(2026, 3, 11, 0, 0, tzinfo=tz)
    assert end == datetime(2026, 3, 12, 0, 0, tzinfo=tz)


def test_tool_rejects_invalid_day_offset(tmp_path: Path) -> None:
    tool = make_get_calendar_tool(_settings(tmp_path), bridge=FakeBridge())
    assert tool.execute(day_offset=2).startswith("error: day_offset must be")
    assert tool.execute(day_offset=True).startswith("error: day_offset must be")
    assert tool.execute(day_offset=-1).startswith("error: day_offset must be")


def test_execute_calls_homebase_and_maps_events(tmp_path: Path) -> None:
    bridge = FakeBridge(
        _agenda(
            {
                "source": "event",
                "id": "e1",
                "title": "Dentist",
                "start": "2026-05-04T09:00:00+02:00",
                "end": "2026-05-04T09:30:00+02:00",
                "all_day": False,
                "calendar_name": "Homebase",
                "calendar_color": "emerald",
            },
            {
                "source": "feed",
                "id": "f1",
                "title": "Standup",
                "start": "2026-05-04T08:00:00+02:00",
                "end": "2026-05-04T08:15:00+02:00",
                "all_day": True,
                "calendar_name": "Fam Palland",
                "calendar_color": "sky",
            },
            schedule_lines=["09:00 Dentist"],
        )
    )
    tz = ZoneInfo("Europe/Amsterdam")
    result = _execute_get_calendar(
        bridge,
        "Europe/Amsterdam",
        10.0,
        now=datetime(2026, 5, 4, 7, 0, tzinfo=tz),
        day_offset=0,
    )
    payload = json.loads(result)
    assert payload["day_offset"] == 0
    assert payload["date"] == "2026-05-04"
    assert payload["event_count"] == 2
    # sorted by start; Homebase `title` is renamed to `summary`
    assert [e["summary"] for e in payload["events"]] == ["Standup", "Dentist"]
    assert payload["events"][0]["calendar_name"] == "Fam Palland"
    assert payload["events"][0]["all_day"] is True
    assert payload["events"][1]["calendar_color"] == "emerald"
    assert payload["schedule_lines"] == ["09:00 Dentist"]
    assert payload["stale"] is False
    assert bridge.calls == [
        (HOMEBASE_AGENDA_TOOL, {"date": "2026-05-04"}, 10.0),
    ]


def test_execute_day_offset_tomorrow_computes_date(tmp_path: Path) -> None:
    bridge = FakeBridge(_agenda())
    tz = ZoneInfo("Europe/Amsterdam")
    result = _execute_get_calendar(
        bridge,
        "Europe/Amsterdam",
        5.0,
        now=datetime(2026, 5, 4, 23, 30, tzinfo=tz),
        day_offset=1,
    )
    payload = json.loads(result)
    assert payload["date"] == "2026-05-05"
    assert payload["day_offset"] == 1
    assert bridge.calls[0][1] == {"date": "2026-05-05"}


def test_missing_title_falls_back(tmp_path: Path) -> None:
    bridge = FakeBridge(
        _agenda(
            {
                "source": "event",
                "id": "e1",
                "title": "",
                "start": "2026-05-04T09:00:00+02:00",
                "all_day": False,
                "calendar_name": "Homebase",
            }
        )
    )
    payload = json.loads(
        _execute_get_calendar(bridge, "UTC", 5.0, day_offset=0)
    )
    assert payload["events"][0]["summary"] == "(no title)"


def test_empty_day_is_honest() -> None:
    bridge = FakeBridge(_agenda())
    payload = json.loads(_execute_get_calendar(bridge, "UTC", 5.0, day_offset=0))
    assert payload["event_count"] == 0
    assert payload["events"] == []


def test_homebase_down_returns_clear_unavailable() -> None:
    bridge = FakeBridge("error: unknown MCP tool 'homebase.calendar.agenda'")
    out = _execute_get_calendar(bridge, "UTC", 5.0, day_offset=0)
    assert out.startswith("error: calendar unavailable")
    assert "unknown MCP tool" in out


def test_homebase_timeout_returns_clear_unavailable() -> None:
    bridge = FakeBridge("error: tool 'homebase.calendar.agenda' timed out")
    out = _execute_get_calendar(bridge, "UTC", 5.0, day_offset=0)
    assert out.startswith("error: calendar unavailable")


def test_module_off_json_returns_clear_unavailable() -> None:
    bridge = FakeBridge(
        json.dumps({"error": {"code": "unavailable", "message": "Calendar module is off"}})
    )
    out = _execute_get_calendar(bridge, "UTC", 5.0, day_offset=0)
    assert out == "error: calendar unavailable (Calendar module is off)"


def test_invalid_agenda_response() -> None:
    bridge = FakeBridge("not json at all")
    out = _execute_get_calendar(bridge, "UTC", 5.0, day_offset=0)
    assert out == "error: calendar unavailable (invalid agenda response)"


def test_bridge_none_degrades() -> None:
    out = _execute_get_calendar(None, "UTC", 5.0, day_offset=0)
    assert out == "error: calendar unavailable (MCP bridge not available)"


def test_bad_timezone_degrades() -> None:
    out = _execute_get_calendar(FakeBridge(), "Not/AZone", 5.0, day_offset=0)
    assert out.startswith("error: calendar unavailable (bad timezone")


def test_fetch_override_short_circuits_bridge(tmp_path: Path) -> None:
    bridge = FakeBridge(_agenda())
    override = json.dumps({"window": {"start": "2026-01-02T00:00:00+00:00"}, "events": []})
    tool = make_get_calendar_tool(
        _settings(tmp_path), fetch_override=lambda: override, bridge=bridge
    )
    payload = json.loads(tool.execute(day_offset=1))
    assert payload["day_offset"] == 1
    assert payload["date"] == "2026-01-02"
    assert bridge.calls == []


def test_fetch_override_error_passthrough(tmp_path: Path) -> None:
    tool = make_get_calendar_tool(
        _settings(tmp_path),
        fetch_override=lambda: "error: calendar unavailable (offline)",
        bridge=FakeBridge(_agenda()),
    )
    assert tool.execute(day_offset=0) == "error: calendar unavailable (offline)"


def test_description_has_no_ics_or_calendar_context(tmp_path: Path) -> None:
    tool = make_get_calendar_tool(_settings(tmp_path), bridge=FakeBridge())
    description = tool.description
    assert "ICS" not in description
    assert "calendar_context" not in description
    assert "Homebase" in description


def test_build_registry_wires_bridge_into_get_calendar(tmp_path: Path) -> None:
    bridge = FakeBridge(_agenda())
    registry = build_registry(_settings(tmp_path), data_dir=tmp_path, mcp=bridge)
    assert "get_calendar" in registry
    registry["get_calendar"].execute(day_offset=0)
    assert bridge.calls and bridge.calls[0][0] == HOMEBASE_AGENDA_TOOL


def test_calendar_tools_without_bridge_degrades(tmp_path: Path) -> None:
    tool = calendar_tools(_settings(tmp_path))["get_calendar"]
    assert tool.execute(day_offset=0).startswith("error: calendar unavailable")
