"""T-074: Model profiles + sticky Active model."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from brain.active_model import active_model_path, write_active_profile
from brain.config import ConfigError, load_config
from brain.model_profiles import main as model_profiles_main

PROFILES_YAML = """\
location:
  latitude: 51.5
  longitude: -0.12
ollama:
  model: qwen3:8b
  num_ctx: 8192
  think: false
  keep_alive: 45m
  profiles:
    default:
      model: qwen3:8b
    trial:
      model: qwen3:14b
      num_ctx: 4096
"""

MINIMAL_YAML = """\
location:
  latitude: 51.5
  longitude: -0.12
"""

_ENV_CLEAR = [
    "MIMIR_OLLAMA_MODEL",
    "MIMIR_OLLAMA_NUM_CTX",
    "MIMIR_OLLAMA_THINK",
    "MIMIR_OLLAMA_KEEP_ALIVE",
    "MIMIR_DATA_DIR",
    "MIMIR_CONFIG",
    "MIMIR_CLIENT_TOKEN",
    "MIMIR_AUTH_TOKEN",
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in _ENV_CLEAR:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MIMIR_DATA_DIR", str(tmp_path / "data"))


def _cfg(tmp_path: Path, text: str = PROFILES_YAML) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_no_profiles_synthesizes_default(tmp_path: Path) -> None:
    s = load_config(_cfg(tmp_path, MINIMAL_YAML), use_dotenv=False)
    assert s.ollama.active_profile == "default"
    assert s.ollama.model == "qwen3:8b"
    assert "default" in s.ollama.profiles
    assert s.ollama.profiles["default"].model == "qwen3:8b"


def test_sticky_active_resolves_trial(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_active_profile(data_dir, "trial")
    s = load_config(_cfg(tmp_path), use_dotenv=False)
    assert s.ollama.active_profile == "trial"
    assert s.ollama.model == "qwen3:14b"
    assert s.ollama.num_ctx == 4096


def test_persistence_across_reloads(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_active_profile(tmp_path / "data", "trial")
    first = load_config(cfg, use_dotenv=False)
    second = load_config(cfg, use_dotenv=False)
    assert first.ollama.model == second.ollama.model == "qwen3:14b"
    assert first.ollama.active_profile == "trial"


def test_unknown_active_fails_loud(tmp_path: Path) -> None:
    write_active_profile(tmp_path / "data", "gone")
    with pytest.raises(ConfigError, match="gone"):
        load_config(_cfg(tmp_path), use_dotenv=False)


def test_malformed_state_fails_loud(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    active_model_path(data_dir).write_text("{not-json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(_cfg(tmp_path), use_dotenv=False)


def test_malformed_state_wrong_shape(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    active_model_path(data_dir).write_text('{"profile": 3}', encoding="utf-8")
    with pytest.raises(ConfigError, match="non-empty string"):
        load_config(_cfg(tmp_path), use_dotenv=False)


def test_profiles_without_default_fails(tmp_path: Path) -> None:
    yaml_text = """\
location:
  latitude: 51.5
  longitude: -0.12
ollama:
  profiles:
    trial:
      model: qwen3:14b
"""
    with pytest.raises(ConfigError, match="default"):
        load_config(_cfg(tmp_path, yaml_text), use_dotenv=False)


def test_env_overlay_after_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_active_profile(tmp_path / "data", "trial")
    monkeypatch.setenv("MIMIR_OLLAMA_MODEL", "env-override:tag")
    monkeypatch.setenv("MIMIR_OLLAMA_NUM_CTX", "2048")
    s = load_config(_cfg(tmp_path), use_dotenv=False)
    assert s.ollama.active_profile == "trial"
    assert s.ollama.model == "env-override:tag"
    assert s.ollama.num_ctx == 2048
    assert "model" in s.ollama.env_masked_fields
    assert "num_ctx" in s.ollama.env_masked_fields


def test_cli_use_writes_state(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    rc = model_profiles_main(["--config", str(cfg), "use", "trial"])
    assert rc == 0
    s = load_config(cfg, use_dotenv=False)
    assert s.ollama.active_profile == "trial"
    assert s.ollama.model == "qwen3:14b"


def test_cli_use_default_recovers_stale(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    write_active_profile(tmp_path / "data", "deleted_profile")
    with pytest.raises(ConfigError):
        load_config(cfg, use_dotenv=False)
    rc = model_profiles_main(["--config", str(cfg), "use", "default"])
    assert rc == 0
    s = load_config(cfg, use_dotenv=False)
    assert s.ollama.active_profile == "default"
    assert s.ollama.model == "qwen3:8b"


def test_cli_use_restart_mocked(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with patch("brain.model_profiles._restart_brain", return_value=0) as restart:
        rc = model_profiles_main(
            ["--config", str(cfg), "use", "trial", "--restart"]
        )
    assert rc == 0
    restart.assert_called_once()
    s = load_config(cfg, use_dotenv=False)
    assert s.ollama.active_profile == "trial"


def test_cli_list_and_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _cfg(tmp_path)
    write_active_profile(tmp_path / "data", "trial")
    assert model_profiles_main(["--config", str(cfg), "list"]) == 0
    out = capsys.readouterr().out
    assert "trial:" in out
    assert "default:" in out
    assert model_profiles_main(["--config", str(cfg), "show"]) == 0
    out = capsys.readouterr().out
    assert "active_profile=trial" in out
    assert "model=qwen3:14b" in out
