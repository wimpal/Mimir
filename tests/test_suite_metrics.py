"""Unit tests for suite metric aggregation (no live Ollama)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_suite():
    path = Path(__file__).resolve().parents[1] / "scripts" / "tool_call_suite.py"
    spec = importlib.util.spec_from_file_location("tool_call_suite", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_metric_rates() -> None:
    suite = _load_suite()
    CaseResult = suite.CaseResult
    results = [
        CaseResult(
            id="a",
            category="x",
            passed=True,
            reason="ok",
            latency_ms=1,
            right_tool=True,
            valid_args=True,
            result_used=True,
        ),
        CaseResult(
            id="b",
            category="x",
            passed=False,
            reason="tool_not_used_in_answer",
            latency_ms=1,
            right_tool=True,
            valid_args=True,
            result_used=False,
        ),
    ]
    rates = suite.metric_rates(results)
    assert rates["right_tool"] == 1.0
    assert rates["valid_args"] == 1.0
    assert rates["result_used"] == 0.5
