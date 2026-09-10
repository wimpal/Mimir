"""Tests for SSRF-safe web.fetch (T-021 / T-050)."""

from __future__ import annotations

import json

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
    from brain.tools.web_fetch import MAX_BODY_BYTES

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (MAX_BODY_BYTES + 50_000),
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


_PICNIC_LIKE_LD_JSON = """
{
  "@context": "https://schema.org",
  "@type": "Recipe",
  "name": "Ketjap zalm met broccoli en rijst",
  "recipeIngredient": [
    "200 g rijst",
    "2 zalmfilets",
    "1 broccoli",
    "2 el sojasaus",
    "1 el ketjap manis",
    "2 bosui",
    "2 teentjes knoflook"
  ],
  "recipeInstructions": [
    {
      "@type": "HowToStep",
      "text": "Breng een pan met water aan de kook en kook de rijst volgens de verpakking."
    },
    {
      "@type": "HowToStep",
      "text": "Snijd de broccoli in roosjes en kook ze gaar."
    },
    {
      "@type": "HowToStep",
      "text": "Bak de zalm en voeg sojasaus, ketjap manis, bosui en knoflook toe."
    },
    {
      "@type": "HowToStep",
      "text": "Serveer de zalm met broccoli en rijst."
    }
  ]
}
"""


def test_simplify_keeps_jsonld_howto_steps_in_order() -> None:
    html = f"""
    <html><body>
      <nav>Picnic recepten Home Winkelwagen</nav>
      <h1>Ketjap zalm met broccoli en rijst</h1>
      <p>Ingrediënten lijst chrome zonder bereiding</p>
      <script>alert('track')</script>
      <script type="application/ld+json">{_PICNIC_LIKE_LD_JSON}</script>
      <footer>Meer recepten</footer>
    </body></html>
    """
    text = simplify_body(html.encode("utf-8"), content_type="text/html")
    assert "Breng een pan" in text
    assert "rijst" in text.lower()
    assert "sojasaus" in text
    assert "ketjap manis" in text
    assert "bosui" in text
    assert "knoflook" in text
    rice_idx = text.index("Breng een pan")
    broccoli_idx = text.index("Snijd de broccoli")
    zalm_idx = text.index("Bak de zalm")
    assert rice_idx < broccoli_idx < zalm_idx
    assert "alert" not in text
    assert "@type" not in text
    assert "recipeInstructions" not in text


def test_simplify_jsonld_graph_and_howto_section() -> None:
    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebPage", "name": "noise"},
            {
                "@type": "Recipe",
                "name": "Test",
                "recipeIngredient": ["1 ui"],
                "recipeInstructions": {
                    "@type": "HowToSection",
                    "name": "Bereiding",
                    "itemListElement": [
                        {"@type": "HowToStep", "text": "Eerst de ui snijden."},
                        {
                            "@type": "HowToStep",
                            "name": "Dan bakken tot goudbruin.",
                        },
                    ],
                },
            },
        ],
    }
    html = (
        "<html><body><p>Chrome only</p>"
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    text = simplify_body(html.encode("utf-8"), content_type="text/html")
    assert "1 ui" in text
    first = text.index("Eerst de ui snijden")
    second = text.index("Dan bakken tot goudbruin")
    assert first < second


def test_simplify_jsonld_truncation_keeps_directions() -> None:
    from brain.tools.web_fetch import MAX_MODEL_CHARS

    steps = [
        {"@type": "HowToStep", "text": f"Step {i}: cook the rice carefully."}
        for i in range(1, 8)
    ]
    payload = {
        "@type": "Recipe",
        "recipeIngredient": [f"{i} g item" for i in range(20)],
        "recipeInstructions": steps,
    }
    chrome = "nav chrome " * 3000
    html = (
        f"<html><body><p>{chrome}</p>"
        f'<script type="Application/ld+json ; charset=utf-8">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    text = simplify_body(html.encode("utf-8"), content_type="text/html")
    assert len(text) <= MAX_MODEL_CHARS
    assert "Directions" in text
    assert "Step 1: cook the rice carefully" in text
    assert "Step 7: cook the rice carefully" in text


def test_simplify_jsonld_near_limit_chrome_does_not_overflow() -> None:
    from brain.tools.web_fetch import MAX_MODEL_CHARS

    # Structured just under the limit so chrome room is tiny / negative-prone.
    filler = "x" * (MAX_MODEL_CHARS - 80)
    payload = {
        "@type": "Recipe",
        "recipeIngredient": ["1 ui"],
        "recipeInstructions": [{"@type": "HowToStep", "text": "Kook de rijst." + filler}],
    }
    html = (
        "<html><body>"
        + ("nav " * 5000)
        + f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    text = simplify_body(html.encode("utf-8"), content_type="text/html")
    assert len(text) <= MAX_MODEL_CHARS
    assert "Kook de rijst" in text


def test_simplify_jsonld_howto_step_property() -> None:
    payload = {
        "@type": "Recipe",
        "recipeIngredient": ["zout"],
        "recipeInstructions": {
            "@type": "HowTo",
            "step": [
                {"@type": "HowToStep", "text": "Eerst zouten."},
                {"@type": "HowToStep", "text": "Dan bakken."},
            ],
        },
    }
    html = (
        "<html><body>"
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    text = simplify_body(html.encode("utf-8"), content_type="text/html")
    assert text.index("Eerst zouten") < text.index("Dan bakken")


def test_simplify_nextjs_escaped_recipe_json() -> None:
    """Picnic embeds Recipe JSON inside Next.js flight script strings."""
    recipe = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": "Ketjap-zalm",
        "recipeIngredient": [
            "300 g rijst",
            "2 el sojasaus",
            "4 el ketjap manis",
            "1 stuk bosui",
            "2 teentjes knoflook",
        ],
        "recipeInstructions": [
            {
                "@type": "HowToStep",
                "text": "Breng een pan met water aan de kook. Kook 300 g rijst.",
            },
            {
                "@type": "HowToStep",
                "text": "Bak de zalm in de ketjap-saus.",
            },
        ],
    }
    embedded = json.dumps(json.dumps(recipe, separators=(",", ":")))
    # json.dumps of a string yields a quoted JSON string literal.
    html = (
        "<html><body><nav>Picnic chrome</nav>"
        f"<script>self.__next_f.push([1,{embedded}])</script>"
        "</body></html>"
    )
    text = simplify_body(html.encode("utf-8"), content_type="text/html")
    assert "Breng een pan" in text
    assert text.index("Breng een pan") < text.index("Bak de zalm")
    assert "sojasaus" in text
    assert "ketjap manis" in text
    assert "bosui" in text
    assert "knoflook" in text
