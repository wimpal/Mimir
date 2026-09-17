"""random_fact — curated local facts (T-056). Offline by design."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Literal

from brain.config import Settings, find_mimir_repo_root
from brain.tools import Tool

Locale = Literal["nl", "en"]
DEFAULT_LOCALE: Locale = "nl"


def default_facts_path() -> Path:
    """Resolve config/random_facts.json from repo root or CWD."""
    root = find_mimir_repo_root()
    if root is not None:
        return root / "config" / "random_facts.json"
    return Path.cwd() / "config" / "random_facts.json"


def _normalize_locale(raw: str | None) -> Locale | str:
    if raw is None:
        return DEFAULT_LOCALE
    text = str(raw).strip().lower()
    if not text:
        return DEFAULT_LOCALE
    if text in {"nl", "en"}:
        return text  # type: ignore[return-value]
    if text.startswith("en"):
        return "en"
    if text.startswith("nl"):
        return "nl"
    return f"error: random facts unavailable (invalid locale {raw!r})"


def load_facts(path: Path) -> list[dict[str, str]]:
    """Load bilingual fact entries; raises ValueError on bad data."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"facts file missing: {path}") from exc
    except OSError as exc:
        raise ValueError(f"facts file unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"facts file corrupt: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("facts file must be an object with a facts array")
    facts = raw.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ValueError("facts list empty")
    out: list[dict[str, str]] = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        text_en = str(item.get("text_en") or "").strip()
        text_nl = str(item.get("text_nl") or "").strip()
        if text_en and text_nl:
            out.append({"text_en": text_en, "text_nl": text_nl})
    if not out:
        raise ValueError("no valid bilingual facts")
    return out


def pick_fact(
    facts: list[dict[str, str]],
    *,
    locale: Locale,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    chooser = rng if rng is not None else random
    entry = chooser.choice(facts)
    text = entry["text_nl"] if locale == "nl" else entry["text_en"]
    return {"fact": text, "locale": locale, "source": "local"}


def _execute_random_fact(
    *,
    locale: str | None,
    facts_path: Path,
    rng: random.Random | None = None,
) -> str:
    loc_or_err = _normalize_locale(locale)
    if isinstance(loc_or_err, str) and loc_or_err.startswith("error:"):
        return loc_or_err
    loc: Locale = loc_or_err  # type: ignore[assignment]
    try:
        facts = load_facts(facts_path)
        payload = pick_fact(facts, locale=loc, rng=rng)
        return json.dumps(payload, separators=(",", ":"))
    except ValueError as exc:
        return f"error: random facts unavailable ({exc})"


def make_random_fact_tool(
    settings: Settings,
    *,
    facts_path: Path | None = None,
    rng: random.Random | None = None,
) -> Tool:
    path = facts_path if facts_path is not None else default_facts_path()
    # settings reserved for future config path; silence unused in v1
    _ = settings

    def execute(*, locale: str | None = None) -> str:
        return _execute_random_fact(locale=locale, facts_path=path, rng=rng)

    return Tool(
        name="random_fact",
        description=(
            "Return one random fact from a curated local list (works offline). "
            "Use when the user asks for a random fact / weetje. "
            "Pass locale nl or en matching the user (default nl). "
            "Never call the network for this tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "locale": {
                    "type": "string",
                    "enum": ["nl", "en"],
                    "description": "Language of the fact; match the user (default nl).",
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
    )


def random_fact_tools(
    settings: Settings,
    *,
    facts_path: Path | None = None,
    rng: random.Random | None = None,
) -> dict[str, Tool]:
    tool = make_random_fact_tool(settings, facts_path=facts_path, rng=rng)
    return {tool.name: tool}
