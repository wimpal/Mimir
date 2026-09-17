"""convert_currency — Frankfurter/ECB rates (T-056)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import urljoin

import httpx

from brain.config import Settings
from brain.tools import Tool

DEFAULT_RATES_BASE_URL = "https://api.frankfurter.dev/v1"
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")
_TWOPLACES = Decimal("0.01")
_RATE_PLACES = Decimal("0.0001")


def _normalize_code(raw: str) -> str | None:
    text = (raw or "").strip().upper()
    if not _CURRENCY_RE.match(text):
        return None
    return text


def _quantize_amount(value: Decimal) -> str:
    return format(value.quantize(_TWOPLACES, rounding=ROUND_HALF_UP), "f")


def _quantize_rate(value: Decimal) -> str:
    return format(value.quantize(_RATE_PLACES, rounding=ROUND_HALF_UP), "f")


def normalize_frankfurter_payload(
    raw: dict[str, Any],
    *,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
) -> dict[str, Any]:
    """Map Frankfurter latest JSON into the tool wire shape."""
    rates = raw.get("rates")
    if not isinstance(rates, dict) or to_currency not in rates:
        raise ValueError("missing target rate")
    converted_raw = rates[to_currency]
    converted = Decimal(str(converted_raw))
    if amount == 0:
        rate = Decimal("0")
    else:
        rate = converted / amount
    rate_date = str(raw.get("date") or "").strip()
    if not rate_date:
        raise ValueError("missing rate date")
    return {
        "amount": _quantize_amount(amount),
        "from_currency": from_currency,
        "to_currency": to_currency,
        "converted": _quantize_amount(converted),
        "rate": _quantize_rate(rate),
        "rate_date": rate_date,
        "source": "frankfurter/ecb",
    }


def _execute_convert_currency(
    *,
    amount: float | int | str,
    from_currency: str,
    to_currency: str,
    base_url: str,
    timeout_s: float,
    http_client: httpx.Client | None = None,
) -> str:
    code_from = _normalize_code(from_currency)
    code_to = _normalize_code(to_currency)
    if code_from is None or code_to is None:
        return "error: currency unavailable (invalid currency code)"
    try:
        amount_dec = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return "error: currency unavailable (invalid amount)"
    if not amount_dec.is_finite():
        return "error: currency unavailable (invalid amount)"
    if amount_dec <= 0:
        return "error: currency unavailable (amount must be positive)"
    if amount_dec.as_tuple().exponent < -8 or abs(amount_dec) > Decimal("1e12"):
        return "error: currency unavailable (amount out of range)"
    if code_from == code_to:
        return json.dumps(
            {
                "amount": _quantize_amount(amount_dec),
                "from_currency": code_from,
                "to_currency": code_to,
                "converted": _quantize_amount(amount_dec),
                "rate": _quantize_rate(Decimal("1")),
                "rate_date": "same-currency",
                "source": "local",
            },
            separators=(",", ":"),
        )

    root = (base_url or DEFAULT_RATES_BASE_URL).rstrip("/") + "/"
    url = urljoin(root, "latest")
    params = {
        "amount": format(amount_dec, "f"),
        "from": code_from,
        "to": code_to,
    }
    owns_client = http_client is None
    # follow_redirects: frankfurter.app → frankfurter.dev/v1 (301).
    client = http_client or httpx.Client(
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=True,
    )
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict):
            return "error: currency unavailable (bad response)"
        payload = normalize_frankfurter_payload(
            raw,
            amount=amount_dec,
            from_currency=code_from,
            to_currency=code_to,
        )
        return json.dumps(payload, separators=(",", ":"))
    except httpx.TimeoutException:
        return f"error: currency unavailable (timed out after {timeout_s}s)"
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = (exc.response.text or "")[:200].lower()
        if status == 404 or "not found" in body or "currency" in body:
            return "error: currency unavailable (unsupported currency)"
        return f"error: currency unavailable (HTTP {status})"
    except httpx.HTTPError as exc:
        return f"error: currency unavailable ({exc.__class__.__name__})"
    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
        return f"error: currency unavailable ({exc})"
    finally:
        if owns_client:
            client.close()


def make_convert_currency_tool(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
) -> Tool:
    timeout_s = settings.timeouts.tool_s
    base_url = settings.currency.rates_base_url

    def execute(
        *,
        amount: float | int | str,
        from_currency: str,
        to_currency: str,
    ) -> str:
        if fetch_override is not None:
            return fetch_override()
        return _execute_convert_currency(
            amount=amount,
            from_currency=from_currency,
            to_currency=to_currency,
            base_url=base_url,
            timeout_s=timeout_s,
            http_client=http_client,
        )

    return Tool(
        name="convert_currency",
        description=(
            "Convert an amount between currencies using ECB rates via Frankfurter. "
            "Use for FX questions (e.g. how many euros is 50 USD). "
            "Returns converted amount, rate, and rate_date — cite the rate date. "
            "Never write BudgetTracker transactions. "
            "Args: amount (positive number), from_currency, to_currency (ISO 4217)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Amount to convert (positive).",
                },
                "from_currency": {
                    "type": "string",
                    "description": "Source ISO 4217 code (e.g. USD).",
                },
                "to_currency": {
                    "type": "string",
                    "description": "Target ISO 4217 code (e.g. EUR).",
                },
            },
            "required": ["amount", "from_currency", "to_currency"],
            "additionalProperties": False,
        },
        execute=execute,
    )


def currency_tools(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
) -> dict[str, Tool]:
    tool = make_convert_currency_tool(
        settings,
        http_client=http_client,
        fetch_override=fetch_override,
    )
    return {tool.name: tool}
