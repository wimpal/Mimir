"""Offline tests for multi-turn follow-up cases in tool_call_suite."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from brain.agent import StoppedReason, TurnResult
from brain.ollama import ChatMessage, ToolCall, ToolCallFunction
from brain.tools import TOOLS


def _load_suite():
    path = Path(__file__).resolve().parents[1] / "scripts" / "tool_call_suite.py"
    spec = importlib.util.spec_from_file_location("tool_call_suite", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_case_messages_includes_prior_turns() -> None:
    suite = _load_suite()
    from brain.config import Settings

    settings = Settings(
        location={"latitude": 52.0, "longitude": 5.0, "timezone": "Europe/Amsterdam"},
        ollama={"url": "http://test", "model": "qwen3:8b"},
        runtime={"data_dir": Path("/tmp/mimir-test-data")},
    )
    case = suite.Case(
        id="x",
        category="followup_turn",
        prompt="en wim?",
        check=lambda _r: suite.CheckResult(True, True, True, "ok"),
        prior_turns=(
            ("first question", "first answer"),
        ),
    )
    messages = suite.build_case_messages(case, settings, TOOLS)
    roles = [m.role for m in messages]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 1
    assert messages[-1].content == "en wim?"
    assert "Current date and time" in messages[0].content
    assert "Follow-ups" in messages[0].content


def test_followup_budget_check_requires_wim_tool_call() -> None:
    suite = _load_suite()
    check = suite._require_followup_budget_wim()

    bad = TurnResult(
        content="Wim heeft deze maand €157,55 uitgegeven aan boodschappen.",
        messages=[
            ChatMessage(role="assistant", content="Wim heeft …"),
        ],
        steps=[],
        stopped_reason=StoppedReason.FINAL,
    )
    assert not check(bad).passed
    assert check(bad).reason == "no_tool_when_required"

    good = TurnResult(
        content="Wim heeft €99,00 uitgegeven aan boodschappen.",
        messages=[
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="budgettracker.transactions.search",
                            arguments=json.dumps(
                                {
                                    "person": "Wim",
                                    "category": "Boodschappen",
                                    "from": "2026-08-01",
                                    "to": "2026-08-31",
                                }
                            ),
                        )
                    )
                ],
            ),
            ChatMessage(
                role="tool",
                content='[{"amount":9900,"spent_euros":99.0,"person":"Wim"}]',
                tool_name="budgettracker.transactions.search",
            ),
        ],
        steps=[],
        stopped_reason=StoppedReason.FINAL,
    )
    assert check(good).passed


def test_followup_weather_check_requires_tool() -> None:
    suite = _load_suite()
    check = suite._require_followup_weather_tomorrow()
    payload = suite._suite_weather_payload()

    no_tool = TurnResult(
        content="Tomorrow will be mainly clear, about 22 degrees.",
        messages=[ChatMessage(role="assistant", content="Tomorrow …")],
        steps=[],
        stopped_reason=StoppedReason.FINAL,
    )
    assert not check(no_tool).passed

    with_tool = TurnResult(
        content="Tomorrow looks mainly clear, up to 22 degrees.",
        messages=[
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="get_weather",
                            arguments="{}",
                        )
                    )
                ],
            ),
            ChatMessage(role="tool", content=payload, tool_name="get_weather"),
        ],
        steps=[],
        stopped_reason=StoppedReason.FINAL,
    )
    assert check(with_tool).passed
