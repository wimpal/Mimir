"""ICS feed cache — last successful calendar body under data_dir."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SAFE_ID = re.compile(r"[^a-zA-Z0-9_-]+")


def calendar_cache_path(data_dir: Path, feed_id: str = "default") -> Path:
    """Per-feed cache file. ``feed_id`` is sanitized for the filesystem."""
    safe = _SAFE_ID.sub("_", (feed_id or "default").strip()) or "default"
    return data_dir / "cache" / f"calendar_{safe}.json"


@dataclass(frozen=True)
class CachedIcs:
    fetched_at: str
    body: str

    def age_seconds(self, *, now: datetime | None = None) -> float:
        fetched = datetime.fromisoformat(self.fetched_at)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        current = now or datetime.now(UTC)
        return max(0.0, (current - fetched).total_seconds())


def read_cache(path: Path) -> CachedIcs | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    fetched_at = raw.get("fetched_at")
    body = raw.get("body")
    if not isinstance(fetched_at, str) or not isinstance(body, str):
        return None
    return CachedIcs(fetched_at=fetched_at, body=body)


def write_cache(path: Path, body: str, *, fetched_at: str | None = None) -> str:
    ts = fetched_at or datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": ts, "body": body}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return ts


def read_fresh(
    path: Path,
    *,
    ttl_s: float,
    now: datetime | None = None,
) -> CachedIcs | None:
    cached = read_cache(path)
    if cached is None:
        return None
    if ttl_s < 0:
        return None
    if cached.age_seconds(now=now) > ttl_s:
        return None
    return cached
