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


class LocationSettings(_Strict):
    latitude: float
    longitude: float


class JellyfinSettings(_Strict):
    url: str = ""
    api_key: str | None = None  # secret: JELLYFIN_API_KEY (used in Phase 5)


class AuthSettings(_Strict):
    mode: Literal["none", "token"] = "none"
    token: str | None = None  # secret: MIMIR_AUTH_TOKEN (enforced in Phase 7)


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


class Settings(_Strict):
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    location: LocationSettings
    jellyfin: JellyfinSettings = Field(default_factory=JellyfinSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)


# Flat env names on purpose — no nested delimiters in deployment configs.
_ENV_OVERRIDES: dict[tuple[str, str], str] = {
    ("ollama", "url"): "MIMIR_OLLAMA_URL",
    ("ollama", "model"): "MIMIR_OLLAMA_MODEL",
    ("location", "latitude"): "MIMIR_LATITUDE",
    ("location", "longitude"): "MIMIR_LONGITUDE",
    ("jellyfin", "url"): "MIMIR_JELLYFIN_URL",
    ("auth", "mode"): "MIMIR_AUTH_MODE",
    ("runtime", "data_dir"): "MIMIR_DATA_DIR",
    ("runtime", "log_level"): "MIMIR_LOG_LEVEL",
    ("runtime", "host"): "MIMIR_HOST",
    ("runtime", "port"): "MIMIR_PORT",
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
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {cfg_path}:\n{exc}") from exc


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
