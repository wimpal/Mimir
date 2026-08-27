"""Unit tests for Calendar feed ICS tool (Phase 8d + multi-feed)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from brain.config import Settings, resolved_calendar_feeds
from brain.tools import TOOLS, build_registry, dispatch
from brain.tools.calendar import (
    events_in_window,
    window_bounds,
)

FIXTURE_ICS = (
    Path(__file__).parent / "fixtures" / "calendar" / "sample.ics"
).read_text(encoding="utf-8")

OTHER_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Mimir//Test//EN
BEGIN:VEVENT
UID:work-1@mimir.test
DTSTART:20260827T130000Z
DTEND:20260827T140000Z
SUMMARY:Sprint planning
END:VEVENT
END:VCALENDAR
"""


def _settings(tmp_path: Path, **calendar_kw: object) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    calendar = {
        "url": "https://example.com/feed.ics",
        "cache_ttl_s": 300.0,
        **calendar_kw,
    }
    return Settings(
        location={
            "latitude": 52.09,
            "longitude": 5.12,
            "timezone": "Europe/Amsterdam",
        },
        ollama={"url": "http://test", "model": "qwen3:8b"},
        runtime={"data_dir": data_dir},
        timeouts={"ollama_s": 30, "tool_s": 5, "turn_s": 60},
        calendar=calendar,
    )


def _run_execute(settings: Settings, client: httpx.Client, *, now: datetime) -> str:
    from brain.config import calendar_feeds_declared, resolved_calendar_feeds
    from brain.tools.calendar import _execute_get_calendar

    return _execute_get_calendar(
        feeds=resolved_calendar_feeds(settings.calendar),
        declared=calendar_feeds_declared(settings.calendar),
        timezone_name=settings.location.timezone,
        timeout_s=settings.timeouts.tool_s,
        http_client=client,
        data_dir=Path(settings.runtime.data_dir),
        cache_ttl_s=settings.calendar.cache_ttl_s,
        now=now,
    )


def test_window_bounds_full_calendar_day() -> None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 8, 27, 8, 30, tzinfo=tz)
    start, end = window_bounds(tz=tz, now=now)
    assert start == datetime(2026, 8, 27, 0, 0, tzinfo=tz)
    assert end == datetime(2026, 8, 28, 0, 0, tzinfo=tz)


def test_events_in_window_includes_timed_allday_rrule() -> None:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 8, 27, 8, 0, tzinfo=tz)
    start, end = window_bounds(tz=tz, now=now)
    events = events_in_window(FIXTURE_ICS, tz=tz, start=start, end=end)
    summaries = [e["summary"] for e in events]
    assert "Standup" in summaries
    assert "Away day" in summaries
    assert "Daily check-in" in summaries
    assert "Tomorrow only" not in summaries


def test_full_day_includes_past_morning_event() -> None:
    from zoneinfo import ZoneInfo

    ics = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Mimir//Test//EN
BEGIN:VEVENT
UID:morning@mimir.test
DTSTART:20260827T064500Z
DTEND:20260827T071500Z
SUMMARY:Huisarts
END:VEVENT
END:VCALENDAR
"""
    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 8, 27, 10, 0, tzinfo=tz)
    start, end = window_bounds(tz=tz, now=now)
    events = events_in_window(ics, tz=tz, start=start, end=end)
    assert [e["summary"] for e in events] == ["Huisarts"]


def test_tool_schema_rejects_hours_argument(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reg = build_registry(settings)
    out = dispatch("get_calendar", {"hours": 24}, tools=reg)
    assert out.startswith("error: unexpected argument")


def test_build_registry_includes_calendar(tmp_path: Path) -> None:
    reg = build_registry(_settings(tmp_path))
    assert "get_calendar" in reg


def test_missing_url_fails_loud(tmp_path: Path) -> None:
    settings = _settings(tmp_path, url=None, feeds=[])
    reg = build_registry(settings)
    out = dispatch("get_calendar", {}, tools=reg)
    assert out == "error: calendar unavailable (not configured)"


def test_legacy_url_resolves_as_default_feed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    feeds = resolved_calendar_feeds(settings.calendar)
    assert len(feeds) == 1
    assert feeds[0].id == "default"
    assert feeds[0].name == "Calendar"


def test_get_calendar_with_mock_transport(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    settings = _settings(tmp_path)
    data_dir = settings.runtime.data_dir

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/feed.ics")
        return httpx.Response(200, text=FIXTURE_ICS)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.calendar import calendar_tools

        reg = {
            **TOOLS,
            **calendar_tools(settings, http_client=client, data_dir=data_dir),
        }
        now = datetime(2026, 8, 27, 8, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        out = _run_execute(settings, client, now=now)

    assert not out.startswith("error:")
    data = json.loads(out)
    assert data["stale"] is False
    summaries = [e["summary"] for e in data["events"]]
    assert "Standup" in summaries
    assert data["events"][0]["calendar"] == "default"
    assert data["events"][0]["calendar_name"] == "Calendar"
    from brain.calendar_cache import calendar_cache_path

    assert calendar_cache_path(data_dir, "default").is_file()
    out2 = dispatch("get_calendar", {}, tools=reg)
    assert isinstance(out2, str)


def test_multi_feed_merges_and_tags(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    settings = _settings(
        tmp_path,
        url=None,
        feeds=[
            {
                "id": "family",
                "name": "Fam Palland",
                "url": "https://example.com/family.ics",
                "context": "Shared household schedule",
            },
            {
                "id": "work",
                "name": "Work",
                "url": "https://example.com/work.ics",
                "context": "Photographer and videographer jobs",
            },
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("family.ics"):
            return httpx.Response(200, text=FIXTURE_ICS)
        if url.endswith("work.ics"):
            return httpx.Response(200, text=OTHER_ICS)
        return httpx.Response(404, text="nope")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        now = datetime(2026, 8, 27, 8, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        out = _run_execute(settings, client, now=now)

    data = json.loads(out)
    by_summary = {e["summary"]: e for e in data["events"]}
    assert by_summary["Standup"]["calendar"] == "family"
    assert by_summary["Standup"]["calendar_name"] == "Fam Palland"
    assert by_summary["Standup"]["calendar_context"] == "Shared household schedule"
    assert by_summary["Sprint planning"]["calendar"] == "work"
    assert by_summary["Sprint planning"]["calendar_name"] == "Work"
    assert (
        by_summary["Sprint planning"]["calendar_context"]
        == "Photographer and videographer jobs"
    )
    assert len(data["feeds"]) == 2
    assert all(f["ok"] for f in data["feeds"])
    by_feed = {f["id"]: f for f in data["feeds"]}
    assert by_feed["family"]["context"] == "Shared household schedule"
    assert by_feed["work"]["context"] == "Photographer and videographer jobs"


def test_multi_feed_partial_failure(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    settings = _settings(
        tmp_path,
        url=None,
        feeds=[
            {"id": "family", "name": "Fam Palland", "url": "https://example.com/family.ics"},
            {"id": "work", "name": "Work", "url": "https://example.com/work.ics"},
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("family.ics"):
            return httpx.Response(200, text=FIXTURE_ICS)
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        now = datetime(2026, 8, 27, 8, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        out = _run_execute(settings, client, now=now)

    assert not out.startswith("error:")
    data = json.loads(out)
    assert any(e["summary"] == "Standup" for e in data["events"])
    assert data["errors"]
    assert data["errors"][0]["id"] == "work"


def test_fetch_uses_network_even_with_fresh_cache(tmp_path: Path) -> None:
    """Weather pattern: always fetch; in-TTL cache is only a failure fallback."""
    from zoneinfo import ZoneInfo

    from brain.calendar_cache import calendar_cache_path, write_cache

    settings = _settings(tmp_path)
    data_dir = Path(settings.runtime.data_dir)
    path = calendar_cache_path(data_dir, "default")
    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 8, 27, 8, 0, tzinfo=tz)
    write_cache(path, FIXTURE_ICS, fetched_at=now.astimezone(UTC).isoformat())
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=FIXTURE_ICS)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        out = _run_execute(settings, client, now=now)

    assert calls["n"] == 1
    data = json.loads(out)
    assert data["stale"] is False
    assert any(e["summary"] == "Standup" for e in data["events"])


def test_fetch_fail_serves_in_ttl_stale_cache(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from brain.calendar_cache import calendar_cache_path, write_cache

    settings = _settings(tmp_path, cache_ttl_s=300.0)
    data_dir = Path(settings.runtime.data_dir)
    path = calendar_cache_path(data_dir, "default")
    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 8, 27, 8, 0, tzinfo=tz)
    # Within TTL so weather-like stale fallback applies.
    fetched_at = (now.astimezone(UTC) - timedelta(seconds=60)).isoformat()
    write_cache(path, FIXTURE_ICS, fetched_at=fetched_at)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        out = _run_execute(settings, client, now=now)

    data = json.loads(out)
    assert data["stale"] is True
    assert data["fetched_at"] == fetched_at
    assert any(e["summary"] == "Standup" for e in data["events"])


def test_fetch_fail_expired_cache_fails_loud(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from brain.calendar_cache import calendar_cache_path, write_cache

    settings = _settings(tmp_path, cache_ttl_s=1.0)
    data_dir = Path(settings.runtime.data_dir)
    path = calendar_cache_path(data_dir, "default")
    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 8, 27, 8, 0, tzinfo=tz)
    old = (now.astimezone(UTC) - timedelta(seconds=120)).isoformat()
    write_cache(path, FIXTURE_ICS, fetched_at=old)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        out = _run_execute(settings, client, now=now)

    assert out.startswith("error: calendar unavailable")
    assert "503" in out


def test_named_feeds_without_urls_ignore_legacy(tmp_path: Path) -> None:
    """When feeds are declared, legacy CALENDAR_ICS_URL is not a fallback."""
    settings = _settings(
        tmp_path,
        url="https://example.com/legacy.ics",
        feeds=[{"id": "family", "name": "Fam Palland", "url": None}],
    )
    from brain.config import calendar_feeds_declared, resolved_calendar_feeds

    assert resolved_calendar_feeds(settings.calendar) == []
    declared = calendar_feeds_declared(settings.calendar)
    assert len(declared) == 1
    reg = build_registry(settings)
    out = dispatch("get_calendar", {}, tools=reg)
    assert out.startswith("error: calendar unavailable")
    assert "not configured" in out


def test_http_error_without_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.calendar import calendar_tools

        reg = {**TOOLS, **calendar_tools(settings, http_client=client)}
        out = dispatch("get_calendar", {}, tools=reg)

    assert out.startswith("error: calendar unavailable")
    assert "404" in out


def test_timeout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=1.0) as client:
        from brain.tools.calendar import calendar_tools

        reg = {**TOOLS, **calendar_tools(settings, http_client=client)}
        out = dispatch("get_calendar", {}, tools=reg)

    assert "timed out" in out


def test_fetch_override(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reg = build_registry(
        settings,
        calendar_fetch_override=lambda: "error: calendar unavailable (offline)",
    )
    out = dispatch("get_calendar", {}, tools=reg)
    assert out == "error: calendar unavailable (offline)"
