"""System prompt loading — fail loud if missing."""

from __future__ import annotations

import hashlib
from pathlib import Path


class PromptError(RuntimeError):
    """System prompt file is missing or unreadable."""


def resolve_prompt_path(path: Path) -> Path:
    """Resolve relative prompt paths against the process working directory."""
    p = path.expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def load_system_prompt(path: Path) -> tuple[str, str]:
    """Load the system prompt text and a stable prompt_id (content hash).

    Returns ``(text, prompt_id)`` where ``prompt_id`` is ``sha256:<12 hex>``.
    """
    resolved = resolve_prompt_path(path)
    if not resolved.is_file():
        raise PromptError(f"system prompt not found: {resolved}")
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"cannot read system prompt {resolved}: {exc}") from exc
    if not text.strip():
        raise PromptError(f"system prompt is empty: {resolved}")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return text, f"sha256:{digest}"
