"""Tests for brain_launcher ensure_brain_running (login / TUI backup path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from clients.tui.brain_launcher import LaunchResult, _bind_host_port, ensure_brain_running


def test_bind_host_port_reads_runtime_host_from_config(tmp_path: Path) -> None:
    repo = tmp_path / "mimir"
    cfg_dir = repo / "config"
    cfg_dir.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'mimir'\n", encoding="utf-8")
    (repo / "brain").mkdir()
    (cfg_dir / "config.yaml").write_text(
        "location:\n  latitude: 52.0\n  longitude: 5.0\n  timezone: Europe/Amsterdam\n"
        "runtime:\n  host: 0.0.0.0\n  port: 8000\n",
        encoding="utf-8",
    )

    host, port = _bind_host_port("http://127.0.0.1:8000", repo_root=repo)

    assert host == "0.0.0.0"
    assert port == 8000


def test_ensure_brain_running_skips_when_already_healthy() -> None:
    with patch("clients.tui.brain_launcher.brain_reachable", return_value=True):
        result = ensure_brain_running("http://127.0.0.1:8000")
    assert result.already_running is True
    assert result.started is False
    assert "already running" in result.message.lower()


def test_ensure_brain_running_starts_when_down() -> None:
    proc = MagicMock()
    proc.pid = 4242
    proc.poll.return_value = None

    with (
        patch("clients.tui.brain_launcher.brain_reachable", side_effect=[False, True]),
        patch("clients.tui.brain_launcher.start_brain_process", return_value=proc) as start,
    ):
        result = ensure_brain_running("http://127.0.0.1:8000", ready_timeout_s=5.0)

    assert result.started is True
    assert result.pid == 4242
    start.assert_called_once()
