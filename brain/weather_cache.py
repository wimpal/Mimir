"""Forecast cache — last successful weather payload under data_dir."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def weather_cache_path(data_dir: Path) -> Path:
    return data_dir / "cache" / "weather.json"


@dataclass(frozen=True)
class CachedForecast:
    fetched_at: str
    forecast: dict[str, Any]

    def age_seconds(self, *, now: datetime | None = None) -> float:
        fetched = datetime.fromisoformat(self.fetched_at)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        return max(0.0, (current - fetched).total_seconds())


def read_cache(path: Path) -> CachedForecast | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    fetched_at = raw.get("fetched_at")
    forecast = raw.get("forecast")
    if not isinstance(fetched_at, str) or not isinstance(forecast, dict):
        return None
    return CachedForecast(fetched_at=fetched_at, forecast=forecast)


def write_cache(path: Path, forecast: dict[str, Any], *, fetched_at: str | None = None) -> str:
    ts = fetched_at or datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": ts, "forecast": forecast}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return ts


def read_fresh(
    path: Path,
    *,
    ttl_s: float,
    now: datetime | None = None,
) -> CachedForecast | None:
    cached = read_cache(path)
    if cached is None:
        return None
    if ttl_s < 0:
        return None
    if cached.age_seconds(now=now) > ttl_s:
        return None
    return cached
