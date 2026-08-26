"""Unit tests for Open-Meteo weather tool (KNMI normalize + failures)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from brain.config import HomeLocation, Settings
from brain.tools import TOOLS, build_registry, dispatch
from brain.tools.weather import (
    KNMI_MODEL,
    normalize_forecast,
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
    "hourly": {
        "time": [
            "2026-08-25T12:00",
            "2026-08-25T13:00",
            "2026-08-25T14:00",
        ],
        "precipitation": [0.2, 0.5, 0.0],
        "weather_code": [61, 63, 1],
    },
}


def test_wmo_label_known_and_unknown() -> None:
    assert wmo_label(0) == "clear"
    assert wmo_label(61) == "slight rain"
    assert "999" in wmo_label(999)
    assert wmo_label(None) == "unknown"


def test_normalize_forecast_compact() -> None:
    out = normalize_forecast(
        SAMPLE_RAW,
        home=HomeLocation(latitude=52.09, longitude=5.12, timezone="Europe/Amsterdam"),
    )
    assert out["source"] == "open-meteo/knmi"
    assert out["timezone"] == "Europe/Amsterdam"
    assert out["current"]["temperature_c"] == 18.5
    assert out["current"]["conditions"] == "slight rain"
    assert out["today"]["date"] == "2026-08-25"
    assert out["today"]["precipitation_mm"] == 1.5
    assert out["tomorrow"]["temp_max_c"] == 22.0
    assert out["tomorrow"]["conditions"] == "mainly clear"
    assert len(out["next_hours_precip"]) == 3
    assert out["next_hours_precip"][1]["precipitation_mm"] == 0.5


def test_build_registry_includes_weather(tmp_path: Path) -> None:
    reg = build_registry(_settings(tmp_path))
    assert set(reg) == {"get_server_time", "echo", "get_weather"}
    assert set(TOOLS) == {"get_server_time", "echo"}


def test_get_weather_with_mock_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    data_dir = settings.runtime.data_dir

    def handler(request: httpx.Request) -> httpx.Response:
        assert KNMI_MODEL in str(request.url)
        assert "Europe%2FAmsterdam" in str(request.url) or "Europe/Amsterdam" in str(
            request.url
        )
        return httpx.Response(200, json=SAMPLE_RAW)

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
    from brain.weather_cache import weather_cache_path

    assert weather_cache_path(data_dir).is_file()


def test_get_weather_serves_stale_cache(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    data_dir = settings.runtime.data_dir
    from brain.tools.weather import normalize_forecast
    from brain.weather_cache import weather_cache_path, write_cache

    compact = normalize_forecast(
        SAMPLE_RAW,
        home=settings.location.as_home(),
    )
    write_cache(weather_cache_path(data_dir), compact, fetched_at="2026-08-26T10:00:00+00:00")

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
    assert data["fetched_at"] == "2026-08-26T10:00:00+00:00"
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
    from datetime import UTC, datetime, timedelta

    from brain.tools.weather import normalize_forecast
    from brain.weather_cache import weather_cache_path, write_cache

    compact = normalize_forecast(SAMPLE_RAW, home=settings.location.as_home())
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
