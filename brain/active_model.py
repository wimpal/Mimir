"""Sticky Active model profile selection (T-074 / ADR-016).

Persists the Active profile *name* under ``{data_dir}/active_model.json``.
Named profiles themselves live in config YAML; this file is the sticky switch.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_FILENAME = "active_model.json"


class ActiveModelStateError(RuntimeError):
    """Malformed or unreadable sticky Active model state."""


def active_model_path(data_dir: Path) -> Path:
    return Path(data_dir) / STATE_FILENAME


def load_active_profile(data_dir: Path) -> str | None:
    """Return the stored Active profile name, or None if the file is missing.

    Raises ActiveModelStateError on malformed JSON or wrong shape.
    """
    path = active_model_path(data_dir)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ActiveModelStateError(f"cannot read active model state {path}: {exc}") from exc
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ActiveModelStateError(
            f"invalid JSON in active model state {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ActiveModelStateError(
            f"active model state {path}: top level must be an object"
        )
    profile = data.get("profile")
    if not isinstance(profile, str) or not profile.strip():
        raise ActiveModelStateError(
            f"active model state {path}: 'profile' must be a non-empty string"
        )
    return profile.strip()


def write_active_profile(data_dir: Path, name: str) -> Path:
    """Atomically write Active profile name. Returns the state file path."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ActiveModelStateError("active profile name must be non-empty")
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = active_model_path(root)
    payload = json.dumps({"profile": cleaned}, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(root),
        prefix=".active_model_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
