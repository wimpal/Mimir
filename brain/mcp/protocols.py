"""Cinema Protocol staging / confirm gate (T-115)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from brain.recipe_import import is_bare_cancel, is_bare_confirm
from brain.repeat_last import is_repeat_intent

PROTOCOL_LIST_TOOL = "homebase.protocols.list"
PROTOCOL_RUN_TOOL = "homebase.protocols.run"
PROTOCOL_WRITE_TOOLS = frozenset({PROTOCOL_RUN_TOOL})

CINEMA_CANONICAL_NAME = "Cinema"
PENDING_TTL_S = 600.0

_PROTOCOL_RUN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bcinema\b.*\bprotocol\b",
        r"\bprotocol\b.*\bcinema\b",
        r"\bbioscoop\b.*\bprotocol\b",
        r"\bprotocol\b.*\bbioscoop\b",
    )
)

_PROTOCOL_QUESTION = re.compile(
    r"\b(what|which|hoe|wat|welke|explain|leg\s+uit)\b|"
    r"\?\s*$",
    re.IGNORECASE,
)

_PROTOCOL_NEGATION = re.compile(
    r"\b(don'?t|do\s+not|never|niet|geen|stop|cancel|annuleer|no|not)\b"
    r".*\b(cinema|bioscoop)?\s*protocol\b|"
    r"\b(cinema|bioscoop)?\s*protocol\b.*"
    r"\b(don'?t|do\s+not|never|niet|geen|no|not)\b|"
    r"\b(never|niet|geen|no|not)\b.*\b(cinema|bioscoop)\b.*\bprotocol\b|"
    r"\b(don'?t|do\s+not|never|niet)\b.*\brun\b.*\bprotocol\b",
    re.IGNORECASE,
)

_FREESTYLE_TOOLS_ON_PROTOCOL = frozenset(
    {
        "homebase.devices.wake",
        "homebase.devices.go_home",
        "homebase.devices.launch_app",
        "homebase.devices.set_input",
        "homebase.devices.power_off",
        "homebase.lights.set_state",
        "homebase.lights.party_mode",
    }
)


@dataclass
class PendingProtocol:
    payload: dict[str, Any]
    created_monotonic: float = field(default_factory=time.monotonic)
    confirmable: bool = True
    dutch: bool = False
    tool_name: str = PROTOCOL_RUN_TOOL


class PendingProtocolStore:
    """In-memory pending Protocol runs keyed by conversation id."""

    def __init__(self, *, ttl_s: float = PENDING_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._by_conversation: dict[str, PendingProtocol] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, item in self._by_conversation.items()
            if now - item.created_monotonic > self._ttl_s
        ]
        for key in expired:
            del self._by_conversation[key]

    def set(
        self,
        conversation_id: str,
        payload: dict[str, Any],
        *,
        dutch: bool | None = None,
        tool_name: str = PROTOCOL_RUN_TOOL,
    ) -> None:
        self._purge_expired()
        prior = self._by_conversation.get(conversation_id)
        if dutch is not None:
            locale = dutch
        elif prior is not None:
            locale = prior.dutch
        else:
            locale = False
        self._by_conversation[conversation_id] = PendingProtocol(
            payload=dict(payload),
            confirmable=True,
            dutch=locale,
            tool_name=tool_name if tool_name in PROTOCOL_WRITE_TOOLS else PROTOCOL_RUN_TOOL,
        )

    def get(self, conversation_id: str | None) -> dict[str, Any] | None:
        if not conversation_id:
            return None
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return dict(item.payload) if item is not None else None

    def get_tool_name(self, conversation_id: str | None) -> str:
        if not conversation_id:
            return PROTOCOL_RUN_TOOL
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        if item is None:
            return PROTOCOL_RUN_TOOL
        return item.tool_name or PROTOCOL_RUN_TOOL

    def is_dutch(self, conversation_id: str | None) -> bool:
        if not conversation_id:
            return False
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return bool(item is not None and item.dutch)

    def is_confirmable(self, conversation_id: str | None) -> bool:
        if not conversation_id:
            return False
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return bool(item is not None and item.confirmable)

    def clear(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        self._by_conversation.pop(conversation_id, None)

    def has(self, conversation_id: str | None) -> bool:
        return self.get(conversation_id) is not None


def _protocol_negated(text: str) -> bool:
    return bool((text or "").strip()) and _PROTOCOL_NEGATION.search(text) is not None


def _protocol_question(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    # Bare imperatives like "cinema protocol" must not look like questions.
    if _PROTOCOL_QUESTION.search(stripped) is None:
        return False
    # "what is the cinema protocol?" / "wat is het bioscoop protocol?"
    if re.search(r"\b(what|wat|which|welke|hoe)\b", stripped, re.IGNORECASE):
        return True
    return stripped.rstrip().endswith("?")


def user_message_requests_protocol_run(text: str) -> bool:
    """True when the user asked to run Cinema / bioscoop protocol this turn."""
    if not (text or "").strip():
        return False
    if _protocol_negated(text) or _protocol_question(text):
        return False
    return any(p.search(text) for p in _PROTOCOL_RUN_PATTERNS)


def resolve_protocol_name_from_message(text: str) -> str | None:
    """Canonical name from a known Protocol phrase; ignore model-supplied names."""
    if not user_message_requests_protocol_run(text):
        return None
    return CINEMA_CANONICAL_NAME


def may_stage_protocol_run(text: str, *, has_pending: bool) -> bool:
    del has_pending  # Cinema v1: only fresh protocol phrases stage
    if is_bare_confirm(text) or is_bare_cancel(text):
        return False
    return user_message_requests_protocol_run(text)


def should_keep_pending_protocol(text: str) -> bool:
    if not (text or "").strip():
        return False
    if is_bare_confirm(text) or is_bare_cancel(text):
        return True
    if is_repeat_intent(text):
        return True
    return user_message_requests_protocol_run(text)


def protocol_locale_dutch(user_message: str, *, prefer_dutch: bool | None = None) -> bool:
    if prefer_dutch is not None:
        return prefer_dutch
    msg = (user_message or "").lower()
    if "bioscoop" in msg:
        return True
    return any(w in msg for w in ("voer", "draai", "start het", "zet het"))


def is_freestyle_tool_on_protocol(tool_name: str) -> bool:
    return tool_name in _FREESTYLE_TOOLS_ON_PROTOCOL


def awaiting_protocol_confirmation_result(payload: dict[str, Any]) -> str:
    body = {
        "status": "awaiting_confirmation",
        "tool": PROTOCOL_RUN_TOOL,
        "name": payload.get("name") or CINEMA_CANONICAL_NAME,
        "saved": False,
        "note": "Not run yet — ask the user to confirm before claiming success.",
    }
    return json.dumps(body, ensure_ascii=False)


def protocol_dispatch_args(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or CINEMA_CANONICAL_NAME).strip() or CINEMA_CANONICAL_NAME
    return {"name": name}


def build_protocol_confirm_reply(
    payload: dict[str, Any],
    *,
    user_message: str = "",
    dutch: bool | None = None,
) -> str:
    use_dutch = protocol_locale_dutch(user_message, prefer_dutch=dutch)
    name = str(payload.get("name") or CINEMA_CANONICAL_NAME)
    if use_dutch:
        return (
            f"**{name}**-protocol starten (TV + Jellyfin; lampen na cut-off) — "
            "nog niet gedaan.\n"
            "Zeg *ja* of tik Confirm."
        )
    return (
        f"Run **{name}** protocol (TV + Jellyfin; lamps after cutoff) — not done yet.\n"
        "Say *yes* or tap Confirm."
    )


def build_protocol_success_reply(
    payload: dict[str, Any],
    result_text: str,
    *,
    dutch: bool = False,
) -> str:
    name = str(payload.get("name") or CINEMA_CANONICAL_NAME)
    notes = ""
    try:
        parsed = json.loads(result_text) if result_text.strip().startswith("{") else {}
        if isinstance(parsed, dict):
            note = parsed.get("notes") or parsed.get("note")
            if note:
                notes = f" {note}" if isinstance(note, str) else ""
    except (json.JSONDecodeError, TypeError):
        pass
    if dutch:
        return f"**{name}**-protocol gestart.{notes}".rstrip()
    return f"**{name}** protocol started.{notes}".rstrip()


def build_protocol_unavailable_reply(*, dutch: bool = False) -> str:
    if dutch:
        return (
            "Ik kan Homebase Protocols nu niet bereiken — "
            "`protocols.run` is niet beschikbaar."
        )
    return (
        "I can't reach Homebase Protocols right now — "
        "`protocols.run` is unavailable."
    )


def build_protocol_cancel_reply(*, dutch: bool = False) -> str:
    if dutch:
        return "Oké, ik start het protocol niet."
    return "Ok, I won't run that protocol."


def build_protocol_failure_reply(result_text: str, *, dutch: bool = False) -> str:
    """Surface a clear Homebase Protocol error for chat."""
    msg = ""
    body = (result_text or "").strip()
    if body.startswith("error:"):
        msg = body[6:].strip()
    elif body.startswith("{"):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                err = parsed.get("error")
                if isinstance(err, dict):
                    msg = str(err.get("message") or err.get("code") or "").strip()
                elif isinstance(err, str):
                    msg = err.strip()
                if not msg:
                    msg = str(parsed.get("status") or "").strip()
        except (json.JSONDecodeError, TypeError):
            msg = ""
    if not msg:
        msg = body or "Protocol failed"
    if dutch:
        return f"Protocol mislukt: {msg}"
    return f"Protocol failed: {msg}"


def protocol_run_succeeded(result_text: str) -> bool:
    """True when Homebase reports a successful Protocol run."""
    if not result_text or result_text.startswith("error:"):
        return False
    body = result_text.strip()
    if "\n" in body and body.split("\n", 1)[0].startswith("Note:"):
        body = body.split("\n", 1)[1].strip()
    if not body.startswith("{"):
        return False
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get("success") is False:
        return False
    if "error" in parsed:
        return False
    if parsed.get("success") is True:
        return True
    # Allow {status: "ok"|"ok_tv_only"|...} without success key (Homebase T-115).
    status = str(parsed.get("status") or "").lower()
    return status in {
        "ok",
        "ok_tv_only",
        "started",
        "completed",
        "success",
    }
