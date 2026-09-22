"""Open-Meteo weather tool — KNMI HARMONIE for Netherlands home coords."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from brain.config import HomeLocation, Settings

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
KNMI_MODEL = "knmi_harmonie_arome_netherlands"
SOURCE = "open-meteo/knmi"

DayPartKey = Literal["morning", "afternoon", "evening", "night"]

# Half-open local clock windows [start_hour, end_hour).
_DAY_PART_WINDOWS: tuple[tuple[DayPartKey, int, int], ...] = (
    ("morning", 6, 12),
    ("afternoon", 12, 18),
    ("evening", 18, 24),
    ("night", 0, 6),
)

# Higher = more severe / wetter — used when picking a representative WMO code.
_WMO_SEVERITY: dict[int, int] = {
    0: 0,
    1: 1,
    2: 2,
    3: 3,
    45: 4,
    48: 5,
    51: 10,
    53: 12,
    55: 14,
    56: 16,
    57: 18,
    61: 20,
    63: 22,
    65: 24,
    66: 26,
    67: 28,
    71: 21,
    73: 23,
    75: 25,
    77: 19,
    80: 21,
    81: 23,
    82: 27,
    85: 22,
    86: 26,
    95: 30,
    96: 32,
    99: 34,
}

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


def _parse_local_hourly_time(value: str, *, tz: ZoneInfo) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _truncate_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _wmo_severity(code: int | None) -> int:
    if code is None:
        return -1
    return _WMO_SEVERITY.get(int(code), int(code))


def _pick_conditions_code(
    codes: list[int | None],
    precip_mm: list[float],
) -> int | None:
    """Prefer wetter/more severe codes when rain fell; else modal code."""
    usable = [int(c) for c in codes if isinstance(c, (int, float))]
    if not usable:
        return None
    wet = any(p > 0 for p in precip_mm if isinstance(p, (int, float)))
    if wet:
        return max(usable, key=lambda c: (_wmo_severity(c), c))
    counts = Counter(usable)
    # Modal; stable tie-break by severity then code.
    return max(usable, key=lambda c: (counts[c], _wmo_severity(c), c))


def _hourly_rows_from_raw(
    hourly: dict[str, Any],
    *,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    times = list(hourly.get("time") or [])
    temps = list(hourly.get("temperature_2m") or [])
    precip = list(hourly.get("precipitation") or [])
    codes = list(hourly.get("weather_code") or [])
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(times):
        hc = codes[i] if i < len(codes) else None
        code_val = int(hc) if isinstance(hc, (int, float)) else None
        temp = temps[i] if i < len(temps) else None
        p = precip[i] if i < len(precip) else None
        rows.append(
            {
                "time": t,
                "temperature_c": temp,
                "precipitation_mm": p,
                "conditions": wmo_label(code_val),
                "weather_code": code_val,
                "_dt": _parse_local_hourly_time(str(t), tz=tz),
            }
        )
    return rows


def _night_window_end(now_local: datetime) -> datetime:
    """End of tonight's overnight window: tomorrow 06:00 local (covers evening + night)."""
    end_date = now_local.date() + timedelta(days=1)
    return datetime(end_date.year, end_date.month, end_date.day, 6, 0, tzinfo=now_local.tzinfo)


def _night_part_date(now_local: datetime) -> date:
    """Calendar date for the night [00,06) window currently/next relevant."""
    if now_local.hour < 6:
        return now_local.date()
    return now_local.date() + timedelta(days=1)


def build_day_parts(
    hourly: list[dict[str, Any]],
    *,
    timezone: str,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate remaining hours into named day-part slices.

    Windows (home TZ, half-open): morning [06,12), afternoon [12,18),
    evening [18,24), night [00,06). Morning/afternoon/evening use today's
    calendar date; night is the current overnight before 06:00, otherwise
    the following morning. Only hours at/after the current hour are included.
    """
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = (now or datetime.now(UTC)).astimezone(tz)
    now_hour = _truncate_hour(now_local)
    anchor = now_local.date()
    night_date = _night_part_date(now_local)

    # Materialize datetimes for rows that lack _dt (e.g. from cache).
    prepared: list[dict[str, Any]] = []
    for row in hourly:
        dt = row.get("_dt")
        if not isinstance(dt, datetime):
            dt = _parse_local_hourly_time(str(row.get("time") or ""), tz=tz)
        if dt is None:
            continue
        prepared.append({**row, "_dt": dt})

    parts: dict[str, dict[str, Any]] = {}
    for key, start_h, end_h in _DAY_PART_WINDOWS:
        if key == "night":
            part_date = night_date
        else:
            part_date = anchor
        window_start = datetime(
            part_date.year, part_date.month, part_date.day, start_h % 24, 0, tzinfo=tz
        )
        if end_h >= 24:
            window_end = datetime(
                part_date.year, part_date.month, part_date.day, 0, 0, tzinfo=tz
            ) + timedelta(days=1)
        else:
            window_end = datetime(
                part_date.year, part_date.month, part_date.day, end_h, 0, tzinfo=tz
            )

        bucket: list[dict[str, Any]] = []
        for row in prepared:
            dt = row["_dt"]
            assert isinstance(dt, datetime)
            if dt < now_hour:
                continue
            if window_start <= dt < window_end:
                bucket.append(row)

        if not bucket:
            continue

        temps = [
            float(r["temperature_c"])
            for r in bucket
            if isinstance(r.get("temperature_c"), (int, float))
        ]
        precip_vals = [
            float(r["precipitation_mm"])
            if isinstance(r.get("precipitation_mm"), (int, float))
            else 0.0
            for r in bucket
        ]
        codes = [r.get("weather_code") for r in bucket]
        code = _pick_conditions_code(codes, precip_vals)
        start_dt = bucket[0]["_dt"]
        end_dt = bucket[-1]["_dt"]
        assert isinstance(start_dt, datetime) and isinstance(end_dt, datetime)
        parts[key] = {
            "temp_min_c": min(temps) if temps else None,
            "temp_max_c": max(temps) if temps else None,
            "precipitation_mm": round(sum(precip_vals), 2),
            "conditions": wmo_label(code),
            "weather_code": code,
            "start": start_dt.strftime("%Y-%m-%dT%H:%M"),
            "end": end_dt.strftime("%Y-%m-%dT%H:%M"),
        }
    return parts


def trim_hourly_for_payload(
    rows: list[dict[str, Any]],
    *,
    timezone: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Keep hours from now through end of tonight's night window (no _dt)."""
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = (now or datetime.now(UTC)).astimezone(tz)
    now_hour = _truncate_hour(now_local)
    end = _night_window_end(now_local)
    out: list[dict[str, Any]] = []
    for row in rows:
        dt = row.get("_dt")
        if not isinstance(dt, datetime):
            dt = _parse_local_hourly_time(str(row.get("time") or ""), tz=tz)
        if dt is None or dt < now_hour or dt >= end:
            continue
        out.append(
            {
                "time": row.get("time"),
                "temperature_c": row.get("temperature_c"),
                "precipitation_mm": row.get("precipitation_mm"),
                "conditions": row.get("conditions"),
                "weather_code": row.get("weather_code"),
            }
        )
    return out


def refresh_day_parts(
    forecast: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recompute day_parts from cached hourly using current local time.

    Old caches without hourly get day_parts cleared (never invent slices).
    """
    out = dict(forecast)
    tz_name = str(out.get("timezone") or "UTC")
    hourly = out.get("hourly")
    if not isinstance(hourly, list):
        # Legacy cache without hourly — never invent day_parts.
        out.pop("day_parts", None)
        return out
    # Refresh trimmed hourly + day_parts from absolute rows (empty → empty parts).
    rows_with_meta = [
        {**row, "_dt": None} if isinstance(row, dict) else row for row in hourly
    ]
    cleaned: list[dict[str, Any]] = [r for r in rows_with_meta if isinstance(r, dict)]
    out["hourly"] = trim_hourly_for_payload(cleaned, timezone=tz_name, now=now)
    out["day_parts"] = build_day_parts(cleaned, timezone=tz_name, now=now)
    # Keep next_hours_precip aligned with first 6 remaining hours.
    next_hours: list[dict[str, Any]] = []
    for row in out["hourly"][:6]:
        next_hours.append(
            {
                "time": row.get("time"),
                "temperature_c": row.get("temperature_c"),
                "precipitation_mm": row.get("precipitation_mm"),
                "conditions": row.get("conditions"),
            }
        )
    out["next_hours_precip"] = next_hours
    return out


def normalize_forecast(
    raw: dict[str, Any],
    *,
    home: HomeLocation,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Shrink Open-Meteo JSON into a compact payload for the LLM."""
    current = raw.get("current") or {}
    daily = raw.get("daily") or {}
    hourly_raw = raw.get("hourly") or {}

    try:
        tz = ZoneInfo(home.timezone)
    except Exception:
        tz = ZoneInfo("UTC")

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

    rows = _hourly_rows_from_raw(hourly_raw if isinstance(hourly_raw, dict) else {}, tz=tz)
    # Prefer API "current.time" as now when testing fixtures so day_parts are stable.
    if now is None and isinstance(current.get("time"), str):
        parsed = _parse_local_hourly_time(str(current["time"]), tz=tz)
        if parsed is not None:
            now = parsed

    hourly_out = trim_hourly_for_payload(rows, timezone=home.timezone, now=now)
    day_parts = build_day_parts(rows, timezone=home.timezone, now=now)

    next_hours: list[dict[str, Any]] = []
    for row in hourly_out[:6]:
        next_hours.append(
            {
                "time": row.get("time"),
                "temperature_c": row.get("temperature_c"),
                "precipitation_mm": row.get("precipitation_mm"),
                "conditions": row.get("conditions"),
            }
        )

    return {
        "location": {"latitude": home.latitude, "longitude": home.longitude},
        "timezone": home.timezone,
        "current": current_out,
        "today": _day(0),
        "tomorrow": _day(1),
        "next_hours_precip": next_hours,
        "hourly": hourly_out,
        "day_parts": day_parts,
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
        "hourly": "temperature_2m,precipitation,weather_code",
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
        compact = normalize_forecast(raw, home=home, now=datetime.now(UTC))
        fetched_at = datetime.now(UTC).isoformat()
        if cache_path is not None:
            write_cache(cache_path, compact, fetched_at=fetched_at)
        refreshed = refresh_day_parts(compact, now=datetime.now(UTC))
        # Hourly stays in cache for day_part refresh; omit from LLM payload.
        llm_out = {k: v for k, v in refreshed.items() if k != "hourly"}
        out = {**llm_out, "fetched_at": fetched_at, "stale": False}
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
    refreshed = refresh_day_parts(cached.forecast)
    llm_out = {k: v for k, v in refreshed.items() if k != "hourly"}
    out = {**llm_out, "fetched_at": cached.fetched_at, "stale": True}
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
            "temperature, day-parts (ochtend/middag/avond/vanavond/nacht; "
            "morning/afternoon/evening/tonight/night), or conditions. Takes "
            "**no arguments** — never pass day_offset (that is get_calendar only). "
            "Home location is fixed. Payload includes current, today, tomorrow, "
            "and day_parts — for morgen/tomorrow use tomorrow; for vanavond/"
            "tonight/afternoon use the matching day_parts slice. Payload may "
            "include stale=true with fetched_at when serving Forecast cache."
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
