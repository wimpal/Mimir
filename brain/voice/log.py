"""Append-only voice request log (data/logs/voice.jsonl)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def voice_log_path(data_dir: Path) -> Path:
    return data_dir / "logs" / "voice.jsonl"


def append_voice_log(data_dir: Path, entry: dict[str, Any]) -> None:
    path = voice_log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(UTC).isoformat(), **entry}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
