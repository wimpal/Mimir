"""Unit tests for convert_currency (Frankfurter / ECB)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx

from brain.config import Settings
from brain.tools import TOOLS, build_registry, dispatch
from brain.tools.currency import normalize_frankfurter_payload


def _settings(tmp_path: Path, **timeout_kw: float) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    timeouts = {"ollama_s": 30, "tool_s": 5, "turn_s": 60, **timeout_kw}
    return Settings(
        location={
            "latitude": 52.09,
            "longitude": 5.12,
            "timezone": "Europe/Amsterdam",
        },
        ollama={"url": "http://test", "model": "qwen3:8b"},
        runtime={"data_dir": data_dir},
        timeouts=timeouts,
    )


SAMPLE_FRANKFURTER = {
    "amount": 50.0,
    "base": "USD",
    "date": "2026-09-15",
    "rates": {"EUR": 45.12},
}


def test_normalize_frankfurter_payload() -> None:
    out = normalize_frankfurter_payload(
        SAMPLE_FRANKFURTER,
        amount=Decimal("50"),
        from_currency="USD",
        to_currency="EUR",
    )
    assert out["converted"] == "45.12"
    assert out["rate_date"] == "2026-09-15"
    assert out["from_currency"] == "USD"
    assert out["to_currency"] == "EUR"
    assert out["source"] == "frankfurter/ecb"
    assert out["rate"] == "0.9024"


def test_convert_currency_with_mock_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "latest" in str(request.url)
        assert "from=USD" in str(request.url)
        assert "to=EUR" in str(request.url)
        return httpx.Response(200, json=SAMPLE_FRANKFURTER)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0, follow_redirects=True) as client:
        from brain.tools.currency import currency_tools

        reg = {**TOOLS, **currency_tools(settings, http_client=client)}
        out = dispatch(
            "convert_currency",
            {"amount": 50, "from_currency": "USD", "to_currency": "EUR"},
            tools=reg,
        )

    assert not out.startswith("error:")
    data = json.loads(out)
    assert data["converted"] == "45.12"
    assert data["rate_date"] == "2026-09-15"


def test_convert_currency_follows_legacy_host_redirect(tmp_path: Path) -> None:
    """api.frankfurter.app → api.frankfurter.dev/v1 (operator smoke: HTTP 301)."""
    settings = _settings(tmp_path)
    settings.currency.rates_base_url = "https://api.frankfurter.app"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.frankfurter.app" in url and "/latest" in url:
            loc = url.replace(
                "https://api.frankfurter.app/latest",
                "https://api.frankfurter.dev/v1/latest",
            )
            return httpx.Response(301, headers={"Location": loc})
        assert "api.frankfurter.dev" in url
        return httpx.Response(
            200,
            json={
                "amount": 2000.0,
                "base": "JPY",
                "date": "2026-09-15",
                "rates": {"EUR": 11.1819},
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, timeout=5.0, follow_redirects=True) as client:
        from brain.tools.currency import currency_tools

        reg = currency_tools(settings, http_client=client)
        out = dispatch(
            "convert_currency",
            {"amount": 2000, "from_currency": "JPY", "to_currency": "EUR"},
            tools=reg,
        )

    data = json.loads(out)
    assert data["converted"] == "11.18"
    assert data["from_currency"] == "JPY"


def test_convert_currency_offline(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    reg = build_registry(
        settings,
        currency_fetch_override=lambda: "error: currency unavailable (offline)",
    )
    out = dispatch(
        "convert_currency",
        {"amount": 10, "from_currency": "USD", "to_currency": "EUR"},
        tools=reg,
    )
    assert out.startswith("error: currency unavailable")


def test_convert_currency_invalid_code(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from brain.tools.currency import currency_tools

    reg = currency_tools(settings)
    out = dispatch(
        "convert_currency",
        {"amount": 10, "from_currency": "US", "to_currency": "EUR"},
        tools=reg,
    )
    assert "invalid currency code" in out


def test_convert_currency_same_currency(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from brain.tools.currency import currency_tools

    reg = currency_tools(settings)
    out = dispatch(
        "convert_currency",
        {"amount": 12.5, "from_currency": "eur", "to_currency": "EUR"},
        tools=reg,
    )
    data = json.loads(out)
    assert data["converted"] == "12.50"
    assert data["source"] == "local"


def test_convert_currency_nan_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    from brain.tools.currency import currency_tools

    reg = currency_tools(settings)
    out = dispatch(
        "convert_currency",
        {"amount": float("nan"), "from_currency": "USD", "to_currency": "EUR"},
        tools=reg,
    )
    assert "invalid amount" in out


def test_build_registry_includes_currency(tmp_path: Path) -> None:
    reg = build_registry(_settings(tmp_path))
    assert "convert_currency" in reg
    assert "wikipedia_lookup" in reg
    assert "random_fact" in reg
