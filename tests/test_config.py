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
    "MIMIR_OLLAMA_NUM_CTX",
    "MIMIR_OLLAMA_THINK",
    "MIMIR_LATITUDE",
    "MIMIR_LONGITUDE",
    "MIMIR_TIMEZONE",
    "MIMIR_JELLYFIN_URL",
    "MIMIR_JELLYFIN_USER_ID",
    "MIMIR_JELLYFIN_LIBRARY_IDS",
    "MIMIR_JELLYFIN_SYNC_INTERVAL_HOURS",
    "MIMIR_JELLYFIN_PAGE_SIZE",
    "MIMIR_JELLYFIN_SYNC_TIMEOUT_S",
    "MIMIR_AUTH_MODE",
    "MIMIR_DATA_DIR",
    "MIMIR_LOG_LEVEL",
    "MIMIR_HOST",
    "MIMIR_PORT",
    "MIMIR_CONFIG",
    "MIMIR_AGENT_MAX_ITERATIONS",
    "MIMIR_SYSTEM_PROMPT_PATH",
    "MIMIR_TIMEOUT_OLLAMA_S",
    "MIMIR_TIMEOUT_TOOL_S",
    "MIMIR_TIMEOUT_TURN_S",
    "MIMIR_HISTORY_PAIRS",
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


def _load(tmp_path: Path, text: str = VALID_YAML):
    return load_config(write_config(tmp_path, text), use_dotenv=False)


def test_minimal_config_loads_with_defaults(tmp_path: Path) -> None:
    s = _load(tmp_path)
    assert s.ollama.url == "http://127.0.0.1:11434"
    assert s.ollama.model == "qwen3:8b"
    assert s.ollama.num_ctx == 8192
    assert s.ollama.think is False
    assert s.location.latitude == 51.5
    assert s.location.timezone == "Europe/Amsterdam"
    assert s.jellyfin.api_key is None
    assert s.auth.mode == "none"
    assert s.runtime.port == 8000
    assert s.agent.max_iterations == 3
    assert s.timeouts.ollama_s == 120.0
    assert s.timeouts.turn_s == 180.0
    assert s.timeouts.jellyfin_sync_s == 300.0
    assert s.memory.history_pairs == 20
    assert s.jellyfin.user_id == ""
    assert s.jellyfin.library_ids == []
    assert s.jellyfin.sync_interval_hours == 24.0


def test_repo_example_config_loads() -> None:
    example = Path(__file__).parents[1] / "config" / "config.example.yaml"
    s = load_config(example, use_dotenv=False)
    assert s.ollama.model == "qwen3:8b"
    assert s.ollama.num_ctx == 8192
    assert s.ollama.think is False
    assert s.agent.max_iterations == 3
    assert s.timeouts.tool_s == 30.0
    assert s.location.timezone == "Europe/Amsterdam"
    assert s.location.latitude == 52.09
    assert s.memory.history_pairs == 20
    assert s.timeouts.jellyfin_sync_s == 300.0
    assert s.jellyfin.sync_interval_hours == 24.0


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIMIR_OLLAMA_MODEL", "qwen3:14b")
    monkeypatch.setenv("MIMIR_OLLAMA_NUM_CTX", "16384")
    monkeypatch.setenv("MIMIR_OLLAMA_THINK", "true")
    monkeypatch.setenv("MIMIR_PORT", "9999")
    monkeypatch.setenv("MIMIR_DATA_DIR", "/tmp/mimir-data")
    monkeypatch.setenv("MIMIR_AGENT_MAX_ITERATIONS", "5")
    monkeypatch.setenv("MIMIR_TIMEOUT_TURN_S", "90")
    monkeypatch.setenv("MIMIR_TIMEZONE", "UTC")
    monkeypatch.setenv("MIMIR_HISTORY_PAIRS", "12")
    monkeypatch.setenv("MIMIR_JELLYFIN_USER_ID", "uid-9")
    monkeypatch.setenv("MIMIR_JELLYFIN_LIBRARY_IDS", "lib-a, lib-b")
    monkeypatch.setenv("MIMIR_JELLYFIN_SYNC_TIMEOUT_S", "120")
    s = load_config(write_config(tmp_path), use_dotenv=False)
    assert s.ollama.model == "qwen3:14b"
    assert s.ollama.num_ctx == 16384
    assert s.ollama.think is True
    assert s.runtime.port == 9999
    assert s.runtime.data_dir.as_posix().endswith("mimir-data")
    assert s.agent.max_iterations == 5
    assert s.timeouts.turn_s == 90.0
    assert s.location.timezone == "UTC"
    assert s.memory.history_pairs == 12
    assert s.jellyfin.user_id == "uid-9"
    assert s.jellyfin.library_ids == ["lib-a", "lib-b"]
    assert s.timeouts.jellyfin_sync_s == 120.0


def test_jellyfin_sync_configured_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.config import jellyfin_sync_configured

    monkeypatch.setenv("JELLYFIN_API_KEY", "k")
    yaml_text = VALID_YAML + (
        "jellyfin:\n"
        "  url: http://jf\n"
        "  user_id: u1\n"
        "  library_ids: [lib1]\n"
    )
    s = load_config(write_config(tmp_path, yaml_text), use_dotenv=False)
    assert jellyfin_sync_configured(s) is True


def test_secrets_come_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JELLYFIN_API_KEY", "sekrit")
    monkeypatch.setenv("MIMIR_AUTH_TOKEN", "tok-123")
    s = load_config(write_config(tmp_path), use_dotenv=False)
    assert s.jellyfin.api_key == "sekrit"
    assert s.auth.token == "tok-123"


def test_missing_file_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml", use_dotenv=False)


def test_invalid_value_fails_loud(tmp_path: Path) -> None:
    bad = VALID_YAML.replace("latitude: 51.5", 'latitude: "north"')
    with pytest.raises(ConfigError, match="latitude"):
        load_config(write_config(tmp_path, bad), use_dotenv=False)


def test_unknown_key_fails_loud(tmp_path: Path) -> None:
    typo = VALID_YAML + "jelyfin:\n  url: http://x\n"
    with pytest.raises(ConfigError, match="jelyfin"):
        load_config(write_config(tmp_path, typo), use_dotenv=False)


def test_redacted_view_masks_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.config import redacted_view

    monkeypatch.setenv("JELLYFIN_API_KEY", "sekrit")
    view = redacted_view(load_config(write_config(tmp_path), use_dotenv=False))
    assert view["jellyfin"]["api_key"] == "***set***"
