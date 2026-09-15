"""Calendar feed tool — provider-agnostic ICS subscribe URL(s) (ADR 0007)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from icalendar import Calendar
from recurring_ical_events import of as recurring_of

from brain.config import (
    CalendarFeedSettings,
    ResolvedCalendarFeed,
    Settings,
    calendar_feeds_declared,
    resolved_calendar_feeds,
)
from brain.morning_brief import format_event_line

LAG_NOTE = "feed may lag publisher (e.g. Proton share up to ~8h)"


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


def window_bounds(
    *,
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


def _as_aware(value: datetime | date, *, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    return datetime(value.year, value.month, value.day, tzinfo=tz)


def _is_all_day(component: Any) -> bool:
    dtstart = component.get("dtstart")
    if dtstart is None:
        return False
    return isinstance(dtstart.dt, date) and not isinstance(dtstart.dt, datetime)


def _prop_str(component: Any, name: str) -> str | None:
    raw = component.get(name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _format_instant(value: datetime | date, *, all_day: bool, tz: ZoneInfo) -> str:
    if all_day and isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return _as_aware(value, tz=tz).isoformat()


def events_in_window(
    ics_text: str,
    *,
    tz: ZoneInfo,
    start: datetime,
    end: datetime,
    calendar_id: str | None = None,
    calendar_name: str | None = None,
    calendar_context: str | None = None,
) -> list[dict[str, Any]]:
    """Parse ICS, expand RRULEs, return compact events overlapping [start, end)."""
    try:
        cal = Calendar.from_ical(ics_text)
    except Exception as exc:  # noqa: BLE001 — surface as ValueError to callers
        raise ValueError(f"invalid ICS: {exc}") from exc

    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    try:
        occurrences = recurring_of(cal).between(start_utc, end_utc)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"ICS expand failed: {exc}") from exc

    events: list[dict[str, Any]] = []
    for component in occurrences:
        if component.name != "VEVENT":
            continue
        dtstart = component.get("dtstart")
        if dtstart is None:
            continue
        all_day = _is_all_day(component)
        ev_start = dtstart.dt
        dtend = component.get("dtend")
        if dtend is not None:
            ev_end = dtend.dt
        elif all_day and isinstance(ev_start, date) and not isinstance(ev_start, datetime):
            ev_end = ev_start + timedelta(days=1)
        else:
            ev_end = ev_start

        item: dict[str, Any] = {
            "summary": _prop_str(component, "summary") or "(no title)",
            "start": _format_instant(ev_start, all_day=all_day, tz=tz),
            "end": _format_instant(ev_end, all_day=all_day, tz=tz),
            "all_day": all_day,
        }
        location = _prop_str(component, "location")
        if location:
            item["location"] = location
        if calendar_id:
            item["calendar"] = calendar_id
        if calendar_name:
            item["calendar_name"] = calendar_name
        if calendar_context:
            item["calendar_context"] = calendar_context
        events.append(item)

    events.sort(key=lambda e: (e["start"], e["summary"]))
    return events


def fetch_ics(
    url: str,
    *,
    timeout_s: float,
    username: str | None = None,
    password: str | None = None,
    client: httpx.Client | None = None,
) -> str:
    """GET the ICS subscribe URL; raises httpx errors on transport failure."""
    owns = client is None
    auth = None
    if username is not None or password is not None:
        auth = (username or "", password or "")
    http = client or httpx.Client(timeout=httpx.Timeout(timeout_s))
    try:
        resp = http.get(url, auth=auth)
        resp.raise_for_status()
        text = resp.text
        if not text.strip():
            raise ValueError("empty ICS body")
        return text
    finally:
        if owns:
            http.close()


def _err_message(exc: BaseException, *, timeout_s: float) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return f"timed out after {timeout_s}s"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return exc.__class__.__name__
    if isinstance(exc, ValueError):
        return f"bad response: {exc}"
    return exc.__class__.__name__


def _record_feed_error(
    errors: list[dict[str, str]],
    feed_meta: list[dict[str, Any]],
    *,
    feed_id: str,
    name: str,
    error: str,
    context: str | None = None,
) -> None:
    errors.append({"id": feed_id, "error": error})
    entry: dict[str, Any] = {"id": feed_id, "name": name, "ok": False, "error": error}
    if context:
        entry["context"] = context
    feed_meta.append(entry)


def _load_feed_body(
    feed: ResolvedCalendarFeed,
    *,
    timeout_s: float,
    http_client: httpx.Client | None,
    cache_path: Path | None,
    cache_ttl_s: float,
    now: datetime | None,
) -> tuple[str, str, bool] | str:
    """Always fetch (weather pattern). On failure, serve in-TTL cache as stale."""
    from brain.calendar_cache import read_fresh, write_cache

    fresh_now = now.astimezone(UTC) if now else None
    try:
        body = fetch_ics(
            feed.url,
            timeout_s=timeout_s,
            username=feed.username,
            password=feed.password,
            client=http_client,
        )
        fetched_at = datetime.now(UTC).isoformat()
        if cache_path is not None:
            write_cache(cache_path, body, fetched_at=fetched_at)
        return body, fetched_at, False
    except Exception as exc:  # noqa: BLE001 — convert to feed-level error / stale
        reason = _err_message(exc, timeout_s=timeout_s)
        if cache_path is None:
            return reason
        cached = read_fresh(cache_path, ttl_s=cache_ttl_s, now=fresh_now)
        if cached is None:
            return reason
        return cached.body, cached.fetched_at, True


def _execute_get_calendar(
    *,
    feeds: list[ResolvedCalendarFeed],
    declared: list[CalendarFeedSettings],
    timezone_name: str,
    timeout_s: float,
    http_client: httpx.Client | None = None,
    data_dir: Path | None = None,
    cache_ttl_s: float = 300.0,
    now: datetime | None = None,
    day_offset: int = 0,
) -> str:
    from brain.calendar_cache import calendar_cache_path

    if not declared:
        return "error: calendar unavailable (not configured)"

    if isinstance(day_offset, bool) or day_offset not in (0, 1):
        return "error: day_offset must be 0 (today) or 1 (tomorrow)"

    try:
        tz = ZoneInfo(timezone_name)
    except Exception:  # noqa: BLE001
        return f"error: calendar unavailable (bad timezone: {timezone_name})"

    start, end = window_bounds(tz=tz, now=now, day_offset=day_offset)
    day_iso = start.date().isoformat()
    by_id = {f.id: f for f in feeds}
    events: list[dict[str, Any]] = []
    feed_meta: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    any_stale = False
    newest_fetched: str | None = None

    for slot in declared:
        resolved = by_id.get(slot.id)
        if resolved is None:
            _record_feed_error(
                errors,
                feed_meta,
                feed_id=slot.id,
                name=slot.name,
                error="not configured",
                context=slot.context,
            )
            continue

        cache_path = (
            calendar_cache_path(data_dir, resolved.id) if data_dir is not None else None
        )
        loaded = _load_feed_body(
            resolved,
            timeout_s=timeout_s,
            http_client=http_client,
            cache_path=cache_path,
            cache_ttl_s=cache_ttl_s,
            now=now,
        )
        if isinstance(loaded, str):
            _record_feed_error(
                errors,
                feed_meta,
                feed_id=resolved.id,
                name=resolved.name,
                error=loaded,
                context=resolved.context,
            )
            continue

        body, fetched_at, stale = loaded
        any_stale = any_stale or stale
        if newest_fetched is None or fetched_at > newest_fetched:
            newest_fetched = fetched_at
        try:
            feed_events = events_in_window(
                body,
                tz=tz,
                start=start,
                end=end,
                calendar_id=resolved.id,
                calendar_name=resolved.name,
                calendar_context=resolved.context,
            )
        except ValueError as exc:
            _record_feed_error(
                errors,
                feed_meta,
                feed_id=resolved.id,
                name=resolved.name,
                error=f"bad response: {exc}",
                context=resolved.context,
            )
            continue

        events.extend(feed_events)
        meta: dict[str, Any] = {
            "id": resolved.id,
            "name": resolved.name,
            "ok": True,
            "fetched_at": fetched_at,
            "stale": stale,
            "event_count": len(feed_events),
        }
        if resolved.context:
            meta["context"] = resolved.context
        feed_meta.append(meta)

    events.sort(key=lambda e: (e["start"], e.get("calendar_name") or "", e["summary"]))
    schedule_lines = [format_event_line(ev, locale="en") for ev in events]

    if errors and not any(f.get("ok") for f in feed_meta):
        detail = "; ".join(f"{e['id']}: {e['error']}" for e in errors)
        return f"error: calendar unavailable ({detail})"

    out: dict[str, Any] = {
        "timezone": timezone_name,
        "day_offset": day_offset,
        "date": day_iso,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "events": events,
        "event_count": len(events),
        "schedule_lines": schedule_lines,
        "feeds": feed_meta,
        "fetched_at": newest_fetched or datetime.now(UTC).isoformat(),
        "stale": any_stale,
        "lag_note": LAG_NOTE,
    }
    if errors:
        out["errors"] = errors
    return json.dumps(out, separators=(",", ":"))


def make_get_calendar_tool(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
    data_dir: Path | None = None,
):
    """Build get_calendar bound to ICS feed(s), tool timeout, and per-feed cache."""
    from brain.tools import Tool

    cal = settings.calendar
    timeout_s = settings.timeouts.tool_s
    timezone_name = settings.location.timezone
    feeds = resolved_calendar_feeds(cal)
    declared = calendar_feeds_declared(cal)

    feed_blurb_parts: list[str] = []
    for slot in declared:
        bit = f"{slot.name} ({slot.id})"
        if slot.context:
            bit = f"{bit}: {slot.context}"
        feed_blurb_parts.append(bit)
    feed_blurb = "; ".join(feed_blurb_parts) if feed_blurb_parts else "none configured"

    def execute(*, day_offset: int = 0) -> str:
        if isinstance(day_offset, bool) or day_offset not in (0, 1):
            return "error: day_offset must be 0 (today) or 1 (tomorrow)"
        if fetch_override is not None:
            raw = fetch_override()
            return _annotate_calendar_override(raw, day_offset=day_offset)
        return _execute_get_calendar(
            feeds=feeds,
            declared=declared,
            timezone_name=timezone_name,
            timeout_s=timeout_s,
            http_client=http_client,
            data_dir=data_dir,
            cache_ttl_s=cal.cache_ttl_s,
            day_offset=day_offset,
        )

    return Tool(
        name="get_calendar",
        description=(
            "Return events from the user's Calendar feed(s) (ICS subscribe "
            "URLs in server config) for one full calendar day in the home timezone "
            "(midnight to midnight). Optional day_offset: 0 = today (default; "
            "includes morning appointments already past), 1 = tomorrow. "
            f"Configured feeds: {feed_blurb}. "
            "Each event includes calendar / calendar_name / calendar_context when "
            "configured — use calendar_context to interpret titles (e.g. on a "
            "photographer/videographer work calendar, 'filmen X' is a shoot with "
            "client X, not watching a movie). Mention the calendar name when it "
            "helps. Payload includes day_offset, date (YYYY-MM-DD), event_count and "
            "schedule_lines (reference only — paraphrase each event in spoken prose "
            "with times; do not paste schedule_lines verbatim or copy JSON escape "
            "sequences). When event_count > 0, include every event in a natural "
            "sentence with a short lead-in. Do not invent events. Payload may "
            "include stale=true and per-feed errors."
        ),
        parameters={
            "type": "object",
            "properties": {
                "day_offset": {
                    "type": "integer",
                    "description": "0 = today (default), 1 = tomorrow",
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
    )


def calendar_tools(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    tool = make_get_calendar_tool(
        settings,
        http_client=http_client,
        fetch_override=fetch_override,
        data_dir=data_dir,
    )
    return {tool.name: tool}
