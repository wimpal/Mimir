"""Start the Mimir brain if it is not already reachable."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from brain.config import find_mimir_repo_root as find_repo_root

from clients.tui.brain_client import CONNECT_TIMEOUT_S, normalize_brain_url

DEFAULT_READY_TIMEOUT_S = 60.0
DEFAULT_POLL_S = 0.5

# Keep handles so GC does not close redirected log files while the brain runs.
_child_procs: list[subprocess.Popen[bytes]] = []
_log_handles: list[object] = []


@dataclass(frozen=True)
class LaunchResult:
    already_running: bool
    started: bool
    message: str
    pid: int | None = None


def host_port_from_url(url: str) -> tuple[str, int]:
    parsed = urlparse(normalize_brain_url(url))
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        return host, parsed.port
    if parsed.scheme == "https":
        return host, 443
    return host, 80


def brain_reachable(base_url: str, *, timeout_s: float = CONNECT_TIMEOUT_S) -> bool:
    url = normalize_brain_url(base_url) + "/health"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
    except httpx.HTTPError:
        return False
    return resp.status_code < 500


def _bind_host_port(base_url: str, *, repo_root: Path) -> tuple[str, int]:
    """Prefer ``runtime.host``/``port`` from config; fall back to the client URL."""
    url_host, url_port = host_port_from_url(base_url)
    try:
        from brain.config import load_config

        cfg_path = repo_root / "config" / "config.yaml"
        if not cfg_path.is_file():
            return url_host, url_port
        settings = load_config(cfg_path, use_dotenv=True)
        return settings.runtime.host, int(settings.runtime.port)
    except Exception:  # noqa: BLE001 — launcher must still start with URL bind
        return url_host, url_port


def start_brain_process(base_url: str, *, repo_root: Path | None = None) -> subprocess.Popen[bytes]:
    """Spawn ``uv run uvicorn …`` for the brain. Raises RuntimeError on setup failure."""
    root = repo_root or find_repo_root()
    if root is None:
        raise RuntimeError(
            "Could not find the Mimir repo (pyproject.toml + brain/). "
            "Set MIMIR_REPO_ROOT or keep mimir.exe under the repo (e.g. dist\\)."
        )

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError(
            "Could not find `uv` on PATH. Install uv or start the brain manually."
        )

    host, port = _bind_host_port(base_url, repo_root=root)
    log_dir = root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "brain_launch.log"
    log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    _log_handles.append(log_file)
    log_file.write(f"\n--- launching brain {host}:{port} ---\n")
    log_file.flush()

    cmd = [
        uv,
        "run",
        "uvicorn",
        "brain.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    creationflags = 0
    if sys.platform == "win32":
        # Hide the brain console; logs go to data/logs/brain_launch.log
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    proc = subprocess.Popen(
        cmd,
        cwd=str(root),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    _child_procs.append(proc)
    return proc


def ensure_brain_running(
    base_url: str,
    *,
    ready_timeout_s: float = DEFAULT_READY_TIMEOUT_S,
    poll_s: float = DEFAULT_POLL_S,
    repo_root: Path | None = None,
) -> LaunchResult:
    """Return when the brain answers /health, starting it if needed."""
    if brain_reachable(base_url):
        return LaunchResult(
            already_running=True,
            started=False,
            message="Brain already running.",
        )

    try:
        proc = start_brain_process(base_url, repo_root=repo_root)
    except RuntimeError as exc:
        return LaunchResult(
            already_running=False,
            started=False,
            message=str(exc),
        )

    deadline = time.monotonic() + max(1.0, ready_timeout_s)
    while time.monotonic() < deadline:
        if brain_reachable(base_url):
            return LaunchResult(
                already_running=False,
                started=True,
                message="Brain started.",
                pid=proc.pid,
            )
        if proc.poll() is not None:
            return LaunchResult(
                already_running=False,
                started=False,
                message=(
                    f"Brain process exited early (code {proc.returncode}). "
                    "See data/logs/brain_launch.log"
                ),
                pid=proc.pid,
            )
        time.sleep(poll_s)

    return LaunchResult(
        already_running=False,
        started=False,
        message=(
            f"Brain did not become ready within {ready_timeout_s:.0f}s. "
            "See data/logs/brain_launch.log"
        ),
        pid=proc.pid,
    )
