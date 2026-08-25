"""Config loading tests — Phase 0 exit criterion: "Config loads"."""

from pathlib import Path

import pytest

from brain.config import ConfigError, load_config

VALID_YAML = """\
location:
  latitude: 51.5
  longitude: -0.12
"""

_ENV_VARS = [
    "MIMIR_OLLAMA_URL",
    "MIMIR_OLLAMA_MODEL",
    "MIMIR_LATITUDE",
    "MIMIR_LONGITUDE",
    "MIMIR_JELLYFIN_URL",
    "MIMIR_AUTH_MODE",
    "MIMIR_DATA_DIR",
    "MIMIR_LOG_LEVEL",
    "MIMIR_HOST",
    "MIMIR_PORT",
    "MIMIR_CONFIG",
    "JELLYFIN_API_KEY",
    "MIMIR_AUTH_TOKEN",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def write_config(tmp_path: Path, text: str = VALID_YAML) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_minimal_config_loads_with_defaults(tmp_path: Path) -> None:
    s = load_config(write_config(tmp_path))
    assert s.ollama.url == "http://127.0.0.1:11434"
    assert s.ollama.model == "qwen3:8b"
    assert s.location.latitude == 51.5
    assert s.jellyfin.api_key is None
    assert s.auth.mode == "none"
    assert s.runtime.port == 8000


def test_repo_example_config_loads() -> None:
    example = Path(__file__).parents[1] / "config" / "config.example.yaml"
    s = load_config(example)
    assert s.ollama.model == "qwen3:8b"


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIMIR_OLLAMA_MODEL", "qwen3:14b")
    monkeypatch.setenv("MIMIR_PORT", "9999")
    monkeypatch.setenv("MIMIR_DATA_DIR", "/tmp/mimir-data")
    s = load_config(write_config(tmp_path))
    assert s.ollama.model == "qwen3:14b"
    assert s.runtime.port == 9999
    assert s.runtime.data_dir.as_posix().endswith("mimir-data")


def test_secrets_come_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JELLYFIN_API_KEY", "sekrit")
    monkeypatch.setenv("MIMIR_AUTH_TOKEN", "tok-123")
    s = load_config(write_config(tmp_path))
    assert s.jellyfin.api_key == "sekrit"
    assert s.auth.token == "tok-123"


def test_missing_file_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml", use_dotenv=False)


def test_invalid_value_fails_loud(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("latitude: 51.5", 'latitude: "north"')
    with pytest.raises(ConfigError, match="latitude"):
        load_config(write_config(tmp_path, bad))


def test_unknown_key_fails_loud(tmp_path: Path) -> None:
    typo = VALID_YAML + "jelyfin:\n  url: http://x\n"
    with pytest.raises(ConfigError, match="jelyfin"):
        load_config(write_config(tmp_path, typo))


def test_redacted_view_masks_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.config import redacted_view

    monkeypatch.setenv("JELLYFIN_API_KEY", "sekrit")
    view = redacted_view(load_config(write_config(tmp_path)))
    assert view["jellyfin"]["api_key"] == "***set***"
