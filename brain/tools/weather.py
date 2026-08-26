"""Open-Meteo weather tool — KNMI HARMONIE for Netherlands home coords."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from brain.config import HomeLocation, Settings

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
KNMI_MODEL = "knmi_harmonie_arome_netherlands"
SOURCE = "open-meteo/knmi"

# WMO Weather interpretation codes (Open-Meteo / WMO).
_WMO_LABELS: dict[int, str] = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def wmo_label(code: int | None) -> str:
    if code is None:
        return "unknown"
    return _WMO_LABELS.get(int(code), f"weather code {code}")


def normalize_forecast(
    raw: dict[str, Any],
    *,
    home: HomeLocation,
) -> dict[str, Any]:
    """Shrink Open-Meteo JSON into a compact payload for the LLM."""
    current = raw.get("current") or {}
    daily = raw.get("daily") or {}
    hourly = raw.get("hourly") or {}

    code = current.get("weather_code")
    current_out = {
        "time": current.get("time"),
        "temperature_c": current.get("temperature_2m"),
        "conditions": wmo_label(code if isinstance(code, (int, float)) else None),
        "weather_code": code,
        "precipitation_mm": current.get("precipitation"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
    }

    dates = list(daily.get("time") or [])
    tmax = list(daily.get("temperature_2m_max") or [])
    tmin = list(daily.get("temperature_2m_min") or [])
    precip = list(daily.get("precipitation_sum") or [])
    dcode = list(daily.get("weather_code") or [])

    def _day(i: int) -> dict[str, Any] | None:
        if i >= len(dates):
            return None
        dc = dcode[i] if i < len(dcode) else None
        return {
            "date": dates[i],
            "temp_max_c": tmax[i] if i < len(tmax) else None,
            "temp_min_c": tmin[i] if i < len(tmin) else None,
            "precipitation_mm": precip[i] if i < len(precip) else None,
            "conditions": wmo_label(dc if isinstance(dc, (int, float)) else None),
            "weather_code": dc,
        }

    hours = list(hourly.get("time") or [])
    h_precip = list(hourly.get("precipitation") or [])
    h_code = list(hourly.get("weather_code") or [])
    next_hours: list[dict[str, Any]] = []
    for i, t in enumerate(hours[:6]):
        hc = h_code[i] if i < len(h_code) else None
        next_hours.append(
            {
                "time": t,
                "precipitation_mm": h_precip[i] if i < len(h_precip) else None,
                "conditions": wmo_label(hc if isinstance(hc, (int, float)) else None),
            }
        )

    return {
        "location": {"latitude": home.latitude, "longitude": home.longitude},
        "timezone": home.timezone,
        "current": current_out,
        "today": _day(0),
        "tomorrow": _day(1),
        "next_hours_precip": next_hours,
        "source": SOURCE,
    }


def fetch_forecast(
    home: HomeLocation,
    *,
    timeout_s: float,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Call Open-Meteo; raises httpx errors on transport failure."""
    params = {
        "latitude": home.latitude,
        "longitude": home.longitude,
        "timezone": home.timezone,
        "models": KNMI_MODEL,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "forecast_days": 2,
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "weather_code",
                "wind_speed_10m",
            ]
        ),
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
            ]
        ),
        "hourly": "precipitation,weather_code",
        "forecast_hours": 6,
    }
    owns = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(timeout_s))
    try:
        resp = http.get(OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected Open-Meteo response shape")
        return data
    finally:
        if owns:
            http.close()


def _execute_get_weather(
    *,
    home: HomeLocation,
    timeout_s: float,
    http_client: httpx.Client | None = None,
    cache_path: Path | None = None,
    cache_ttl_s: float = 3600.0,
) -> str:
    from brain.weather_cache import write_cache

    try:
        raw = fetch_forecast(home, timeout_s=timeout_s, client=http_client)
        compact = normalize_forecast(raw, home=home)
        fetched_at = datetime.now(UTC).isoformat()
        if cache_path is not None:
            write_cache(cache_path, compact, fetched_at=fetched_at)
        out = {**compact, "fetched_at": fetched_at, "stale": False}
        return json.dumps(out, separators=(",", ":"))
    except httpx.TimeoutException as exc:
        err = f"error: weather unavailable (timed out after {timeout_s}s)"
        return _maybe_stale(cache_path, cache_ttl_s, err, cause=exc)
    except httpx.HTTPStatusError as exc:
        err = f"error: weather unavailable (HTTP {exc.response.status_code})"
        return _maybe_stale(cache_path, cache_ttl_s, err, cause=exc)
    except httpx.HTTPError as exc:
        err = f"error: weather unavailable ({exc.__class__.__name__})"
        return _maybe_stale(cache_path, cache_ttl_s, err, cause=exc)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        err = f"error: weather unavailable (bad response: {exc})"
        return _maybe_stale(cache_path, cache_ttl_s, err, cause=exc)


def _maybe_stale(
    cache_path: Path | None,
    cache_ttl_s: float,
    err: str,
    *,
    cause: BaseException,
) -> str:
    from brain.weather_cache import read_fresh

    del cause  # reserved for future logging
    if cache_path is None:
        return err
    cached = read_fresh(cache_path, ttl_s=cache_ttl_s)
    if cached is None:
        return err
    out = {**cached.forecast, "fetched_at": cached.fetched_at, "stale": True}
    return json.dumps(out, separators=(",", ":"))


def make_get_weather_tool(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
    data_dir: Path | None = None,
):
    """Build get_weather bound to config lat/long, tool timeout, and Forecast cache."""
    from brain.tools import Tool
    from brain.weather_cache import weather_cache_path

    home = settings.location.as_home()
    timeout_s = settings.timeouts.tool_s
    cache_ttl_s = settings.weather.cache_ttl_s
    cache_path = weather_cache_path(data_dir) if data_dir is not None else None

    def execute() -> str:
        if fetch_override is not None:
            return fetch_override()
        return _execute_get_weather(
            home=home,
            timeout_s=timeout_s,
            http_client=http_client,
            cache_path=cache_path,
            cache_ttl_s=cache_ttl_s,
        )

    return Tool(
        name="get_weather",
        description=(
            "Return current conditions and a short forecast for the user's home "
            "location (lat/long from server config; Netherlands KNMI model via "
            "Open-Meteo). Use for weather today/tomorrow, rain, umbrella, "
            "temperature, or conditions. No arguments — home location is fixed. "
            "Payload may include stale=true with fetched_at when serving Forecast cache."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        execute=execute,
    )


def weather_tools(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    tool = make_get_weather_tool(
        settings,
        http_client=http_client,
        fetch_override=fetch_override,
        data_dir=data_dir,
    )
    return {tool.name: tool}
