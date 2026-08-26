"""Configuration loading for the Mimir brain.

Layered config, fail-loud:

1. YAML file (non-secrets): `MIMIR_CONFIG` path or `config/config.yaml`
2. `MIMIR_*` environment variables override YAML (container-friendly)
3. Secrets come from `.env` / environment only (`JELLYFIN_API_KEY`, `MIMIR_AUTH_TOKEN`)

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
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class WeatherSettings(_Strict):
    cache_ttl_s: float = 3600.0  # Forecast cache TTL when Open-Meteo fails


class MemorySettings(_Strict):
    history_pairs: int = 20  # last N user+assistant pairs injected under num_ctx


class Settings(_Strict):
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    location: LocationSettings
    jellyfin: JellyfinSettings = Field(default_factory=JellyfinSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    timeouts: TimeoutSettings = Field(default_factory=TimeoutSettings)
    weather: WeatherSettings = Field(default_factory=WeatherSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)


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
    ("weather", "cache_ttl_s"): "MIMIR_WEATHER_CACHE_TTL_S",
    ("memory", "history_pairs"): "MIMIR_HISTORY_PAIRS",
}

_SECRET_ENV: dict[str, dict[str, str]] = {
    "jellyfin": {"api_key": "JELLYFIN_API_KEY"},
    "auth": {"token": "MIMIR_AUTH_TOKEN"},
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

    try:
        settings = Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {cfg_path}:\n{exc}") from exc

    validate_bind_auth(settings)
    return settings


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
