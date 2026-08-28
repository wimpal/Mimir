"""Configuration loading for the Mimir brain.

Layered config, fail-loud:

1. YAML file (non-secrets): `MIMIR_CONFIG` path or `config/config.yaml`
2. `MIMIR_*` environment variables override YAML (container-friendly)
3. Secrets come from `.env` / environment only (`JELLYFIN_API_KEY`, `MIMIR_AUTH_TOKEN`,
   `CALENDAR_ICS_URL`, …)

Proof command: `uv run python -m brain.config` prints a redacted summary.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path("config/config.yaml")


class ConfigError(RuntimeError):
    """Configuration is missing or invalid. Never guess — fail loud."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OllamaSettings(_Strict):
    url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    num_ctx: int = 8192
    think: bool = False


@dataclass(frozen=True)
class HomeLocation:
    """Home coords + timezone for weather (and later location-bound tools)."""

    latitude: float
    longitude: float
    timezone: str


class LocationSettings(_Strict):
    latitude: float
    longitude: float
    timezone: str = "Europe/Amsterdam"

    def as_home(self) -> HomeLocation:
        return HomeLocation(
            latitude=self.latitude,
            longitude=self.longitude,
            timezone=self.timezone,
        )


class JellyfinSettings(_Strict):
    url: str = ""
    api_key: str | None = None  # secret: JELLYFIN_API_KEY
    user_id: str = ""
    library_ids: list[str] = Field(default_factory=list)
    sync_interval_hours: float = 24.0  # 0 = periodic Sync off
    page_size: int = 100
    recent_watched_days: int = 14  # Recent watches window for tools / rec bias


class AuthSettings(_Strict):
    mode: Literal["none", "token"] = "none"
    token: str | None = None  # secret: MIMIR_AUTH_TOKEN


class RuntimeSettings(_Strict):
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000

    def ensure_data_dir(self) -> Path:
        """Create the data dir if missing; return it as an absolute path."""
        p = self.data_dir.expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        p.mkdir(parents=True, exist_ok=True)
        return p


class AgentSettings(_Strict):
    max_iterations: int = 3
    system_prompt_path: Path = Path("config/system_prompt.md")


class TimeoutSettings(_Strict):
    ollama_s: float = 120.0
    tool_s: float = 30.0  # enforced when tools become I/O-bound (Phase 3+)
    turn_s: float = 180.0  # overall wall-clock budget for one chat turn
    jellyfin_sync_s: float = 300.0  # overall Sync wall clock
    mcp_default_s: float = 10.0  # MCP tool call default (contracts/mimir.client.md)
    mcp_search_s: float = 30.0  # MCP tools whose name ends with .search


class WeatherSettings(_Strict):
    cache_ttl_s: float = 3600.0  # Forecast cache TTL when Open-Meteo fails


class CalendarFeedSettings(_Strict):
    """One named Calendar feed. URL/auth secrets come from env (see load_config)."""

    id: str
    name: str
    context: str | None = None  # optional note for the LLM (what this calendar is)
    url: str | None = None  # secret: CALENDAR_ICS_URL_<ID>
    username: str | None = None  # secret: CALENDAR_ICS_USERNAME_<ID>
    password: str | None = None  # secret: CALENDAR_ICS_PASSWORD_<ID>

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        text = (value or "").strip()
        if not text or not all(c.isalnum() or c in "_-" for c in text):
            raise ValueError(
                "calendar feed id must be non-empty alphanumeric/underscore/hyphen"
            )
        if not text[0].isalpha():
            raise ValueError("calendar feed id must start with a letter")
        return text

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("calendar feed name must be non-empty")
        return text

    @field_validator("context")
    @classmethod
    def _context_strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class CalendarSettings(_Strict):
    # Legacy single-feed secrets (CALENDAR_ICS_URL) — used when feeds is empty.
    url: str | None = None
    username: str | None = None
    password: str | None = None
    cache_ttl_s: float = 300.0
    feeds: list[CalendarFeedSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_feed_ids(self) -> CalendarSettings:
        ids = [f.id.lower() for f in self.feeds]
        if len(ids) != len(set(ids)):
            raise ValueError("calendar.feeds ids must be unique")
        return self


@dataclass(frozen=True)
class ResolvedCalendarFeed:
    """Feed ready for fetch: has a non-empty URL."""

    id: str
    name: str
    url: str
    username: str | None
    password: str | None
    context: str | None = None


def calendar_feed_env_suffix(feed_id: str) -> str:
    """Map feed id → env suffix (family → FAMILY)."""
    return feed_id.strip().upper().replace("-", "_")


def resolved_calendar_feeds(settings: CalendarSettings) -> list[ResolvedCalendarFeed]:
    """Named feeds with URLs, or a legacy single feed from calendar.url.

    When ``feeds`` is non-empty, only those entries with URLs are returned — legacy
    ``calendar.url`` is not a fallback beside named feeds.
    """
    if settings.feeds:
        out: list[ResolvedCalendarFeed] = []
        for feed in settings.feeds:
            url = (feed.url or "").strip()
            if not url:
                continue
            out.append(
                ResolvedCalendarFeed(
                    id=feed.id,
                    name=feed.name,
                    url=url,
                    username=feed.username,
                    password=feed.password,
                    context=feed.context,
                )
            )
        return out

    legacy = (settings.url or "").strip()
    if legacy:
        return [
            ResolvedCalendarFeed(
                id="default",
                name="Calendar",
                url=legacy,
                username=settings.username,
                password=settings.password,
                context=None,
            )
        ]
    return []


def calendar_feeds_declared(settings: CalendarSettings) -> list[CalendarFeedSettings]:
    """Feeds listed in YAML (may lack URLs yet), or a synthetic legacy slot."""
    if settings.feeds:
        return list(settings.feeds)
    if (settings.url or "").strip():
        return [
            CalendarFeedSettings(
                id="default",
                name="Calendar",
                url=settings.url,
                username=settings.username,
                password=settings.password,
            )
        ]
    return []


class MemorySettings(_Strict):
    history_pairs: int = 20  # last N user+assistant pairs injected under num_ctx


class McpServiceSettings(_Strict):
    """One MCP service Mimir consumes (shape mirrors project-control-heim/registry.yaml)."""

    host: str = "127.0.0.1"
    port: int
    path: str = "/mcp"
    enabled: bool = True
    token: str | None = None  # secret: <SERVICE>_TOKEN e.g. BUDGETTRACKER_TOKEN
    # rmcp Streamable HTTP defaults to loopback-only Host allowlist. When connecting
    # by LAN IP, send Host: localhost (matches BudgetTracker mcp_smoke.rs).
    host_header: str | None = None


def mcp_service_url(service_id: str, svc: McpServiceSettings) -> str:
    """Build the streamable HTTP MCP endpoint URL."""
    path = svc.path if svc.path.startswith("/") else f"/{svc.path}"
    return f"http://{svc.host}:{svc.port}{path}"


def mcp_token_env_name(service_id: str) -> str:
    """Env var for bearer token — BUDGETTRACKER_TOKEN for budgettracker."""
    return f"{service_id.upper()}_TOKEN"


def mcp_request_host_header(svc: McpServiceSettings) -> str | None:
    """Host header for MCP HTTP requests (rmcp DNS-rebinding allowlist)."""
    if svc.host_header is not None:
        text = svc.host_header.strip()
        return text or None
    if is_loopback_host(svc.host):
        return None
    return "localhost"


class Settings(_Strict):
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    location: LocationSettings
    jellyfin: JellyfinSettings = Field(default_factory=JellyfinSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    weather: WeatherSettings = Field(default_factory=WeatherSettings)
    calendar: CalendarSettings = Field(default_factory=CalendarSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    services: dict[str, McpServiceSettings] = Field(default_factory=dict)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback_host(host: str) -> bool:
    """True when ``runtime.host`` is loopback-only."""
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def validate_bind_auth(settings: Settings) -> None:
    """Refuse insecure bind: non-loopback requires Auth token (ADR 0005)."""
    token = (settings.auth.token or "").strip()
    if settings.auth.mode == "token" and not token:
        raise ConfigError(
            "auth.mode is 'token' but MIMIR_AUTH_TOKEN is empty — set the token"
        )
    if not is_loopback_host(settings.runtime.host):
        if settings.auth.mode != "token" or not token:
            raise ConfigError(
                f"runtime.host is {settings.runtime.host!r} (not loopback); "
                "set auth.mode: token and MIMIR_AUTH_TOKEN, or bind 127.0.0.1 "
                "(see docs/adr/0005-non-loopback-requires-auth-token.md)"
            )


# Flat env names on purpose — no nested delimiters in deployment configs.
_ENV_OVERRIDES: dict[tuple[str, str], str] = {
    ("ollama", "url"): "MIMIR_OLLAMA_URL",
    ("ollama", "model"): "MIMIR_OLLAMA_MODEL",
    ("ollama", "num_ctx"): "MIMIR_OLLAMA_NUM_CTX",
    ("ollama", "think"): "MIMIR_OLLAMA_THINK",
    ("location", "latitude"): "MIMIR_LATITUDE",
    ("location", "longitude"): "MIMIR_LONGITUDE",
    ("location", "timezone"): "MIMIR_TIMEZONE",
    ("jellyfin", "url"): "MIMIR_JELLYFIN_URL",
    ("jellyfin", "user_id"): "MIMIR_JELLYFIN_USER_ID",
    ("jellyfin", "library_ids"): "MIMIR_JELLYFIN_LIBRARY_IDS",
    ("jellyfin", "sync_interval_hours"): "MIMIR_JELLYFIN_SYNC_INTERVAL_HOURS",
    ("jellyfin", "page_size"): "MIMIR_JELLYFIN_PAGE_SIZE",
    ("auth", "mode"): "MIMIR_AUTH_MODE",
    ("runtime", "data_dir"): "MIMIR_DATA_DIR",
    ("runtime", "log_level"): "MIMIR_LOG_LEVEL",
    ("runtime", "host"): "MIMIR_HOST",
    ("runtime", "port"): "MIMIR_PORT",
    ("agent", "max_iterations"): "MIMIR_AGENT_MAX_ITERATIONS",
    ("agent", "system_prompt_path"): "MIMIR_SYSTEM_PROMPT_PATH",
    ("timeouts", "ollama_s"): "MIMIR_TIMEOUT_OLLAMA_S",
    ("timeouts", "tool_s"): "MIMIR_TIMEOUT_TOOL_S",
    ("timeouts", "turn_s"): "MIMIR_TIMEOUT_TURN_S",
    ("timeouts", "jellyfin_sync_s"): "MIMIR_JELLYFIN_SYNC_TIMEOUT_S",
    ("timeouts", "mcp_default_s"): "MIMIR_TIMEOUT_MCP_DEFAULT_S",
    ("timeouts", "mcp_search_s"): "MIMIR_TIMEOUT_MCP_SEARCH_S",
    ("weather", "cache_ttl_s"): "MIMIR_WEATHER_CACHE_TTL_S",
    ("calendar", "cache_ttl_s"): "MIMIR_CALENDAR_CACHE_TTL_S",
    ("memory", "history_pairs"): "MIMIR_HISTORY_PAIRS",
}

_SECRET_ENV: dict[str, dict[str, str]] = {
    "jellyfin": {"api_key": "JELLYFIN_API_KEY"},
    "auth": {"token": "MIMIR_AUTH_TOKEN"},
    "calendar": {
        "url": "CALENDAR_ICS_URL",
        "username": "CALENDAR_ICS_USERNAME",
        "password": "CALENDAR_ICS_PASSWORD",
    },
}


def load_config(path: str | Path | None = None, *, use_dotenv: bool = True) -> Settings:
    """Load and validate configuration. Raises ConfigError with specifics."""
    if use_dotenv:
        load_dotenv()  # no-op when .env is absent

    if path is None:
        path = os.environ.get("MIMIR_CONFIG", DEFAULT_CONFIG_PATH)
    cfg_path = Path(path)

    if not cfg_path.is_file():
        raise ConfigError(
            f"config file not found: {cfg_path} "
            f"(copy config/config.example.yaml to config/config.yaml)"
        )

    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {cfg_path}: {exc}") from exc

    data = raw if raw is not None else {}
    if not isinstance(data, dict):
        raise ConfigError(f"{cfg_path}: top level must be a mapping of sections")

    for (section, key), env_name in _ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value is None:
            continue
        target = data.setdefault(section, {})
        if not isinstance(target, dict):
            raise ConfigError(
                f"{cfg_path}: section '{section}' must be a mapping "
                f"(cannot apply {env_name})"
            )
        if section == "jellyfin" and key == "library_ids":
            target[key] = [p.strip() for p in value.split(",") if p.strip()]
        else:
            target[key] = value

    for section, secrets in _SECRET_ENV.items():
        target = data.setdefault(section, {})
        if not isinstance(target, dict):
            raise ConfigError(f"{cfg_path}: section '{section}' must be a mapping")
        for key, env_name in secrets.items():
            value = os.environ.get(env_name)
            if value is not None:
                target[key] = value

    _apply_calendar_feed_secrets(data, cfg_path)
    _apply_mcp_service_tokens(data)

    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {cfg_path}:\n{exc}") from exc

    validate_bind_auth(settings)
    return settings


def _apply_mcp_service_tokens(data: dict) -> None:
    """Overlay BUDGETTRACKER_TOKEN etc. onto services.<id>.token from env."""
    services = data.get("services")
    if not isinstance(services, dict):
        return
    for service_id, entry in services.items():
        if not isinstance(entry, dict):
            continue
        env_name = mcp_token_env_name(str(service_id))
        value = os.environ.get(env_name)
        if value is not None:
            entry["token"] = value


def _apply_calendar_feed_secrets(data: dict, cfg_path: Path) -> None:
    """Overlay CALENDAR_ICS_URL_<ID> (and username/password) onto calendar.feeds."""
    cal = data.get("calendar")
    if not isinstance(cal, dict):
        return
    feeds = cal.get("feeds")
    if not isinstance(feeds, list):
        return
    for feed in feeds:
        if not isinstance(feed, dict):
            raise ConfigError(f"{cfg_path}: calendar.feeds entries must be mappings")
        raw_id = feed.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        suffix = calendar_feed_env_suffix(raw_id)
        for key, prefix in (
            ("url", "CALENDAR_ICS_URL_"),
            ("username", "CALENDAR_ICS_USERNAME_"),
            ("password", "CALENDAR_ICS_PASSWORD_"),
        ):
            value = os.environ.get(f"{prefix}{suffix}")
            if value is not None:
                feed[key] = value


def jellyfin_sync_configured(settings: Settings) -> bool:
    """True when Sync has url, api key, user id, and at least one library id."""
    jf = settings.jellyfin
    return bool(
        jf.url.strip()
        and jf.api_key
        and jf.user_id.strip()
        and jf.library_ids
    )


def redacted_view(settings: Settings) -> dict:
    """Config as a printable dict, with secrets masked."""
    data = settings.model_dump(mode="json")
    if data["jellyfin"].get("api_key"):
        data["jellyfin"]["api_key"] = "***set***"
    if data["auth"].get("token"):
        data["auth"]["token"] = "***set***"
    cal = data.get("calendar") or {}
    if cal.get("url"):
        cal["url"] = "***set***"
    if cal.get("username"):
        cal["username"] = "***set***"
    if cal.get("password"):
        cal["password"] = "***set***"
    for feed in cal.get("feeds") or []:
        if not isinstance(feed, dict):
            continue
        if feed.get("url"):
            feed["url"] = "***set***"
        if feed.get("username"):
            feed["username"] = "***set***"
        if feed.get("password"):
            feed["password"] = "***set***"
    for svc_id, svc in (data.get("services") or {}).items():
        if isinstance(svc, dict) and svc.get("token"):
            svc["token"] = "***set***"
    return data


def main() -> int:
    try:
        settings = load_config()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 1
    print(yaml.safe_dump(redacted_view(settings), sort_keys=False), end="")
    print("config OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
