"""Tests for SSRF-safe web.fetch (T-021)."""

from __future__ import annotations

import httpx

from brain.tools.web_fetch import fetch_url, simplify_body, validate_fetch_url


def test_validate_rejects_non_http_schemes() -> None:
    assert validate_fetch_url("file:///etc/passwd") is not None
    assert validate_fetch_url("ftp://example.com/x") is not None


def test_validate_rejects_private_literal_ips() -> None:
    assert validate_fetch_url("http://127.0.0.1/secret") is not None
    assert validate_fetch_url("http://192.168.1.1/") is not None
    assert validate_fetch_url("http://10.0.0.5/") is not None
    assert validate_fetch_url("http://169.254.169.254/latest/meta-data") is not None


def test_validate_rejects_credentials() -> None:
    assert validate_fetch_url("https://user:pass@example.com/r") is not None


def test_fetch_happy_path_returns_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers.get("host") == "example.com"
        return httpx.Response(
            200,
            text="<html><body><h1>Pannenkoeken</h1><p>Mix flour.</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    transport = httpx.MockTransport(handler)

    def factory() -> httpx.Client:
        return httpx.Client(transport=transport, follow_redirects=False, trust_env=False)

    result = fetch_url(
        "https://example.com/recipe",
        client_factory=factory,
        resolve_host=lambda _host: ["93.184.216.34"],
    )
    assert not result.startswith("error:")
    assert "Pannenkoeken" in result
    assert "Mix flour" in result
    assert "<script" not in result.lower()


def test_fetch_rejects_blocked_resolver_ip() -> None:
    def factory() -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="nope")),
            follow_redirects=False,
            trust_env=False,
        )

    result = fetch_url(
        "https://evil.example/recipe",
        client_factory=factory,
        resolve_host=lambda _host: ["127.0.0.1"],
    )
    assert result.startswith("error:")
    assert "blocked" in result


def test_fetch_rejects_oversized_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 600_000,
            headers={"content-type": "text/plain"},
        )

    transport = httpx.MockTransport(handler)

    def factory() -> httpx.Client:
        return httpx.Client(transport=transport, follow_redirects=False, trust_env=False)

    result = fetch_url(
        "https://example.com/big",
        client_factory=factory,
        resolve_host=lambda _host: ["93.184.216.34"],
    )
    assert result.startswith("error:")
    assert "exceeds" in result


def test_simplify_strips_script() -> None:
    html = b"<html><script>alert(1)</script><p>Hello recipe</p></html>"
    text = simplify_body(html, content_type="text/html")
    assert "Hello recipe" in text
    assert "alert" not in text


def test_simplify_prefers_ingredients_when_truncating() -> None:
    from brain.tools.web_fetch import MAX_MODEL_CHARS

    noise = "cookie wall " * 2000
    body = (
        noise
        + "\nIngredients\n200 g noodles\nDirections\n1. Stir fry.\n2. Serve.\n"
        + ("footer " * 500)
    )
    text = simplify_body(body.encode("utf-8"), content_type="text/plain")
    assert len(text) <= MAX_MODEL_CHARS
    assert "Ingredients" in text
    assert "noodles" in text
    assert "Stir fry" in text
