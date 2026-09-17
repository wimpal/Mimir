"""Unit tests for wikipedia_lookup (MediaWiki)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from brain.config import Settings
from brain.tools import TOOLS, build_registry, dispatch
from brain.tools.wikipedia import normalize_wikipedia_payload


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


SAMPLE_WIKI = {
    "query": {
        "pages": {
            "123": {
                "pageid": 123,
                "title": "Fiets",
                "extract": "Een fiets is een voertuig met twee wielen.",
                "fullurl": "https://nl.wikipedia.org/wiki/Fiets",
            }
        }
    }
}


def test_normalize_wikipedia_payload() -> None:
    out = normalize_wikipedia_payload(SAMPLE_WIKI, language="nl", query="fiets")
    assert isinstance(out, dict)
    assert out["title"] == "Fiets"
    assert "voertuig" in out["extract"]
    assert out["url"] == "https://nl.wikipedia.org/wiki/Fiets"
    assert out["language"] == "nl"


def test_normalize_wikipedia_api_error() -> None:
    raw = {"error": {"code": "ratelimited", "info": "slow down"}}
    out = normalize_wikipedia_payload(raw, language="en", query="Earth")
    assert isinstance(out, str)
    assert out.startswith("error: wikipedia unavailable")


def test_normalize_wikipedia_missing() -> None:
    raw = {"query": {"pages": {"-1": {"missing": "", "title": "Nope"}}}}
    out = normalize_wikipedia_payload(raw, language="en", query="Nope")
    assert isinstance(out, str)
    assert out.startswith("error: wikipedia not found")


def test_wikipedia_lookup_with_mock_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "nl.wikipedia.org" in str(request.url)
        ua = request.headers.get("User-Agent", "")
        assert ua.startswith("MimirBot/")
        assert "http" in ua.lower()  # Wikimedia requires contact URL
        return httpx.Response(200, json=SAMPLE_WIKI)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.wikipedia import wikipedia_tools

        reg = {**TOOLS, **wikipedia_tools(settings, http_client=client)}
        out = dispatch(
            "wikipedia_lookup",
            {"query": "Fiets", "language": "nl"},
            tools=reg,
        )

    data = json.loads(out)
    assert data["title"] == "Fiets"
    assert "wikipedia.org" in data["url"]


def test_wikipedia_user_agent_includes_contact() -> None:
    from brain.tools.wikipedia import USER_AGENT

    assert "MimirBot/" in USER_AGENT
    assert "http" in USER_AGENT.lower()
    assert "@" in USER_AGENT


def test_wikipedia_offline_override(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reg = build_registry(
        settings,
        wikipedia_fetch_override=lambda: "error: wikipedia unavailable (offline)",
    )
    out = dispatch("wikipedia_lookup", {"query": "Fiets"}, tools=reg)
    assert out.startswith("error: wikipedia unavailable")


def test_wikipedia_timeout(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0) as client:
        from brain.tools.wikipedia import wikipedia_tools

        reg = wikipedia_tools(settings, http_client=client)
        out = dispatch("wikipedia_lookup", {"query": "Fiets"}, tools=reg)
    assert "timed out" in out
