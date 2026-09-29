"""Delivery package id resolution for homebase.delivery.set_status."""

from __future__ import annotations

import json
import re
from typing import Any

from brain.mcp.errors import tool_result_is_error

_CUID_LIKE = re.compile(r"^c[a-z0-9]{20,}$", re.IGNORECASE)

_STATUS_ALIASES: dict[str, str] = {
    "pending": "PENDING",
    "in_transit": "IN_TRANSIT",
    "in transit": "IN_TRANSIT",
    "onderweg": "IN_TRANSIT",
    "out_for_delivery": "OUT_FOR_DELIVERY",
    "out for delivery": "OUT_FOR_DELIVERY",
    "uit voor bezorging": "OUT_FOR_DELIVERY",
    "delivered": "DELIVERED",
    "bezorgd": "DELIVERED",
    "exception": "EXCEPTION",
    "uitzondering": "EXCEPTION",
    "in afwachting": "PENDING",
}


def looks_like_delivery_id(value: str) -> bool:
    """True when value matches Prisma cuid shape (not a human label)."""
    return bool(_CUID_LIKE.match(value.strip()))


def normalize_delivery_status(raw: str) -> str | None:
    """Map EN/NL status phrases to DeliveryStatus enum, or None if unknown."""
    key = (raw or "").strip().lower().replace("-", " ")
    key = re.sub(r"\s+", " ", key)
    if not key:
        return None
    if key in _STATUS_ALIASES:
        return _STATUS_ALIASES[key]
    underscored = key.replace(" ", "_")
    if underscored in _STATUS_ALIASES:
        return _STATUS_ALIASES[underscored]
    upper = (raw or "").strip().upper().replace(" ", "_")
    if upper in {
        "PENDING",
        "IN_TRANSIT",
        "OUT_FOR_DELIVERY",
        "DELIVERED",
        "EXCEPTION",
    }:
        return upper
    return None


def parse_delivery_list(raw: str) -> list[dict[str, Any]] | None:
    if raw.startswith("error:"):
        return None
    body = raw.strip()
    if "\n" in body and body.split("\n", 1)[0].startswith("Note:"):
        body = body.split("\n", 1)[1].strip()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        return [p for p in parsed if isinstance(p, dict)]
    if isinstance(parsed, dict) and parsed.get("id"):
        return [parsed]
    return None


def _label(pkg: dict[str, Any]) -> str:
    desc = (pkg.get("description") or "").strip()
    if desc:
        return desc
    tracking = (pkg.get("tracking_number") or "").strip()
    if tracking:
        return tracking
    return str(pkg.get("id") or "?")


def resolve_delivery_ids(
    packages: list[dict[str, Any]], raw: str
) -> tuple[list[str], str | None]:
    """Map user/model id, description, or tracking to package id(s).

    Returns ``(ids, None)`` on success, ``([], "ambiguous")`` when several
    partial matches, or ``([], None)`` when nothing matched.

    Unique sole package is accepted when the needle is not a foreign cuid
    (household has one row — match by presence, not label equality).
    """
    needle = raw.strip()
    if not needle:
        return [], None
    by_id = {str(p["id"]): p for p in packages if p.get("id")}
    if needle in by_id:
        return [needle], None
    if looks_like_delivery_id(needle):
        # Stale or foreign cuid — do not fall through to sole-package match.
        return [], None
    lower = needle.lower()
    exact_desc = [
        str(p["id"])
        for p in packages
        if (p.get("description") or "").strip().lower() == lower
    ]
    if exact_desc:
        return exact_desc, None
    exact_track = [
        str(p["id"])
        for p in packages
        if (p.get("tracking_number") or "").strip().lower() == lower
    ]
    if exact_track:
        return exact_track, None
    partial = [
        p
        for p in packages
        if lower in (p.get("description") or "").strip().lower()
        or lower in (p.get("tracking_number") or "").strip().lower()
    ]
    if len(partial) == 1:
        return [str(partial[0]["id"])], None
    if len(partial) > 1:
        return [], "ambiguous"
    if len(packages) == 1 and packages[0].get("id"):
        return [str(packages[0]["id"])], None
    return [], None

def delivery_resolve_error(code: str, message: str) -> str:
    payload = {"error": {"code": code, "message": message, "retryable": False}}
    return f"error: {json.dumps(payload, ensure_ascii=False)}"


def delivery_not_found_error(
    needle: str, *, known: list[str] | None = None
) -> str:
    hint = (
        f"No delivery matching '{needle}'. Pass the package **description** "
        "(or tracking number) as `id`, not a cuid from an earlier turn."
    )
    if known:
        hint += f" Known packages: {', '.join(known)}."
    return delivery_resolve_error("not_found", hint)


def delivery_ambiguous_error(needle: str, labels: list[str]) -> str:
    shown = ", ".join(labels[:8])
    return delivery_resolve_error(
        "ambiguous",
        f"Several deliveries match '{needle}': {shown}. Ask which one.",
    )


def present_delivery_set_status_json(text: str) -> str:
    """Annotate success so the model confirms from tool output."""
    stripped = text.strip()
    if not stripped or stripped.startswith("error:") or stripped[:1] != "{":
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict) or "error" in parsed:
        return text
    enriched = dict(parsed)
    enriched["status_updated"] = True
    label = _label(enriched)
    status = enriched.get("status") or "?"
    note = (
        f"Note: homebase.delivery.set_status succeeded for {label!r} → {status}. "
        "Confirm to the user from this result. status_updated is true."
    )
    body = json.dumps(enriched, ensure_ascii=False)
    return f"{note}\n{body}"


def confirm_reply_from_set_status_result(text: str) -> str | None:
    """Short user-facing confirm when the turn ran out of iterations after success."""
    body = text.strip()
    if body.startswith("Note:") and "\n" in body:
        body = body.split("\n", 1)[1].strip()
    if tool_result_is_error(body) or not body.startswith("{"):
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "error" in parsed:
        return None
    if not parsed.get("status_updated") and not parsed.get("status"):
        return None
    label = _label(parsed)
    status = str(parsed.get("status") or "updated")
    return f"Marked {label} as {status}."
