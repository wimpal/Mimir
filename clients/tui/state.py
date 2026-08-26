"""Persist Conversation id for the TUI Chat client."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChatState:
    conversation_id: str | None = None


def default_state_path() -> Path:
    override = os.environ.get("MIMIR_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser() / "chat_state.json"
    return Path.home() / ".mimir" / "chat_state.json"


def load_state(path: Path | None = None) -> ChatState:
    target = path or default_state_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ChatState()
    except OSError:
        return ChatState()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ChatState()

    if not isinstance(data, dict):
        return ChatState()
    cid = data.get("conversation_id")
    if cid is None:
        return ChatState()
    text = str(cid).strip()
    return ChatState(conversation_id=text or None)


def save_state(state: ChatState, path: Path | None = None) -> None:
    target = path or default_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"conversation_id": state.conversation_id},
        ensure_ascii=False,
        indent=2,
    )
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=".chat_state_",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
        tmp_path.replace(target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
