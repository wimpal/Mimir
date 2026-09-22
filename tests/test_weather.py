"""Unit tests for Open-Meteo weather tool (KNMI normalize + failures)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from brain.config import HomeLocation, Settings
from brain.tools import TOOLS, build_registry, dispatch
from brain.tools.weather import (
    KNMI_MODEL,
    build_day_parts,
    normalize_forecast,
    refresh_day_parts,
    wmo_label,
)


def _settings(tmp_path: Path, **timeout_kw: float) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    timeouts = {"ollama_s": 30, "tool_s": 5, "turn_s": 60, **timeout_kw}
    return Settings(
        location={
            "latitude": 52.09,
            "longitude": 5.12,
            "timezone": "Europe/Amsterdam",
        },
        ollama={"url": "http://test", "model": "qwen3:8b"},
        runtime={"data_dir": data_dir},
        timeouts=timeouts,
    )


def _hourly_span() -> dict[str, list]:
    """Hours covering afternoon → evening → night for day-part tests."""
    times = [
        "2026-08-25T12:00",
        "2026-08-25T13:00",
        "2026-08-25T14:00",
        "2026-08-25T17:00",
        "2026-08-25T18:00",
        "2026-08-25T19:00",
        "2026-08-25T21:00",
        "2026-08-25T23:00",
        "2026-08-26T01:00",
        "2026-08-26T03:00",
        "2026-08-26T05:00",
    ]
    return {
        "time": times,
        "temperature_2m": [20.0, 21.0, 22.0, 19.0, 17.0, 16.0, 14.0, 13.0, 12.0, 11.0, 10.5],
        "precipitation": [0.0, 0.0, 0.0, 0.0, 0.2, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0],
        "weather_code": [1, 1, 2, 2, 61, 63, 3, 3, 45, 45, 3],
    }


SAMPLE_RAW = {
    "current": {
        "time": "2026-08-25T12:00",
        "temperature_2m": 18.5,
        "relative_humidity_2m": 72,
        "precipitation": 0.2,
        "weather_code": 61,
        "wind_speed_10m": 15.0,
    },
    "daily": {
        "time": ["2026-08-25", "2026-08-26"],
        "temperature_2m_max": [20.0, 22.0],
        "temperature_2m_min": [12.0, 13.0],
        "precipitation_sum": [1.5, 0.0],
        "weather_code": [61, 1],
    },
    "hourly": _hourly_span(),
}

HOME = HomeLocation(latitude=52.09, longitude=5.12, timezone="Europe/Amsterdam")
TZ = ZoneInfo("Europe/Amsterdam")


def test_wmo_label_known_and_unknown() -> None:
    assert wmo_label(0) == "clear"
    assert wmo_label(61) == "slight rain"
    assert "999" in wmo_label(999)
    assert wmo_label(None) == "unknown"


def test_normalize_forecast_compact() -> None:
    out = normalize_forecast(SAMPLE_RAW, home=HOME)
    assert out["source"] == "open-meteo/knmi"
    assert out["timezone"] == "Europe/Amsterdam"
    assert out["current"]["temperature_c"] == 18.5
    assert out["current"]["conditions"] == "slight rain"
    assert out["today"]["date"] == "2026-08-25"
    assert out["today"]["precipitation_mm"] == 1.5
    assert out["tomorrow"]["temp_max_c"] == 22.0
    assert out["tomorrow"]["conditions"] == "mainly clear"
    assert len(out["next_hours_precip"]) == 6
    assert out["next_hours_precip"][0]["temperature_c"] == 20.0
    assert "hourly" in out
    assert "day_parts" in out
    assert "afternoon" in out["day_parts"]
    assert "evening" in out["day_parts"]
    assert out["day_parts"]["evening"]["temp_max_c"] == 17.0
    assert out["day_parts"]["evening"]["temp_min_c"] == 13.0


def test_day_parts_boundary_17_vs_18() -> None:
    now = datetime(2026, 8, 25, 17, 59, tzinfo=TZ)
    parts = build_day_parts(
        [
            {
                "time": "2026-08-25T17:00",
                "temperature_c": 19.0,
                "precipitation_mm": 0.0,
                "conditions": "partly cloudy",
                "weather_code": 2,
            },
            {
                "time": "2026-08-25T18:00",
                "temperature_c": 15.0,
                "precipitation_mm": 0.0,
                "conditions": "overcast",
                "weather_code": 3,
            },
        ],
        timezone="Europe/Amsterdam",
        now=now,
    )
    # 17:00 is still afternoon; 18:00 starts evening. now truncated to 17:00.
    assert "afternoon" in parts
    assert parts["afternoon"]["temp_max_c"] == 19.0
    assert "evening" in parts
    assert parts["evening"]["temp_max_c"] == 15.0


def test_day_parts_mid_evening_remaining_only() -> None:
    now = datetime(2026, 8, 25, 21, 0, tzinfo=TZ)
    out = normalize_forecast(SAMPLE_RAW, home=HOME, now=now)
    evening = out["day_parts"]["evening"]
    assert evening["start"] == "2026-08-25T21:00"
    assert evening["temp_max_c"] == 14.0
    assert evening["temp_min_c"] == 13.0
    assert "afternoon" not in out["day_parts"]


def test_day_parts_night_after_midnight() -> None:
    now = datetime(2026, 8, 26, 1, 0, tzinfo=TZ)
    out = normalize_forecast(SAMPLE_RAW, home=HOME, now=now)
    assert "night" in out["day_parts"]
    assert out["day_parts"]["night"]["temp_min_c"] == 10.5


def test_day_parts_pre_06_keeps_today_evening() -> None:
    """At 01:00, vanavond still means this calendar day's evening — not yesterday."""
    now = datetime(2026, 8, 26, 1, 0, tzinfo=TZ)
    rows = [
        {
            "time": f"2026-08-26T{h:02d}:00",
            "temperature_c": float(10 + h),
            "precipitation_mm": 0.0,
            "conditions": "clear",
            "weather_code": 0,
        }
        for h in (1, 3, 5, 8, 14, 18, 21)
    ]
    parts = build_day_parts(rows, timezone="Europe/Amsterdam", now=now)
    assert "night" in parts
    assert "morning" in parts
    assert "afternoon" in parts
    assert "evening" in parts
    assert parts["evening"]["temp_min_c"] == 28.0  # 18+21 → 28 and 31


def test_refresh_day_parts_from_cache_hourly() -> None:
    compact = normalize_forecast(
        SAMPLE_RAW,
        home=HOME,
        now=datetime(2026, 8, 25, 12, 0, tzinfo=TZ),
    )
    later = refresh_day_parts(
        compact, now=datetime(2026, 8, 25, 20, 0, tzinfo=TZ)
    )
    assert "afternoon" not in later["day_parts"]
    assert "evening" in later["day_parts"]
    assert later["day_parts"]["evening"]["start"] == "2026-08-25T21:00"


def test_refresh_clears_day_parts_without_hourly() -> None:
    legacy = {
        "timezone": "Europe/Amsterdam",
        "current": {"temperature_c": 18.0, "conditions": "clear"},
        "today": {"temp_max_c": 20.0, "temp_min_c": 12.0},
        "day_parts": {"evening": {"temp_max_c": 99}},
    }
    out = refresh_day_parts(legacy)
    assert "day_parts" not in out


def test_build_registry_includes_weather(tmp_path: Path) -> None:
    reg = build_registry(_settings(tmp_path))
    assert {
        "get_server_time",
        "echo",
        "get_weather",
        "get_calendar",
        "web.fetch",
        "convert_currency",
        "wikipedia_lookup",
        "random_fact",
    } <= set(reg)
    assert set(TOOLS) == {"get_server_time", "echo"}


def test_get_weather_with_mock_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    data_dir = settings.runtime.data_dir
    # Build a live-relative raw payload so day_parts survive datetime.now() refresh.
    now_local = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    times = [
        (now_local + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
        for i in range(0, 18)
    ]
    live_raw = {
        "current": {
            "time": now_local.strftime("%Y-%m-%dT%H:%M"),
            "temperature_2m": 18.5,
            "relative_humidity_2m": 72,
            "precipitation": 0.2,
            "weather_code": 61,
            "wind_speed_10m": 15.0,
        },
        "daily": {
            "time": [
                now_local.date().isoformat(),
                (now_local.date() + timedelta(days=1)).isoformat(),
            ],
            "temperature_2m_max": [20.0, 22.0],
            "temperature_2m_min": [12.0, 13.0],
            "precipitation_sum": [1.5, 0.0],
            "weather_code": [61, 1],
        },
        "hourly": {
            "time": times,
            "temperature_2m": [18.0 + (i % 5) for i in range(18)],
            "precipitation": [0.1 if i % 4 == 0 else 0.0 for i in range(18)],
            "weather_code": [61 if i % 4 == 0 else 1 for i in range(18)],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert KNMI_MODEL in str(request.url)
        assert "Europe%2FAmsterdam" in str(request.url) or "Europe/Amsterdam" in str(
            request.url
        )
        assert "temperature_2m" in str(request.url)
        assert "forecast_hours" not in str(request.url)
        return httpx.Response(200, json=live_raw)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.weather import weather_tools

        reg = {
            **TOOLS,
            **weather_tools(settings, http_client=client, data_dir=data_dir),
        }
        out = dispatch("get_weather", {}, tools=reg)

    assert not out.startswith("error:")
    data = json.loads(out)
    assert data["current"]["temperature_c"] == 18.5
    assert data["source"] == "open-meteo/knmi"
    assert data["stale"] is False
    assert "fetched_at" in data
    assert isinstance(data.get("day_parts"), dict)
    from brain.weather_cache import weather_cache_path

    assert weather_cache_path(data_dir).is_file()


def test_get_weather_serves_stale_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    data_dir = settings.runtime.data_dir
    from datetime import timedelta

    from brain.weather_cache import weather_cache_path, write_cache

    compact = normalize_forecast(
        SAMPLE_RAW,
        home=settings.location.as_home(),
        now=datetime(2026, 8, 25, 12, 0, tzinfo=TZ),
    )
    # Within default weather.cache_ttl_s so failure path can serve stale.
    fetched_at = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    write_cache(weather_cache_path(data_dir), compact, fetched_at=fetched_at)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.weather import weather_tools

        reg = {
            **TOOLS,
            **weather_tools(settings, http_client=client, data_dir=data_dir),
        }
        out = dispatch("get_weather", {}, tools=reg)

    assert not out.startswith("error:")
    data = json.loads(out)
    assert data["stale"] is True
    assert data["fetched_at"] == fetched_at
    assert data["current"]["temperature_c"] == 18.5


def test_get_weather_expired_cache_fails_clear(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings = Settings(
        location=settings.location.model_dump(),
        ollama=settings.ollama.model_dump(),
        runtime={"data_dir": settings.runtime.data_dir},
        timeouts=settings.timeouts.model_dump(),
        weather={"cache_ttl_s": 1.0},
    )
    data_dir = settings.runtime.data_dir
    from datetime import timedelta

    from brain.weather_cache import weather_cache_path, write_cache

    compact = normalize_forecast(
        SAMPLE_RAW, home=settings.location.as_home(), now=datetime(2026, 8, 25, 12, 0, tzinfo=TZ)
    )
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    write_cache(weather_cache_path(data_dir), compact, fetched_at=old)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.weather import weather_tools

        reg = {
            **TOOLS,
            **weather_tools(settings, http_client=client, data_dir=data_dir),
        }
        out = dispatch("get_weather", {}, tools=reg)

    assert out.startswith("error: weather unavailable")


def test_get_weather_http_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.weather import weather_tools

        reg = {**TOOLS, **weather_tools(settings, http_client=client)}
        out = dispatch("get_weather", {}, tools=reg)

    assert out.startswith("error: weather unavailable")
    assert "503" in out


def test_get_weather_timeout(tmp_path: Path) -> None:
    settings = _settings(tmp_path, tool_s=1.0)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=1.0) as client:
        from brain.tools.weather import weather_tools

        reg = {**TOOLS, **weather_tools(settings, http_client=client)}
        out = dispatch("get_weather", {}, tools=reg)

    assert out.startswith("error: weather unavailable")
    assert "timed out" in out


def test_get_weather_fetch_override(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reg = build_registry(
        settings, weather_fetch_override=lambda: "error: weather unavailable (offline)"
    )
    out = dispatch("get_weather", {}, tools=reg)
    assert out == "error: weather unavailable (offline)"


def test_get_weather_ignores_day_offset_hallucination(tmp_path: Path) -> None:
    """Models sometimes pass calendar day_offset; argless tools must still run."""
    settings = _settings(tmp_path)
    payload = json.dumps(
        {
            "current": {"temperature_c": 12.0, "conditions": "overcast"},
            "today": {"temp_max_c": 14.0, "temp_min_c": 10.0, "conditions": "overcast"},
            "tomorrow": {
                "temp_max_c": 18.0,
                "temp_min_c": 9.0,
                "conditions": "partly cloudy",
            },
        }
    )
    reg = build_registry(settings, weather_fetch_override=lambda: payload)
    out = dispatch("get_weather", {"day_offset": 1}, tools=reg)
    data = json.loads(out)
    assert data["tomorrow"]["temp_max_c"] == 18.0
    assert "error" not in out
