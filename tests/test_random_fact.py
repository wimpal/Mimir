"""Unit tests for random_fact (local curated list)."""

from __future__ import annotations

import json
import random
from pathlib import Path

from brain.config import Settings
from brain.tools import build_registry, dispatch
from brain.tools.random_fact import default_facts_path, load_facts, pick_fact


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        location={
            "latitude": 52.09,
            "longitude": 5.12,
            "timezone": "Europe/Amsterdam",
        },
        ollama={"url": "http://test", "model": "qwen3:8b"},
        runtime={"data_dir": data_dir},
        timeouts={"ollama_s": 30, "tool_s": 5, "turn_s": 60},
    )


def test_shipped_facts_file_loads() -> None:
    path = default_facts_path()
    facts = load_facts(path)
    assert len(facts) >= 20
    assert all(f["text_en"] and f["text_nl"] for f in facts)


def test_pick_fact_locale(tmp_path: Path) -> None:
    facts = [
        {"text_en": "English fact.", "text_nl": "Nederlands weetje."},
    ]
    en = pick_fact(facts, locale="en", rng=random.Random(0))
    nl = pick_fact(facts, locale="nl", rng=random.Random(0))
    assert en["fact"] == "English fact."
    assert nl["fact"] == "Nederlands weetje."
    assert en["source"] == "local"


def test_random_fact_dispatch(tmp_path: Path) -> None:
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(
        json.dumps(
            {
                "facts": [
                    {"text_en": "One.", "text_nl": "Een."},
                    {"text_en": "Two.", "text_nl": "Twee."},
                ]
            }
        ),
        encoding="utf-8",
    )
    from brain.tools.random_fact import random_fact_tools

    settings = _settings(tmp_path)
    reg = random_fact_tools(settings, facts_path=facts_path, rng=random.Random(1))
    out = dispatch("random_fact", {"locale": "nl"}, tools=reg)
    data = json.loads(out)
    assert data["locale"] == "nl"
    assert data["source"] == "local"
    assert data["fact"] in {"Een.", "Twee."}


def test_random_fact_empty_file(tmp_path: Path) -> None:
    facts_path = tmp_path / "empty.json"
    facts_path.write_text(json.dumps({"facts": []}), encoding="utf-8")
    from brain.tools.random_fact import random_fact_tools

    settings = _settings(tmp_path)
    reg = random_fact_tools(settings, facts_path=facts_path)
    out = dispatch("random_fact", {}, tools=reg)
    assert out.startswith("error: random facts unavailable")


def test_build_registry_includes_random_fact(tmp_path: Path) -> None:
    reg = build_registry(_settings(tmp_path))
    assert "random_fact" in reg
    out = dispatch("random_fact", {"locale": "en"}, tools=reg)
    data = json.loads(out)
    assert data["source"] == "local"
