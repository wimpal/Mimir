"""Tests for Ollama timing fields on turn steps."""

from __future__ import annotations

from brain.agent import StepTrace, _step_from_ollama_response
from brain.ollama import ChatMessage, ChatResponse, OllamaTimings, parse_ollama_timings


def test_parse_ollama_timings() -> None:
    t = parse_ollama_timings(
        {
            "load_duration": 1_000_000,
            "prompt_eval_count": 5,
            "prompt_eval_duration": 2_000_000,
            "eval_count": 3,
            "eval_duration": 500_000,
        }
    )
    assert t.load_duration_ms == 1.0
    assert t.prompt_eval_duration_ms == 2.0
    assert t.eval_duration_ms == 0.5


def test_step_from_ollama_response() -> None:
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content="ok"),
        timings=OllamaTimings(
            load_duration_ns=1_000_000,
            prompt_eval_count=10,
            prompt_eval_duration_ns=2_000_000,
            eval_count=4,
            eval_duration_ns=500_000,
        ),
    )
    step = _step_from_ollama_response(resp, ollama_latency_ms=123.0, tool_names=[], success=True)
    assert step.ollama_latency_ms == 123.0
    assert step.ollama_load_ms == 1.0
    assert step.ollama_prompt_eval_ms == 2.0
    assert step.ollama_eval_ms == 0.5
    assert step.ollama_prompt_tokens == 10
    assert step.ollama_eval_tokens == 4
