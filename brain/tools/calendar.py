"""Calendar tool — backed by the Homebase household calendar (T-140 / ADR-029).

One source of truth: Homebase owns Calendar subscriptions and native events
(`homebase.calendar.agenda`). Mimir keeps the ``get_calendar`` tool name, argument
shape and grounding so morning brief / evening wind-down / schedule Q&A are
unchanged, but its implementation is now a thin adapter over the Homebase agenda.

Local ICS feed config (``calendar.feeds`` / ``CALENDAR_ICS_*``) is **deprecated as
a source of truth** and inert — retained only until the operator confirms the
subscription migration (see CONVENTIONS / T-140). Do not reintroduce a local ICS
fallback: a silent second SoT would mask a Homebase outage.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from brain.config import Settings
from brain.morning_brief import format_event_line

# Producer: Homebase. Consumer: Mimir (ADR-029). Discovered at connect time; the
# name is the contract, not a hardcoded service tool list.
HOMEBASE_AGENDA_TOOL = "homebase.calendar.agenda"


def window_bounds(
    tz: ZoneInfo,
    now: datetime | None = None,
    day_offset: int = 0,
) -> tuple[datetime, datetime]:
    """Return [start, end) for the full calendar day in ``tz``.

    ``day_offset`` 0 = today containing ``now``; 1 = tomorrow. Other values
    are rejected by the tool execute path.
    """
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    else:
        current = current.astimezone(tz)
    day = current.date() + timedelta(days=day_offset)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    next_day = day + timedelta(days=1)
    end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=tz)
    return start, end


def _annotate_calendar_override(raw: str, *, day_offset: int) -> str:
    """Stamp day_offset/date onto a fetch_override JSON payload (suite fixtures)."""
    text = (raw or "").strip()
    if not text or text.startswith("error:"):
        return raw
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return raw
    if not isinstance(data, dict):
        return raw
    data["day_offset"] = day_offset
    window = data.get("window")
    if isinstance(window, dict) and isinstance(window.get("start"), str):
        data.setdefault("date", window["start"][:10])
    return json.dumps(data, separators=(",", ":"))


def _unavailable(detail: str) -> str:
    """Canonical failure string; ``calendar_payload_from_turn`` treats it as not fetched."""
    detail = (detail or "").strip()
    if not detail:
        return "error: calendar unavailable"
    return f"error: calendar unavailable ({detail})"


def _map_agenda(payload: dict[str, Any], *, day_offset: int, date_iso: str) -> dict[str, Any]:
    """Map a Homebase agenda payload onto the brain-local ``get_calendar`` shape."""
    events: list[dict[str, Any]] = []
    for raw_event in payload.get("events") or []:
        if not isinstance(raw_event, dict):
            continue
        item: dict[str, Any] = {
            "summary": str(raw_event.get("title") or "").strip() or "(no title)",
            "start": raw_event.get("start"),
            "end": raw_event.get("end"),
            "all_day": bool(raw_event.get("all_day")),
            "calendar_name": raw_event.get("calendar_name"),
        }
        if raw_event.get("calendar_color"):
            item["calendar_color"] = raw_event["calendar_color"]
        if raw_event.get("source"):
            item["source"] = raw_event["source"]
        if raw_event.get("location"):
            item["location"] = raw_event["location"]
        events.append(item)

    events.sort(
        key=lambda e: (str(e.get("start") or ""), e.get("calendar_name") or "", e["summary"])
    )

    schedule_lines = payload.get("schedule_lines")
    if not isinstance(schedule_lines, list) or not schedule_lines:
        schedule_lines = [format_event_line(ev, locale="en") for ev in events]

    out: dict[str, Any] = {
        "day_offset": day_offset,
        "date": date_iso,
        "window": payload.get("range"),
        "events": events,
        "event_count": payload.get("event_count", len(events)),
        "schedule_lines": schedule_lines,
    }
    if payload.get("stale") is not None:
        out["stale"] = bool(payload["stale"])
    if payload.get("errors"):
        out["errors"] = payload["errors"]
    return out


def _execute_get_calendar(
    bridge: Any | None,
    timezone_name: str,
    timeout_s: float,
    now: datetime | None = None,
    day_offset: int = 0,
) -> str:
    """Fetch one day's agenda from Homebase; never raises, never fetches local ICS."""
    if isinstance(day_offset, bool) or day_offset not in (0, 1):
        return "error: day_offset must be 0 (today) or 1 (tomorrow)"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001
        return _unavailable(f"bad timezone: {timezone_name}")
    if bridge is None:
        return _unavailable("MCP bridge not available")

    start, _end = window_bounds(tz=tz, now=now, day_offset=day_offset)
    date_iso = start.date().isoformat()

    raw = bridge.call_tool_sync(
        HOMEBASE_AGENDA_TOOL, {"date": date_iso}, timeout_s=timeout_s
    )
    text = (raw or "").strip()
    if not text:
        return _unavailable("empty agenda response")
    if text.startswith("error:"):
        return _unavailable(text[len("error:") :].strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _unavailable("invalid agenda response")
    if not isinstance(data, dict):
        return _unavailable("invalid agenda response")
    error = data.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else error
        return _unavailable(str(message).strip() if message else "homebase error")
    return json.dumps(
        _map_agenda(data, day_offset=day_offset, date_iso=date_iso),
        separators=(",", ":"),
    )


def make_get_calendar_tool(
    settings: Settings,
    *,
    fetch_override: Callable[[], str] | None = None,
    bridge: Any | None = None,
) -> Any:
    """Build ``get_calendar`` backed by the Homebase agenda (``bridge`` optional)."""
    from brain.tools import Tool

    timeout_s = settings.timeouts.mcp_default_s
    timezone_name = settings.location.timezone

    def execute(*, day_offset: int = 0) -> str:
        if isinstance(day_offset, bool) or day_offset not in (0, 1):
            return "error: day_offset must be 0 (today) or 1 (tomorrow)"
        if fetch_override is not None:
            return _annotate_calendar_override(fetch_override(), day_offset=day_offset)
        return _execute_get_calendar(
            bridge,
            timezone_name,
            timeout_s,
            day_offset=day_offset,
        )

    return Tool(
        name="get_calendar",
        description=(
            "Return the household calendar events (Homebase) for one full calendar "
            "day in the home timezone (midnight to midnight). Optional day_offset: "
            "0 = today (default; includes morning appointments already past), "
            "1 = tomorrow. Each event includes calendar_name, start/end and all_day. "
            "Payload includes day_offset, date (YYYY-MM-DD), event_count and "
            "schedule_lines (reference only — paraphrase each event in spoken prose "
            "with times; do not paste schedule_lines verbatim or copy JSON escape "
            "sequences). When event_count > 0, include every event in a natural "
            "sentence with a short lead-in. Do not invent events. If the calendar "
            "is unavailable the payload is an error string — say you couldn't fetch "
            "the calendar rather than guessing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "day_offset": {
                    "type": "integer",
                    "description": "0 = today (default), 1 = tomorrow",
                }
            },
            "additionalProperties": False,
        },
        execute=execute,
    )


def calendar_tools(
    settings: Settings,
    *,
    fetch_override: Callable[[], str] | None = None,
    bridge: Any | None = None,
) -> dict[str, Any]:
    tool = make_get_calendar_tool(
        settings,
        fetch_override=fetch_override,
        bridge=bridge,
    )
    return {tool.name: tool}
