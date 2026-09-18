"""T-058 usage_stats aggregation and tool."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from brain.config import Settings
from brain.tools import build_registry, dispatch
from brain.turn_log import TURN_MILESTONES, aggregate_usage, count_voice_stt


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def test_aggregate_usage_empty_missing(tmp_path: Path) -> None:
    missing = tmp_path / "logs" / "turns.jsonl"
    out = aggregate_usage(missing)
    assert out["turn_count"] == 0
    assert out["tool_call_count"] == 0
    assert out["success_count"] == 0
    assert out["failed_count"] == 0
    assert out["top_tools"] == []
    assert out["milestones_reached"] == []
    assert out["window"]["kind"] == "all"


def test_aggregate_usage_fixture_counts(tmp_path: Path) -> None:
    now = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    path = tmp_path / "logs" / "turns.jsonl"
    _write_jsonl(
        path,
        [
            {
                "ts": (now - timedelta(days=2)).isoformat(),
                "success": True,
                "tools_used": ["get_weather", "get_calendar"],
            },
            {
                "ts": (now - timedelta(hours=1)).isoformat(),
                "success": False,
                "tools_used": ["get_weather"],
            },
            {
                "ts": (now - timedelta(minutes=5)).isoformat(),
                "success": True,
                "tools_used": [],
            },
            {
                "ts": "not-a-date",
                "success": True,
                "tools_used": ["echo"],
            },
        ],
    )
    all_time = aggregate_usage(path, now=now)
    assert all_time["turn_count"] == 4
    assert all_time["success_count"] == 3
    assert all_time["failed_count"] == 1
    assert all_time["tool_call_count"] == 4
    assert all_time["top_tools"][0] == {"name": "get_weather", "count": 2}

    windowed = aggregate_usage(path, days=1, now=now)
    assert windowed["window"]["kind"] == "days"
    assert windowed["window"]["days"] == 1
    assert windowed["turn_count"] == 2
    assert windowed["tool_call_count"] == 1


def test_aggregate_usage_milestones(tmp_path: Path) -> None:
    path = tmp_path / "turns.jsonl"
    records = [
        {
            "ts": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "success": True,
            "tools_used": [],
        }
        for _ in range(TURN_MILESTONES[0])
    ]
    _write_jsonl(path, records)
    out = aggregate_usage(path)
    assert out["turn_count"] == 100
    assert "100_turns" in out["milestones_reached"]
    assert "500_turns" not in out["milestones_reached"]


def test_count_voice_stt(tmp_path: Path) -> None:
    path = tmp_path / "voice.jsonl"
    now = datetime(2026, 9, 17, tzinfo=UTC)
    _write_jsonl(
        path,
        [
            {"ts": now.isoformat(), "op": "stt", "ok": True},
            {"ts": now.isoformat(), "op": "tts", "ok": True},
            {
                "ts": (now - timedelta(days=10)).isoformat(),
                "op": "stt",
                "ok": True,
            },
        ],
    )
    assert count_voice_stt(path) == 2
    assert count_voice_stt(path, since=now - timedelta(days=1)) == 1


def test_usage_stats_tool_dispatch(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    turns = data_dir / "logs" / "turns.jsonl"
    _write_jsonl(
        turns,
        [
            {
                "ts": datetime(2026, 9, 17, tzinfo=UTC).isoformat(),
                "success": True,
                "tools_used": ["random_fact"],
            }
        ],
    )
    voice = data_dir / "logs" / "voice.jsonl"
    _write_jsonl(
        voice,
        [{"ts": datetime(2026, 9, 17, tzinfo=UTC).isoformat(), "op": "stt"}],
    )
    settings = Settings(location={"latitude": 1.0, "longitude": 2.0})
    tools = build_registry(settings, data_dir=data_dir)
    assert "usage_stats" in tools
    raw = dispatch("usage_stats", {}, tools=tools)
    payload = json.loads(raw)
    assert payload["turn_count"] == 1
    assert payload["tool_call_count"] == 1
    assert payload["voice_stt_count"] == 1
    assert "voice_note" in payload


def test_format_usage_stats_reply_not_json() -> None:
    from brain.usage_reply import (
        format_usage_stats_reply,
        reply_looks_like_usage_json,
        user_asked_about_usage,
    )

    payload = {
        "turn_count": 73,
        "success_count": 72,
        "failed_count": 1,
        "tool_call_count": 31,
        "top_tools": [
            {"name": "get_weather", "count": 8},
            {"name": "homebase.lights.set_state", "count": 7},
        ],
        "window": {"kind": "days", "days": 7},
        "milestones_reached": [],
        "voice_stt_count": 3,
    }
    en = format_usage_stats_reply(payload, "en")
    assert "73 turns" in en
    assert "get_weather (8)" in en
    assert "Voice STT: 3" in en
    assert not en.strip().startswith("{")
    nl = format_usage_stats_reply(payload, "nl")
    assert "73 beurten" in nl
    assert "de afgelopen 7 dagen" in nl

    dumped = json.dumps(payload) + json.dumps(payload)
    assert reply_looks_like_usage_json(dumped)
    assert user_asked_about_usage("how busy have you been")
    assert user_asked_about_usage("Hoe druk ben je geweest?")


def test_run_turn_forces_usage_stats_spoken_reply() -> None:
    """After usage_stats, reply must be prose — not raw tool JSON (T-058 smoke)."""
    from brain.agent import run_turn
    from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
    from brain.tools import Tool
    from brain.usage_reply import format_usage_stats_reply

    payload = {
        "turn_count": 73,
        "success_count": 72,
        "failed_count": 1,
        "tool_call_count": 31,
        "top_tools": [{"name": "get_weather", "count": 8}],
        "window": {"kind": "days", "days": 7},
        "milestones_reached": [],
        "voice_stt_count": 3,
    }
    raw = json.dumps(payload, separators=(",", ":"))
    calls = {"n": 0}

    class Client:
        def chat(
            self,
            messages: list[ChatMessage],
            tools: list[dict] | None = None,
            *,
            think: bool = False,
            stream: bool = False,
        ) -> ChatResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return ChatResponse(
                    message=ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(
                                function=ToolCallFunction(
                                    name="usage_stats",
                                    arguments={"days": 7},
                                )
                            )
                        ],
                    )
                )
            # If we reach a second Ollama pass, model would dump JSON — must not happen.
            return ChatResponse(
                message=ChatMessage(role="assistant", content=raw + raw)
            )

    tool = Tool(
        name="usage_stats",
        description="usage",
        parameters={"type": "object", "properties": {}},
        execute=lambda **_: raw,
    )
    result = run_turn(
        Client(),  # type: ignore[arg-type]
        [
            ChatMessage(role="system", content="You are Mimir."),
            ChatMessage(role="user", content="how busy have you been"),
        ],
        tools={"usage_stats": tool},
        max_iterations=3,
    )
    expected = format_usage_stats_reply(payload, "en")
    assert result.content == expected
    assert not (result.content or "").strip().startswith("{")
    assert any(s.anomaly == "usage_stats_forced" for s in result.steps)
    assert calls["n"] == 1
