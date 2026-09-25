"""Network device id resolution for homebase.devices.wake (T-101)."""

from __future__ import annotations

import json
import re
from typing import Any

_CUID_LIKE = re.compile(r"^c[a-z0-9]{20,}$", re.IGNORECASE)


def looks_like_device_id(value: str) -> bool:
    return bool(_CUID_LIKE.match(value.strip()))


def parse_devices_list(raw: str) -> list[dict[str, Any]] | None:
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
        return parsed
    if isinstance(parsed, dict) and parsed.get("id") and parsed.get("name"):
        return [parsed]
    return None


def resolve_wake_device_id(
    devices: list[dict[str, Any]], raw: str
) -> str | None:
    """Map id or name to one device id. Prefer wake_capable on ambiguous names."""
    return resolve_device_id_preferring(devices, raw, "wake_capable")


def resolve_tv_device_id(
    devices: list[dict[str, Any]], raw: str
) -> str | None:
    """Map id or name to one device id. Prefer tv_capable on ambiguous names."""
    return resolve_device_id_preferring(devices, raw, "tv_capable")


def resolve_device_id_preferring(
    devices: list[dict[str, Any]],
    raw: str,
    prefer_flag: str,
) -> str | None:
    needle = raw.strip()
    if not needle:
        return None
    by_id = {str(d["id"]): d for d in devices if d.get("id")}
    if needle in by_id:
        return needle
    lower = needle.lower()
    exact = [
        d
        for d in devices
        if (d.get("name") or "").strip().lower() == lower
    ]
    if len(exact) == 1:
        return str(exact[0]["id"])
    if len(exact) > 1:
        capable = [d for d in exact if d.get(prefer_flag) is True]
        if len(capable) == 1:
            return str(capable[0]["id"])
        return None
    partial = [
        d
        for d in devices
        if lower in (d.get("name") or "").strip().lower()
    ]
    if len(partial) == 1:
        return str(partial[0]["id"])
    if len(partial) > 1:
        capable = [d for d in partial if d.get(prefer_flag) is True]
        if len(capable) == 1:
            return str(capable[0]["id"])
        return None
    return None


def device_not_found_error(raw: str, *, names: list[str] | None = None) -> str:
    hint = ""
    if names:
        shown = ", ".join(names[:8])
        hint = f" Known devices: {shown}."
    payload = {
        "error": {
            "code": "not_found",
            "message": f"No network device matching {raw!r}.{hint}",
            "retryable": False,
        }
    }
    return f"error: {json.dumps(payload, ensure_ascii=False)}"


def device_ambiguous_error(raw: str) -> str:
    payload = {
        "error": {
            "code": "invalid_input",
            "message": f"Multiple network devices match {raw!r}; be more specific.",
            "retryable": False,
        }
    }
    return f"error: {json.dumps(payload, ensure_ascii=False)}"


def device_resolve_error(code: str, message: str) -> str:
    payload = {"error": {"code": code, "message": message, "retryable": False}}
    return f"error: {json.dumps(payload, ensure_ascii=False)}"
