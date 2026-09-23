"""Offline weather checker accepts product fetch-failed copy (T-090)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from brain.agent import StepTrace, StoppedReason, TurnResult
from brain.ollama import ChatMessage


def _load_suite():
    path = Path(__file__).resolve().parents[1] / "scripts" / "tool_call_suite.py"
    spec = importlib.util.spec_from_file_location("tool_call_suite", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_weather_offline_accepts_couldnt_fetch_line() -> None:
    suite = _load_suite()
    check = suite._require_weather_offline_clear()
    fail_line = "I couldn" + "'t fetch the weather."
    result = TurnResult(
        content=fail_line,
        messages=[
            ChatMessage(
                role="tool",
                content="error: weather unavailable (offline)",
                tool_name="get_weather",
            ),
        ],
        steps=[StepTrace(ollama_latency_ms=1.0, tool_names=["get_weather"])],
        stopped_reason=StoppedReason.FINAL,
    )
    scored = check(result)
    assert scored.passed, scored.reason
