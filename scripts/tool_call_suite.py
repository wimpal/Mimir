"""Phase 1–3 exit criterion: scripted tool-call suite against live Ollama.

Exit 0 if pass rate >= 80%, else 1.

Reports right_tool / valid_args / result_used separately (ROADMAP quality metrics).

  uv run python scripts/tool_call_suite.py
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.agent import StoppedReason, TurnResult, run_turn
from brain.config import ConfigError, Settings, load_config
from brain.db import Database, Movie
from brain.ollama import ChatMessage, OllamaClient
from brain.prefs import build_system_prompt
from brain.tools import Tool, build_registry, tool_schemas
from brain.turn_log import turns_log_path


def _iso_days_ago(days: float) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixture_catalogue_movies() -> list[Movie]:
    return [
        Movie(
            jellyfin_id="br",
            name="Blade Runner",
            year=1982,
            genres=["sci-fi", "thriller"],
            community_rating=8.5,
            overview="Replicants in Los Angeles.",
            director="Ridley Scott",
            cast=["Harrison Ford"],
            played=True,
            last_played_at=_iso_days_ago(3),
        ),
        Movie(
            jellyfin_id="br2049",
            name="Blade Runner 2049",
            year=2017,
            genres=["sci-fi", "thriller"],
            community_rating=8.0,
            played=False,
            playback_position_ticks=5000,
            last_played_at=_iso_days_ago(1),
        ),
        Movie(
            jellyfin_id="ghost",
            name="Ghost in the Shell",
            year=1995,
            genres=["sci-fi", "action"],
            community_rating=7.9,
            played=False,
        ),
        Movie(
            jellyfin_id="haunt",
            name="The Haunting",
            year=1999,
            genres=["horror"],
            community_rating=5.0,
            played=False,
        ),
        Movie(
            jellyfin_id="blade1",
            name="Blade",
            year=1998,
            genres=["action", "horror"],
            community_rating=6.5,
            played=False,
        ),
    ]


PASS_THRESHOLD = 0.80
PROMPT_PATH = Path("config/system_prompt.md")


@dataclass
class CheckResult:
    right_tool: bool
    valid_args: bool
    result_used: bool
    reason: str

    @property
    def passed(self) -> bool:
        return self.right_tool and self.valid_args and self.result_used


@dataclass
class Case:
    id: str
    category: str
    prompt: str
    check: Callable[[TurnResult], CheckResult]
    prior_turns: tuple[tuple[str, str], ...] = ()
    conversation_id: str | None = None
    plant_trace: dict[str, Any] | None = None


@dataclass
class CaseResult:
    id: str
    category: str
    passed: bool
    reason: str
    latency_ms: float
    right_tool: bool
    valid_args: bool
    result_used: bool
    tool_sequence: list[str] = field(default_factory=list)
    content_preview: str = ""


def _ok() -> CheckResult:
    return CheckResult(True, True, True, "ok")


def _fail_right(reason: str) -> CheckResult:
    return CheckResult(False, True, True, reason)


def _fail_args(reason: str) -> CheckResult:
    return CheckResult(True, False, True, reason)


def _fail_used(reason: str) -> CheckResult:
    return CheckResult(True, True, False, reason)


def _fail_all(reason: str) -> CheckResult:
    return CheckResult(False, False, False, reason)


def _tools_called(result: TurnResult) -> list[str]:
    seq = result.tools_used()
    if seq:
        return seq
    names: list[str] = []
    for m in result.messages:
        if m.role != "assistant":
            continue
        for tc in m.tool_calls:
            names.append(tc.function.name)
    return names


def _tool_call_args(result: TurnResult, tool_name: str) -> list[dict]:
    out: list[dict] = []
    for m in result.messages:
        if m.role != "assistant":
            continue
        for tc in m.tool_calls:
            if tc.function.name != tool_name:
                continue
            raw = tc.function.arguments
            if isinstance(raw, dict):
                out.append(raw)
                continue
            try:
                out.append(json.loads(raw or "{}"))
            except json.JSONDecodeError:
                out.append({})
    return out


def _suite_weather_payload() -> str:
    return json.dumps(
        {
            "current": {
                "time": "2026-08-28T12:00",
                "temperature_c": 18.0,
                "conditions": "overcast",
                "precipitation_mm": 0.1,
            },
            "today": {
                "date": "2026-08-28",
                "temp_max_c": 19.0,
                "temp_min_c": 12.0,
                "conditions": "overcast",
                "precipitation_mm": 0.5,
            },
            "tomorrow": {
                "date": "2026-08-29",
                "temp_max_c": 22.0,
                "temp_min_c": 14.0,
                "conditions": "mainly clear",
                "precipitation_mm": 0.0,
            },
            "fetched_at": "2026-08-28T10:00:00+00:00",
            "stale": False,
        },
        separators=(",", ":"),
    )


def _budget_search_execute(**kwargs: object) -> str:
    person = str(kwargs.get("person") or "").strip().lower()
    if person == "ilse":
        return json.dumps(
            [
                {
                    "amount": 15755,
                    "spent_euros": 157.55,
                    "category": "Boodschappen",
                    "person": "Ilse",
                    "currency": "EUR",
                }
            ]
        )
    if person == "wim":
        return json.dumps(
            [
                {
                    "amount": 9900,
                    "spent_euros": 99.0,
                    "category": "Boodschappen",
                    "person": "Wim",
                    "currency": "EUR",
                }
            ]
        )
    return "[]"


def _suite_budget_registry(settings: Settings, db: Database) -> dict[str, Tool]:
    base = build_registry(
        settings,
        db=db,
        weather_fetch_override=_suite_weather_payload,
        calendar_fetch_override=_suite_calendar_ok,
        currency_fetch_override=_suite_currency_payload,
        wikipedia_fetch_override=_suite_wikipedia_payload,
    )
    search = Tool(
        name="budgettracker.transactions.search",
        description=(
            "Search household expenses by text, category, person, and date range. "
            "Amounts are integer cents; use spent_euros in replies."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "person": {"type": "string"},
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
        },
        execute=_budget_search_execute,
        service="budgettracker",
    )
    base[search.name] = search
    return base


def _require_followup_weather_tomorrow() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "get_weather" not in _tools_called(result):
            return _fail_right("no_tool_when_required")
        payload = _weather_payload(result)
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        tomorrow = data.get("tomorrow") or {}
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        tmax = tomorrow.get("temp_max_c")
        tmin = tomorrow.get("temp_min_c")
        cond = (tomorrow.get("conditions") or "").lower()
        if (
            _temp_in_content(tmax, content)
            or _temp_in_content(tmin, content)
            or _condition_in_content(cond, content)
            or "morgen" in content
            or "tomorrow" in content
        ):
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_followup_budget_wim() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = _tools_called(result)
        tool_name = None
        if "budgettracker.transactions.search" in seq:
            tool_name = "budgettracker.transactions.search"
        elif "budgettracker.summary.by_category" in seq:
            tool_name = "budgettracker.summary.by_category"
        if tool_name is None:
            return _fail_right("no_tool_when_required")
        arg_sets = _tool_call_args(result, tool_name)
        if not arg_sets:
            return _fail_args("malformed_args")
        person_ok = any(
            "wim" in str(args.get("person") or "").lower() for args in arg_sets
        )
        if not person_ok:
            return _fail_args("missing_person_wim")
        content = result.content or ""
        if "99" in content.replace(",", "."):
            return _ok()
        if "9900" in content:
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _weather_payload(result: TurnResult) -> str | None:
    for m in result.messages:
        if m.role == "tool" and m.tool_name == "get_weather":
            return m.content
    return None


def _tool_payload(result: TurnResult, tool_name: str) -> str | None:
    for m in result.messages:
        if m.role == "tool" and m.tool_name == tool_name:
            return m.content
    return None


def _calendar_payload(result: TurnResult) -> str | None:
    for m in result.messages:
        if m.role == "tool" and m.tool_name == "get_calendar":
            return m.content
    return None


def _temp_in_content(temp: float | int | None, content: str) -> bool:
    if temp is None:
        return False
    return (
        str(int(temp)) in content
        or str(temp) in content
        or f"{float(temp):.0f}" in content
        or f"{float(temp):.1f}" in content
    )


def _condition_in_content(conditions: str, content: str) -> bool:
    if not conditions:
        return False
    return any(w in content for w in conditions.lower().split() if len(w) > 3)


def _require_echo(expected: str) -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "echo" not in seq:
            return _fail_right("no_tool_when_required")
        echoed = False
        for m in result.messages:
            if m.role == "tool" and m.tool_name == "echo" and expected in m.content:
                echoed = True
        if not echoed:
            return _fail_args("malformed_args")
        if expected not in (result.content or ""):
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _require_time_grounded() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "get_server_time" not in seq:
            return _fail_right("no_tool_when_required")
        if "echo" in seq and seq == ["echo"]:
            return _fail_right("unexpected_tool")
        time_payload = None
        for m in result.messages:
            if m.role == "tool" and m.tool_name == "get_server_time":
                time_payload = m.content
        if not time_payload or "T" not in time_payload:
            return _fail_args("malformed_args")
        content = result.content or ""
        year = time_payload[:4]
        hhmm = time_payload[11:16]
        if year not in content and hhmm not in content:
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _no_tools() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if result.tools_used():
            return _fail_right("unexpected_tool")
        if not (result.content or "").strip():
            return _fail_used("empty_response")
        return _ok()

    return check


def _require_eli5_shape() -> Callable[[TurnResult], CheckResult]:
    """ELI5: no invented tools; non-empty plain reply (prompt mode)."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if _tools_called(result):
            return _fail_right("unexpected_tool")
        content = (result.content or "").strip()
        if not content:
            return _fail_used("empty_response")
        # Heuristic: keep it short — ELI5 should not essay.
        if len(content) > 900:
            return _fail_used("reply_too_long_for_eli5")
        # Fluff the operator rejected (sanitizer + prompt should prevent these).
        if any(ch in content for ch in ("\U0001f35e", "\u2728", "🍞", "✨")):
            return _fail_used("eli5_emoji_fluff")
        return _ok()

    return check


def _require_two_sided_shape() -> Callable[[TurnResult], CheckResult]:
    """Two-sided: no invented tools; both-side markers in the reply."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if _tools_called(result):
            return _fail_right("unexpected_tool")
        content = (result.content or "").strip()
        if not content:
            return _fail_used("empty_response")
        lower = content.lower()
        markers = (
            "side a",
            "side b",
            "kant a",
            "kant b",
            "voordeel",
            "nadeel",
            "pros",
            "cons",
            "on the one hand",
            "on the other",
            "enerzijds",
            "anderzijds",
        )
        if not any(m in lower for m in markers):
            return _fail_used("missing_two_sided_markers")
        return _ok()

    return check


def _require_explain_cites_tool(tool_name: str) -> Callable[[TurnResult], CheckResult]:
    """Explain-yourself short-circuit: cite tool, no new tools."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if _tools_called(result):
            return _fail_right("unexpected_tool")
        content = (result.content or "").strip()
        if not content:
            return _fail_used("empty_response")
        if tool_name not in content:
            return _fail_used(f"missing_tool_cite:{tool_name}")
        if not any(s.anomaly == "explain_yourself" for s in result.steps):
            return _fail_used("missing_explain_yourself_short_circuit")
        return _ok()

    return check


def _time_not_echo() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if result.stopped_reason != StoppedReason.FINAL:
            return _fail_all(str(result.stopped_reason))
        seq = result.tools_used()
        if "get_server_time" not in seq:
            return _fail_right("no_tool_when_required")
        if "echo" in seq:
            return _fail_right("unexpected_tool")
        if not (result.content or "").strip():
            return _fail_used("empty_response")
        return _ok()

    return check


def _multi_step_echo_time() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if result.stopped_reason == StoppedReason.MAX_ITERATIONS:
            return _fail_all("max_iterations")
        if result.stopped_reason != StoppedReason.FINAL:
            return _fail_all(str(result.stopped_reason))
        seq = result.tools_used()
        if "get_server_time" not in seq:
            return _fail_right("no_tool_when_required")
        if "echo" not in seq:
            return _fail_right("no_tool_when_required")
        if seq.index("get_server_time") > seq.index("echo"):
            return _fail_right("unexpected_tool")
        time_payload = next(
            (
                m.content
                for m in result.messages
                if m.role == "tool" and m.tool_name == "get_server_time"
            ),
            None,
        )
        if not time_payload or time_payload.startswith("error:"):
            return _fail_args("malformed_args")
        echo_payload = next(
            (
                m.content
                for m in result.messages
                if m.role == "tool" and m.tool_name == "echo"
            ),
            "",
        )
        content = result.content or ""
        if time_payload not in echo_payload and time_payload[:16] not in content:
            year = time_payload[:4]
            hhmm = time_payload[11:16]
            if year not in content and hhmm not in content:
                return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _no_crash_vague() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if result.stopped_reason == StoppedReason.EMPTY_RESPONSE:
            return _fail_used("empty_response")
        if result.stopped_reason == StoppedReason.FINAL and (result.content or "").strip():
            return _ok()
        if result.stopped_reason == StoppedReason.MAX_ITERATIONS:
            return _fail_all("max_iterations")
        return _fail_all(str(result.stopped_reason))

    return check


def _require_weather_grounded() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "get_weather" not in seq:
            return _fail_right("no_tool_when_required")
        payload = _weather_payload(result)
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        temp = data.get("current", {}).get("temperature_c")
        conditions = (data.get("current", {}).get("conditions") or "").lower()
        if _temp_in_content(temp, content) or _condition_in_content(conditions, content):
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_weather_umbrella() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "get_weather" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _weather_payload(result)
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        rain_words = (
            "umbrella",
            "rain",
            "precip",
            "dry",
            "wet",
            "shower",
            "drizzle",
            "mm",
        )
        mentions_rain = any(w in content for w in rain_words)
        precip = data.get("current", {}).get("precipitation_mm")
        today_precip = (data.get("today") or {}).get("precipitation_mm")
        precip_token = False
        for val in (precip, today_precip):
            if _temp_in_content(val, content):
                precip_token = True
        if mentions_rain or precip_token:
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_weather_tomorrow() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "get_weather" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _weather_payload(result)
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        tomorrow = data.get("tomorrow") or {}
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        tmax = tomorrow.get("temp_max_c")
        tmin = tomorrow.get("temp_min_c")
        cond = (tomorrow.get("conditions") or "").lower()
        if (
            _temp_in_content(tmax, content)
            or _temp_in_content(tmin, content)
            or _condition_in_content(cond, content)
        ):
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_weather_offline_clear() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "get_weather" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _weather_payload(result)
        if not payload or not payload.startswith("error:"):
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        fail_words = (
            "unavailable",
            "unable",
            "can't",
            "cannot",
            "failed",
            "error",
            "offline",
            "reach",
            "timeout",
            "timed out",
            "down",
        )
        if not any(w in content for w in fail_words):
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _echo_not_weather(expected: str) -> Callable[[TurnResult], CheckResult]:
    """Control: echo works; get_weather must not be called."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "get_weather" in seq:
            return _fail_right("unexpected_tool")
        if "echo" not in seq:
            return _fail_right("no_tool_when_required")
        echoed = any(
            m.role == "tool" and m.tool_name == "echo" and expected in m.content
            for m in result.messages
        )
        if not echoed:
            return _fail_args("malformed_args")
        if not (result.content or "").strip():
            return _fail_used("empty_response")
        return _ok()

    return check


def _suite_currency_payload() -> str:
    return json.dumps(
        {
            "amount": "50.00",
            "from_currency": "USD",
            "to_currency": "EUR",
            "converted": "45.12",
            "rate": "0.9024",
            "rate_date": "2026-09-15",
            "source": "frankfurter/ecb",
        },
        separators=(",", ":"),
    )


def _suite_wikipedia_payload() -> str:
    return json.dumps(
        {
            "title": "Fiets",
            "extract": "Een fiets is een voertuig met twee wielen.",
            "url": "https://nl.wikipedia.org/wiki/Fiets",
            "language": "nl",
        },
        separators=(",", ":"),
    )


def _require_currency_grounded() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "convert_currency" not in _tools_called(result):
            return _fail_right("no_tool_when_required")
        if any(n.startswith("budgettracker.") for n in _tools_called(result)):
            return _fail_right("unexpected_tool")
        payload = _tool_payload(result, "convert_currency")
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        converted = str(data.get("converted") or "")
        rate_date = str(data.get("rate_date") or "")
        if ("45" in content or converted.replace(".", ",") in content or converted in content) and (
            rate_date in content or "2026-09-15" in content or "15" in content
        ):
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_currency_offline_clear() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "convert_currency" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _tool_payload(result, "convert_currency")
        if not payload or not payload.startswith("error:"):
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        fail_words = (
            "unavailable",
            "unable",
            "can't",
            "cannot",
            "failed",
            "error",
            "offline",
            "reach",
            "timeout",
            "timed out",
            "down",
            "niet",
            "onbereikbaar",
        )
        if not any(w in content for w in fail_words):
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _require_wikipedia_grounded() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "wikipedia_lookup" not in _tools_called(result):
            return _fail_right("no_tool_when_required")
        payload = _tool_payload(result, "wikipedia_lookup")
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        title = str(data.get("title") or "").lower()
        url = str(data.get("url") or "").lower()
        if title and title in content and (
            "wikipedia" in content
            or "wiki" in content
            or (url and ("http" in content or "wikipedia.org" in content))
        ):
            return _ok()
        if "fiets" in content and ("wikipedia" in content or "wiki" in content):
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_wikipedia_offline_clear() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "wikipedia_lookup" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _tool_payload(result, "wikipedia_lookup")
        if not payload or not payload.startswith("error:"):
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        fail_words = (
            "unavailable",
            "unable",
            "can't",
            "cannot",
            "failed",
            "error",
            "offline",
            "reach",
            "timeout",
            "timed out",
            "down",
            "niet",
            "onbereikbaar",
        )
        if not content.strip() or not any(w in content for w in fail_words):
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _require_random_fact_local() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "random_fact" not in _tools_called(result):
            return _fail_right("no_tool_when_required")
        payload = _tool_payload(result, "random_fact")
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        if data.get("source") != "local":
            return _fail_args("malformed_args")
        fact = str(data.get("fact") or "").strip()
        content = (result.content or "").strip()
        if not content:
            return _fail_used("empty_response")
        needle = fact[:24] if len(fact) >= 24 else fact
        if needle and needle.lower() in content.lower():
            return _ok()
        words = [w for w in fact.lower().split() if len(w) > 4][:3]
        if words and all(w in content.lower() for w in words):
            return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_calendar_grounded() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "get_calendar" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _calendar_payload(result)
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        events = data.get("events") or []
        if not isinstance(events, list) or not events:
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        for ev in events:
            summary = str(ev.get("summary") or "").strip()
            start = str(ev.get("start") or "").strip()
            if summary and summary.lower() in content:
                return _ok()
            # Ground on a time fragment from the event start (HH:MM or date).
            if len(start) >= 10 and start[:10] in content:
                return _ok()
            if "T" in start:
                hhmm = start[11:16] if len(start) >= 16 else ""
                if hhmm and hhmm in content:
                    return _ok()
        return _fail_used("tool_not_used_in_answer")

    return check


def _require_calendar_offline_clear() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "get_calendar" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _calendar_payload(result)
        if not payload or not payload.startswith("error:"):
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        fail_words = (
            "unavailable",
            "unable",
            "can't",
            "cannot",
            "failed",
            "error",
            "offline",
            "reach",
            "timeout",
            "timed out",
            "down",
            "configured",
            "calendar",
        )
        if not any(w in content for w in fail_words):
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _echo_not_calendar(expected: str) -> Callable[[TurnResult], CheckResult]:
    """Control: echo works; get_calendar must not be called."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "get_calendar" in seq:
            return _fail_right("unexpected_tool")
        if "echo" not in seq:
            return _fail_right("no_tool_when_required")
        echoed = any(
            m.role == "tool" and m.tool_name == "echo" and expected in m.content
            for m in result.messages
        )
        if not echoed:
            return _fail_args("malformed_args")
        if not (result.content or "").strip():
            return _fail_used("empty_response")
        return _ok()

    return check


def _event_in_content(ev: dict[str, Any], content: str) -> bool:
    summary = str(ev.get("summary") or "").strip()
    start = str(ev.get("start") or "").strip()
    if summary and summary.lower() in content:
        return True
    if len(start) >= 10 and start[:10] in content:
        return True
    if "T" in start:
        hhmm = start[11:16] if len(start) >= 16 else ""
        if hhmm and hhmm in content:
            return True
    return False


_FALSE_EMPTY_EN_SUITE: tuple[str, ...] = (
    "nothing on the calendar",
    "no events on the calendar",
    "calendar is clear",
    "schedule is clear",
    "nothing scheduled",
    "clear schedule",
    "no appointments",
)

_FALSE_EMPTY_NL_SUITE: tuple[str, ...] = (
    "niets op de agenda",
    "geen afspraken",
    "agenda is leeg",
    "niets gepland",
)


def _reply_falsely_empty(content: str, *, locale: str) -> bool:
    lowered = content.lower()
    patterns = _FALSE_EMPTY_NL_SUITE if locale == "nl" else _FALSE_EMPTY_EN_SUITE
    return any(p in lowered for p in patterns)


def _require_morning_brief(*, locale: str = "en") -> Callable[[TurnResult], CheckResult]:
    """Both get_weather and get_calendar; reply grounded in each payload."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "get_weather" not in seq or "get_calendar" not in seq:
            return _fail_right("no_tool_when_required")
        weather_raw = _weather_payload(result)
        calendar_raw = _calendar_payload(result)
        if (
            not weather_raw
            or weather_raw.startswith("error:")
            or not calendar_raw
            or calendar_raw.startswith("error:")
        ):
            return _fail_args("malformed_args")
        try:
            weather = json.loads(weather_raw)
            calendar = json.loads(calendar_raw)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        temp = weather.get("current", {}).get("temperature_c")
        conditions = (weather.get("current", {}).get("conditions") or "").lower()
        weather_ok = _temp_in_content(temp, content) or _condition_in_content(
            conditions, content
        )
        if not weather_ok:
            return _fail_used("tool_not_used_in_answer")
        events = calendar.get("events") or []
        if not isinstance(events, list) or not events:
            return _fail_args("malformed_args")
        if _reply_falsely_empty(content, locale=locale):
            return _fail_used("false_empty_calendar")
        for ev in events:
            if not _event_in_content(ev, content):
                return _fail_used("calendar_event_missing")
        return _ok()

    return check


def _require_morning_brief_empty_day() -> Callable[[TurnResult], CheckResult]:
    """Morning brief when calendar has no events — must say schedule is clear."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "get_weather" not in seq or "get_calendar" not in seq:
            return _fail_right("no_tool_when_required")
        calendar_raw = _calendar_payload(result)
        if not calendar_raw or calendar_raw.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            calendar = json.loads(calendar_raw)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        events = calendar.get("events") or []
        if events:
            return _fail_args("expected_empty_calendar")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        if not _reply_falsely_empty(content, locale="en"):
            return _fail_used("missing_clear_schedule_line")
        for title in ("standup", "away day", "verjaardag"):
            if title in content:
                return _fail_used("invented_calendar_event")
        return _ok()

    return check


_ENGLISH_LEAK_IN_DUTCH: tuple[str, ...] = (
    "it is overcast",
    "the rest of the day",
    "heavy rain",
    "good morning",
    "temperature of",
    "scheduled for",
    "you have a family event",
    "with a temperature",
    "will see",
)


def _require_morning_brief_dutch() -> Callable[[TurnResult], CheckResult]:
    """Morning brief in Dutch — same grounding as EN, no English mid-reply."""

    base = _require_morning_brief(locale="nl")

    def check(result: TurnResult) -> CheckResult:
        grounded = base(result)
        if not grounded.passed:
            return grounded
        content = (result.content or "").lower()
        if "goedemorgen" not in content:
            return _fail_used("reply_missing_dutch_greeting")
        for pat in _ENGLISH_LEAK_IN_DUTCH:
            if pat in content:
                return _fail_used(f"english_in_dutch_reply:{pat}")
        return _ok()

    return check


def _require_evening_wind_down(*, locale: str = "en") -> Callable[[TurnResult], CheckResult]:
    """Weather + tomorrow calendar; short good-night reply; no false-empty calendar."""

    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "get_weather" not in seq or "get_calendar" not in seq:
            return _fail_right("no_tool_when_required")
        weather_raw = _weather_payload(result)
        calendar_raw = _calendar_payload(result)
        if (
            not weather_raw
            or weather_raw.startswith("error:")
            or not calendar_raw
            or calendar_raw.startswith("error:")
        ):
            return _fail_args("malformed_args")
        try:
            calendar = json.loads(calendar_raw)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        if calendar.get("day_offset") != 1:
            return _fail_args("calendar_not_tomorrow")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        greeting_ok = (
            ("welterusten" in content or "goedenacht" in content)
            if locale == "nl"
            else ("good night" in content or "goodnight" in content)
        )
        if not greeting_ok:
            return _fail_used("missing_greeting")
        events = calendar.get("events") or []
        if events and _reply_falsely_empty(content, locale=locale):
            return _fail_used("false_empty_calendar")
        if events:
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                summary = (ev.get("summary") or "").lower()
                if not summary:
                    continue
                if summary in content:
                    continue
                tokens = [t for t in summary.split() if len(t) >= 3]
                if tokens and not all(t in content for t in tokens):
                    return _fail_used("calendar_not_grounded")
        return _ok()

    return check


def _require_evening_wind_down_dutch() -> Callable[[TurnResult], CheckResult]:
    return _require_evening_wind_down(locale="nl")


def _require_set_preference(key: str, needle: str) -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "set_preference" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        stored = False
        for m in result.messages:
            if m.role != "tool" or m.tool_name != "set_preference":
                continue
            if m.content.startswith("error:"):
                return _fail_args("malformed_args")
            if key in m.content and needle in m.content:
                stored = True
        if not stored:
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        if needle.lower() not in content and "prefer" not in content and "noted" not in content:
            # Soft: reply should acknowledge; accept if tool ok and any reply
            if len(content) < 3:
                return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _echo_not_preference(expected: str) -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "set_preference" in seq or "get_preference" in seq:
            return _fail_right("unexpected_tool")
        if "echo" not in seq:
            return _fail_right("no_tool_when_required")
        echoed = any(
            m.role == "tool" and m.tool_name == "echo" and expected in m.content
            for m in result.messages
        )
        if not echoed:
            return _fail_args("malformed_args")
        if not (result.content or "").strip():
            return _fail_used("empty_response")
        return _ok()

    return check


def _recommend_payload(result: TurnResult) -> str | None:
    for m in result.messages:
        if m.role == "tool" and m.tool_name == "recommend_movies":
            return m.content
    return None


def _require_recommend_titles(*needles: str) -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "recommend_movies" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _recommend_payload(result)
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        if data.get("ambiguous_seed") or data.get("no_matches"):
            return _fail_args("malformed_args")
        names = [m.get("name", "") for m in data.get("movies") or []]
        content = result.content or ""
        if not content.strip():
            return _fail_used("empty_response")
        grounded = any(n and n in content for n in names) or any(
            needle in content for needle in needles
        )
        if not grounded:
            return _fail_used("tool_not_used_in_answer")
        # Titles in the reply should come from the fixture Catalogue subset
        for needle in needles:
            if needle in content and needle not in names and needle not in (payload or ""):
                # Allow mentioning seed title even if excluded from subset
                if needle == "Blade Runner":
                    continue
                return _fail_used("invented_title")
        return _ok()

    return check


def _require_recommend_empty_clear() -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "recommend_movies" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _recommend_payload(result)
        if not payload or not payload.startswith("error:"):
            return _fail_args("malformed_args")
        content = (result.content or "").lower()
        if not content.strip():
            return _fail_used("empty_response")
        fail_words = (
            "empty",
            "sync",
            "unavailable",
            "catalogue",
            "catalog",
            "library",
            "no movie",
            "cannot",
            "can't",
            "unable",
        )
        if not any(w in content for w in fail_words):
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


def _echo_not_recommend(expected: str) -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        seq = result.tools_used()
        if "recommend_movies" in seq or "list_recently_watched" in seq:
            return _fail_right("unexpected_tool")
        if "echo" not in seq:
            return _fail_right("no_tool_when_required")
        echoed = any(
            m.role == "tool" and m.tool_name == "echo" and expected in m.content
            for m in result.messages
        )
        if not echoed:
            return _fail_args("malformed_args")
        if not (result.content or "").strip():
            return _fail_used("empty_response")
        return _ok()

    return check


def _recent_payload(result: TurnResult) -> str | None:
    for m in result.messages:
        if m.role == "tool" and m.tool_name == "list_recently_watched":
            return m.content
    return None


def _require_recently_watched_titles(
    *needles: str,
) -> Callable[[TurnResult], CheckResult]:
    def check(result: TurnResult) -> CheckResult:
        if result.stopped_reason == StoppedReason.OLLAMA_ERROR:
            return _fail_all("ollama_error")
        if "list_recently_watched" not in result.tools_used():
            return _fail_right("no_tool_when_required")
        payload = _recent_payload(result)
        if not payload or payload.startswith("error:"):
            return _fail_args("malformed_args")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return _fail_args("malformed_args")
        names = [m.get("name", "") for m in data.get("movies") or []]
        content = result.content or ""
        if not content.strip():
            return _fail_used("empty_response")
        grounded = any(n and n in content for n in names) or any(
            needle in content for needle in needles
        )
        if not grounded:
            return _fail_used("tool_not_used_in_answer")
        return _ok()

    return check


CASES: list[Case] = [
    Case(
        "time_1",
        "must_call_time",
        "What time is it on the server right now?",
        _require_time_grounded(),
    ),
    Case(
        "time_2",
        "must_call_time",
        "Please check the server clock and tell me the current UTC time.",
        _require_time_grounded(),
    ),
    Case(
        "echo_1",
        "must_call_echo",
        "Echo exactly: ping-42",
        _require_echo("ping-42"),
    ),
    Case(
        "echo_2",
        "must_call_echo",
        "Use the echo tool to repeat this string verbatim: alpha-omega-7",
        _require_echo("alpha-omega-7"),
    ),
    Case(
        "no_tool_1",
        "must_not_call",
        "What is the capital of France?",
        _no_tools(),
    ),
    Case(
        "no_tool_2",
        "must_not_call",
        "In one sentence, what is a haiku?",
        _no_tools(),
    ),
    Case(
        "wrong_tool_1",
        "wrong_tool_avoided",
        "What time is it?",
        _time_not_echo(),
    ),
    Case(
        "wrong_tool_2",
        "wrong_tool_avoided",
        "Tell me the current server time only — do not echo anything.",
        _time_not_echo(),
    ),
    Case(
        "multi_1",
        "multi_step",
        "Get the current server time, then echo that timestamp back to me.",
        _multi_step_echo_time(),
    ),
    Case(
        "multi_2",
        "multi_step",
        "Echo the current server time.",
        _multi_step_echo_time(),
    ),
    Case(
        "vague_1",
        "malformed_pressure",
        "do the thing",
        _no_crash_vague(),
    ),
    Case(
        "vague_2",
        "malformed_pressure",
        "handle it",
        _no_crash_vague(),
    ),
    Case(
        "weather_1",
        "must_call_weather",
        "What's the weather today?",
        _require_weather_grounded(),
    ),
    Case(
        "weather_2",
        "must_call_weather",
        "Do I need an umbrella?",
        _require_weather_umbrella(),
    ),
    Case(
        "weather_3",
        "must_call_weather",
        "What's tomorrow's forecast?",
        _require_weather_tomorrow(),
    ),
    Case(
        "weather_4",
        "weather_offline",
        "What's the weather right now?",
        _require_weather_offline_clear(),
    ),
    Case(
        "weather_5",
        "must_not_call_weather",
        "Echo exactly: no-weather-42",
        _echo_not_weather("no-weather-42"),
    ),
    Case(
        "pref_1",
        "must_call_set_preference",
        "Please remember that my favorite genres are sci-fi and drama.",
        _require_set_preference("favorite_genres", "sci-fi"),
    ),
    Case(
        "pref_2",
        "must_call_set_preference",
        "Set my tone preference to dry understatement.",
        _require_set_preference("tone", "dry"),
    ),
    Case(
        "pref_3",
        "must_not_call_preference",
        "Echo exactly: no-pref-99",
        _echo_not_preference("no-pref-99"),
    ),
    Case(
        "jellyfin_1",
        "must_call_recommend",
        "Recommend something like Blade Runner that I haven't seen from my library.",
        _require_recommend_titles("Blade Runner 2049", "Ghost in the Shell"),
    ),
    Case(
        "jellyfin_2",
        "must_call_recommend",
        "Suggest an unwatched horror movie from my Jellyfin library.",
        _require_recommend_titles("The Haunting"),
    ),
    Case(
        "jellyfin_3",
        "must_call_recommend",
        "I'm in a scary mood — pick a movie from my library I haven't finished watching.",
        _require_recommend_titles("The Haunting", "Blade"),
    ),
    Case(
        "jellyfin_4",
        "recommend_empty",
        "Recommend a movie from my Jellyfin library.",
        _require_recommend_empty_clear(),
    ),
    Case(
        "jellyfin_5",
        "must_not_call_recommend",
        "Echo exactly: no-movies-77",
        _echo_not_recommend("no-movies-77"),
    ),
    Case(
        "jellyfin_6",
        "must_call_recently_watched",
        "What did I watch lately from my Jellyfin library?",
        _require_recently_watched_titles("Blade Runner", "Blade Runner 2049"),
    ),
    Case(
        "calendar_1",
        "must_call_calendar",
        "What's on my calendar today?",
        _require_calendar_grounded(),
    ),
    Case(
        "calendar_2",
        "calendar_offline",
        "What's on my calendar right now?",
        _require_calendar_offline_clear(),
    ),
    Case(
        "calendar_3",
        "must_not_call_calendar",
        "Echo exactly: no-calendar-55",
        _echo_not_calendar("no-calendar-55"),
    ),
    Case(
        "morning_1",
        "must_call_weather_and_calendar",
        "Good morning",
        _require_morning_brief(),
    ),
    Case(
        "morning_2",
        "must_call_weather_and_calendar",
        "Morning — what's the day look like?",
        _require_morning_brief(),
    ),
    Case(
        "morning_3",
        "must_call_weather_and_calendar",
        "Goodmorning",
        _require_morning_brief(),
    ),
    Case(
        "morning_4",
        "must_call_weather_and_calendar",
        "Goedemorgen",
        _require_morning_brief_dutch(),
    ),
    Case(
        "morning_5",
        "must_call_weather_and_calendar",
        "Mornin",
        _require_morning_brief(),
    ),
    Case(
        "morning_6",
        "must_call_weather_and_calendar",
        "Good morning",
        _require_morning_brief_empty_day(),
    ),
    Case(
        "evening_1",
        "must_call_weather_and_calendar",
        "Good night",
        _require_evening_wind_down(),
    ),
    Case(
        "evening_2",
        "must_call_weather_and_calendar",
        "Welterusten",
        _require_evening_wind_down_dutch(),
    ),
    Case(
        "followup_weather_1",
        "followup_turn",
        "tomorrow?",
        _require_followup_weather_tomorrow(),
        prior_turns=(
            (
                "What's the weather today?",
                "Overcast today, about 18 degrees — steady, if uninspiring.",
            ),
        ),
    ),
    Case(
        "followup_budget_1",
        "followup_turn",
        "en wim?",
        _require_followup_budget_wim(),
        prior_turns=(
            (
                "wat heeft ilse deze maand uitgegeven aan boodschappen",
                "Ilse heeft deze maand €157,55 uitgegeven aan boodschappen.",
            ),
        ),
    ),
    Case(
        "modes_eli5_1",
        "modes",
        "Explain like I'm five: why is the sky blue?",
        _require_eli5_shape(),
    ),
    Case(
        "modes_twosided_1",
        "modes",
        "Give me both sides of working from home versus the office.",
        _require_two_sided_shape(),
    ),
    Case(
        "modes_explain_1",
        "modes",
        "Why did you do that?",
        _require_explain_cites_tool("shopping_list.list"),
        prior_turns=(
            (
                "what's on the shopping list?",
                "Milk and eggs are on the list.",
            ),
        ),
        conversation_id="suite-modes-explain-1",
        plant_trace={
            "ts": "2026-09-16T10:00:00+00:00",
            "turn_id": "suite-explain-trace",
            "prompt_id": "sha256:suite",
            "stopped_reason": "final",
            "success": True,
            "tools_used": ["shopping_list.list"],
            "tool_calls": [{"name": "shopping_list.list", "args": {}}],
            "steps": [],
        },
    ),
    Case(
        "lookup_currency_1",
        "must_call_currency",
        "How many euros is 50 USD?",
        _require_currency_grounded(),
    ),
    Case(
        "lookup_currency_2",
        "currency_offline",
        "How many euros is 50 USD?",
        _require_currency_offline_clear(),
    ),
    Case(
        "lookup_wiki_1",
        "must_call_wikipedia",
        "Wat is een fiets volgens Wikipedia?",
        _require_wikipedia_grounded(),
    ),
    Case(
        "lookup_wiki_2",
        "wikipedia_offline",
        "Wikipedia: fiets",
        _require_wikipedia_offline_clear(),
    ),
    Case(
        "lookup_fact_1",
        "must_call_random_fact",
        "Tell me a random fact",
        _require_random_fact_local(),
    ),
    Case(
        "lookup_fact_2",
        "must_call_random_fact",
        "Vertel een weetje",
        _require_random_fact_local(),
    ),
]


def _system_messages(settings: Settings, registry: dict[str, Tool] | None = None) -> list[ChatMessage]:
    if not PROMPT_PATH.is_file():
        raise FileNotFoundError(
            f"system prompt required for suite: {PROMPT_PATH} "
            "(run from repo root)"
        )
    text = PROMPT_PATH.read_text(encoding="utf-8")
    text += (
        "\n\n## Available tools (this session)\n"
        "- `get_server_time`: use when asked for the time / server clock.\n"
        "- `echo`: use when asked to echo or repeat a specific string.\n"
        "- `get_weather`: use for weather, rain, umbrella, temperature, or forecast "
        "at the configured home location.\n"
        "- `get_calendar`: use for calendar / schedule / what's on today "
        "(no arguments — full calendar day).\n"
        "- `set_preference` / `get_preference`: use for lasting likes (favorite_genres, "
        "tone). Do not invent preferences.\n"
        "- `recommend_movies`: use for movie recommendations from the Jellyfin "
        "Catalogue (seed_title for 'like X', genre, mood). Never invent titles; "
        "ground picks in the tool output.\n"
        "- `list_recently_watched`: use when asked what they watched lately / "
        "last week; ground the answer in the tool list only.\n"
        "- `convert_currency`: use for FX / currency conversion; cite rate_date; "
        "never write BudgetTracker for FX alone.\n"
        "- `wikipedia_lookup`: use for Wikipedia questions; cite title and url; "
        "not general web search.\n"
        "- `random_fact`: use for a random fact / weetje (local list, offline).\n"
        "Call a tool when it clearly applies; otherwise answer directly.\n"
        "On morning greetings (good morning / morning / goedemorgen), call get_weather and "
        "get_calendar in the same step, then brief weather + today's schedule only. "
        "Reply in the user's language — Dutch prompts get a fully Dutch reply.\n"
        "When both time and echo are needed, call get_server_time first, "
        "then echo the returned timestamp — never invent a time.\n"
        "Never invent weather; if get_weather fails, say so briefly.\n"
        "Never invent appointments; if get_calendar fails, say so briefly.\n"
        "Never invent movie titles; if recommend_movies or "
        "list_recently_watched fails or the catalogue is empty, say so briefly.\n"
    )
    if registry:
        for name in sorted(registry):
            if name.startswith("budgettracker."):
                text += f"- `{name}`: {registry[name].description}\n"
    content = build_system_prompt(
        text,
        {},
        timezone=settings.location.timezone,
    )
    return [ChatMessage(role="system", content=content)]


def build_case_messages(case: Case, settings: Settings, registry: dict[str, Tool]) -> list[ChatMessage]:
    messages = _system_messages(settings, registry)
    for user_text, assistant_text in case.prior_turns:
        messages.append(ChatMessage(role="user", content=user_text))
        messages.append(ChatMessage(role="assistant", content=assistant_text))
    messages.append(ChatMessage(role="user", content=case.prompt))
    return messages


def run_case(
    client: OllamaClient,
    case: Case,
    *,
    think: bool,
    tools: dict,
    tool_timeout_s: float,
    settings: Settings,
    data_dir: Path | None = None,
) -> CaseResult:
    messages = build_case_messages(case, settings, tools)
    case_data_dir = data_dir
    if case.plant_trace is not None:
        if case_data_dir is None:
            case_data_dir = Path(tempfile.mkdtemp(prefix="mimir-suite-modes-"))
        path = turns_log_path(case_data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = dict(case.plant_trace)
        if case.conversation_id:
            record.setdefault("conversation_id", case.conversation_id)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    t0 = time.perf_counter()
    result = run_turn(
        client,
        messages,
        tools=tools,
        max_iterations=3,
        think=think,
        default_tool_timeout_s=tool_timeout_s,
        data_dir=case_data_dir,
        conversation_id=case.conversation_id,
    )
    wall = (time.perf_counter() - t0) * 1000
    scored = case.check(result)
    preview = (result.content or "").replace("\n", " ")[:80]
    return CaseResult(
        id=case.id,
        category=case.category,
        passed=scored.passed,
        reason=scored.reason,
        latency_ms=wall,
        right_tool=scored.right_tool,
        valid_args=scored.valid_args,
        result_used=scored.result_used,
        tool_sequence=result.tools_used(),
        content_preview=preview,
    )


def metric_rates(results: list[CaseResult]) -> dict[str, float]:
    """Pure helper for tests — fraction of cases passing each dimension."""
    n = len(results) or 1
    return {
        "right_tool": sum(1 for r in results if r.right_tool) / n,
        "valid_args": sum(1 for r in results if r.valid_args) / n,
        "result_used": sum(1 for r in results if r.result_used) / n,
    }


def _suite_calendar_ok() -> str:
    """Deterministic calendar payload so the suite does not need a live ICS URL."""
    events = [
        {
            "summary": "Standup",
            "start": "2026-08-27T12:00:00+02:00",
            "end": "2026-08-27T13:00:00+02:00",
            "all_day": False,
            "location": "Office",
        },
        {
            "summary": "Away day",
            "start": "2026-08-27",
            "end": "2026-08-28",
            "all_day": True,
        },
    ]
    schedule_lines = [
        "Standup, at 12:00",
        "Away day, all day",
    ]
    return json.dumps(
        {
            "timezone": "Europe/Amsterdam",
            "window": {
                "start": "2026-08-27T08:00:00+02:00",
                "end": "2026-08-28T00:00:00+02:00",
            },
            "events": events,
            "event_count": len(events),
            "schedule_lines": schedule_lines,
            "fetched_at": "2026-08-27T06:00:00+00:00",
            "stale": False,
            "lag_note": "feed may lag publisher (e.g. Proton share up to ~8h)",
        },
        separators=(",", ":"),
    )


def _suite_calendar_empty() -> str:
    """Empty calendar day for morning brief clear-schedule checks."""
    return json.dumps(
        {
            "timezone": "Europe/Amsterdam",
            "window": {
                "start": "2026-09-01T00:00:00+02:00",
                "end": "2026-09-02T00:00:00+02:00",
            },
            "events": [],
            "event_count": 0,
            "schedule_lines": [],
            "fetched_at": "2026-09-01T06:00:00+00:00",
            "stale": False,
            "lag_note": "feed may lag publisher (e.g. Proton share up to ~8h)",
        },
        separators=(",", ":"),
    )


def main() -> int:
    try:
        settings = load_config()
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 1

    data_dir = Path(tempfile.mkdtemp(prefix="mimir-suite-"))
    db = Database(data_dir / "mimir.db")
    db.seed_catalogue_for_tests(_fixture_catalogue_movies())
    empty_dir = Path(tempfile.mkdtemp(prefix="mimir-suite-empty-"))
    empty_db = Database(empty_dir / "mimir.db")
    registry = build_registry(
        settings,
        db=db,
        calendar_fetch_override=_suite_calendar_ok,
        weather_fetch_override=_suite_weather_payload,
        currency_fetch_override=_suite_currency_payload,
        wikipedia_fetch_override=_suite_wikipedia_payload,
    )
    budget_reg = _suite_budget_registry(settings, db)
    offline_reg = build_registry(
        settings,
        db=db,
        weather_fetch_override=lambda: "error: weather unavailable (offline)",
        calendar_fetch_override=_suite_calendar_ok,
        currency_fetch_override=_suite_currency_payload,
        wikipedia_fetch_override=_suite_wikipedia_payload,
    )
    currency_offline_reg = build_registry(
        settings,
        db=db,
        calendar_fetch_override=_suite_calendar_ok,
        weather_fetch_override=_suite_weather_payload,
        currency_fetch_override=lambda: "error: currency unavailable (offline)",
        wikipedia_fetch_override=_suite_wikipedia_payload,
    )
    wikipedia_offline_reg = build_registry(
        settings,
        db=db,
        calendar_fetch_override=_suite_calendar_ok,
        weather_fetch_override=_suite_weather_payload,
        currency_fetch_override=_suite_currency_payload,
        wikipedia_fetch_override=lambda: "error: wikipedia unavailable (offline)",
    )
    calendar_offline_reg = build_registry(
        settings,
        db=db,
        calendar_fetch_override=lambda: "error: calendar unavailable (offline)",
        currency_fetch_override=_suite_currency_payload,
        wikipedia_fetch_override=_suite_wikipedia_payload,
    )
    empty_reg = build_registry(
        settings,
        db=empty_db,
        calendar_fetch_override=_suite_calendar_ok,
        currency_fetch_override=_suite_currency_payload,
        wikipedia_fetch_override=_suite_wikipedia_payload,
    )
    morning_empty_reg = build_registry(
        settings,
        db=db,
        calendar_fetch_override=_suite_calendar_empty,
        weather_fetch_override=_suite_weather_payload,
        currency_fetch_override=_suite_currency_payload,
        wikipedia_fetch_override=_suite_wikipedia_payload,
    )

    print(
        f"model={settings.ollama.model} url={settings.ollama.url} "
        f"num_ctx={settings.ollama.num_ctx} think={settings.ollama.think}"
    )
    print(f"tools={[t['function']['name'] for t in tool_schemas(registry)]}")
    print(f"cases={len(CASES)} threshold={PASS_THRESHOLD:.0%}\n")

    results: list[CaseResult] = []
    with OllamaClient(
        settings.ollama.url,
        settings.ollama.model,
        num_ctx=settings.ollama.num_ctx,
        timeout_s=settings.timeouts.ollama_s,
    ) as client:
        for case in CASES:
            print(f"… {case.id} ({case.category})", flush=True)
            if case.id == "weather_4":
                tools = offline_reg
            elif case.id == "lookup_currency_2":
                tools = currency_offline_reg
            elif case.id == "lookup_wiki_2":
                tools = wikipedia_offline_reg
            elif case.id == "calendar_2":
                tools = calendar_offline_reg
            elif case.id == "jellyfin_4":
                tools = empty_reg
            elif case.id == "morning_6":
                tools = morning_empty_reg
            elif case.id.startswith("followup_budget_"):
                tools = budget_reg
            elif case.id.startswith("followup_weather_"):
                tools = registry
            else:
                tools = registry
            cr = run_case(
                client,
                case,
                think=settings.ollama.think,
                tools=tools,
                tool_timeout_s=settings.timeouts.tool_s,
                settings=settings,
                data_dir=data_dir,
            )
            results.append(cr)
            mark = "PASS" if cr.passed else "FAIL"
            print(
                f"  {mark} reason={cr.reason} "
                f"right_tool={cr.right_tool} valid_args={cr.valid_args} "
                f"result_used={cr.result_used} "
                f"tools={cr.tool_sequence} "
                f"latency_ms={cr.latency_ms:.0f}"
            )
            if cr.content_preview:
                print(f"  preview: {cr.content_preview}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    rate = passed / total if total else 0.0
    rates = metric_rates(results)
    latencies = [r.latency_ms for r in results]
    p50 = statistics.median(latencies) if latencies else 0.0
    if len(latencies) >= 20:
        p95 = statistics.quantiles(latencies, n=20)[18]
    elif len(latencies) >= 2:
        p95 = sorted(latencies)[max(0, int(round(0.95 * (len(latencies) - 1))))]
    else:
        p95 = latencies[0] if latencies else 0.0

    print("\n=== Summary ===")
    print(f"pass_rate={passed}/{total} ({rate:.0%})")
    print(
        f"right_tool={sum(1 for r in results if r.right_tool)}/{total} "
        f"({rates['right_tool']:.0%}) "
        f"valid_args={sum(1 for r in results if r.valid_args)}/{total} "
        f"({rates['valid_args']:.0%}) "
        f"result_used={sum(1 for r in results if r.result_used)}/{total} "
        f"({rates['result_used']:.0%})"
    )
    print(f"latency_ms p50={p50:.0f} p95={p95:.0f}")

    fails = [r for r in results if not r.passed]
    if fails:
        print("failures:")
        for r in fails:
            print(f"  - {r.id}: {r.reason} tools={r.tool_sequence}")

    reasons = Counter(r.reason for r in results)
    print(f"reasons={dict(reasons)}")

    weather_cases = [r for r in results if r.id.startswith("weather_")]
    if weather_cases:
        w_pass = sum(1 for r in weather_cases if r.passed)
        print(f"weather_pinned={w_pass}/{len(weather_cases)}")

    pref_cases = [r for r in results if r.id.startswith("pref_")]
    if pref_cases:
        p_pass = sum(1 for r in pref_cases if r.passed)
        print(f"pref_pinned={p_pass}/{len(pref_cases)}")

    jellyfin_cases = [r for r in results if r.id.startswith("jellyfin_")]
    if jellyfin_cases:
        j_pass = sum(1 for r in jellyfin_cases if r.passed)
        print(f"jellyfin_pinned={j_pass}/{len(jellyfin_cases)}")

    calendar_cases = [r for r in results if r.id.startswith("calendar_")]
    if calendar_cases:
        c_pass = sum(1 for r in calendar_cases if r.passed)
        print(f"calendar_pinned={c_pass}/{len(calendar_cases)}")

    followup_cases = [r for r in results if r.id.startswith("followup_")]
    if followup_cases:
        f_pass = sum(1 for r in followup_cases if r.passed)
        print(f"followup_pinned={f_pass}/{len(followup_cases)}")

    if rate >= PASS_THRESHOLD:
        print(f"\nEXIT OK (>= {PASS_THRESHOLD:.0%})")
        return 0
    print(f"\nEXIT FAIL (need >= {PASS_THRESHOLD:.0%})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
