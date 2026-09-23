"""Empty-tools retry nudges for preference writes and budget follow-ups (T-090)."""

from __future__ import annotations

import re
from typing import Protocol

MAX_PREFERENCE_TOOL_NUDGES = 1
MAX_BUDGET_FOLLOWUP_NUDGES = 1

_PREF_WRITE = re.compile(
    r"(?i)("
    r"\b(remember|onthoud|set|zet)\b.*\b(preference|voorkeur|genres?|tone|toon)\b|"
    r"\b(favorite|favourite|favoriete)\s+genres?\b|"
    r"\btone\s+preference\b|"
    r"\bmy\s+favorite\s+genres\b|"
    r"\bset_preference\b"
    r")"
)

_BUDGET_PRIOR = re.compile(
    r"(?i)\b("
    r"uitgegeven|uitgave|expense|spent|spend|budget|boodschappen|"
    r"transactions?\.search|summary\.by_category|euro|€"
    r")\b"
)

_PERSON_FOLLOWUP = re.compile(
    r"(?i)^\s*(en|and)\s+([a-zA-ZÀ-ÿ][\wÀ-ÿ'-]{1,30})\s*\??\s*$"
)

_PREF_NUDGE = (
    "You must call the set_preference tool to store that preference now. "
    "Do not only claim you remembered it."
)

_BUDGET_FOLLOWUP_NUDGE = (
    "Call budgettracker.transactions.search or "
    "budgettracker.summary.by_category for the person named in the follow-up "
    "(person argument). Do not invent amounts from memory."
)


class _MessageLike(Protocol):
    role: str
    content: str | None


def user_message_requests_preference_write(text: str) -> bool:
    return bool(_PREF_WRITE.search(text or ""))


def preference_retry_nudge(_user_message: str = "") -> str:
    return _PREF_NUDGE


def prior_turn_mentions_budget(messages: list[_MessageLike]) -> bool:
    for msg in messages:
        if msg.role not in ("user", "assistant", "tool"):
            continue
        if _BUDGET_PRIOR.search(msg.content or ""):
            return True
    return False


def budget_person_followup_name(text: str) -> str | None:
    m = _PERSON_FOLLOWUP.match((text or "").strip())
    if not m:
        return None
    return m.group(2).strip()


def user_message_is_budget_person_followup(
    text: str,
    messages: list[_MessageLike],
) -> bool:
    if budget_person_followup_name(text) is None:
        return False
    return prior_turn_mentions_budget(messages)


def budget_followup_retry_nudge(_user_message: str = "") -> str:
    return _BUDGET_FOLLOWUP_NUDGE
