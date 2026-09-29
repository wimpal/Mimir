"""Conversational cook-through over Homebase recipe steps (T-095).

Deterministic start / next / previous / repeat-step / stop (EN + NL).
Progress is SQLite-backed (conversation_id + recipe_id) so it survives brain
restart and client switches. Full-recipe listing stays on the normal LLM
search→get path — this module only claims explicit cook verbs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Locale = Literal["en", "nl"]

RECIPES_SEARCH_TOOL = "homebase.recipes.search"
RECIPES_GET_TOOL = "homebase.recipes.get"

# ---------------------------------------------------------------------------
# Phrase matchers
# ---------------------------------------------------------------------------

_COOK_START = re.compile(
    r"^(?:"
    r"cook\s+(?:the\s+|a\s+|an\s+)?"
    r"|walk\s+me\s+through\s+(?:the\s+|a\s+|an\s+)?"
    r"|guide\s+me\s+through\s+(?:the\s+|a\s+|an\s+)?"
    r"|step\s+by\s+step\s+(?:(?:with|for|through)\s+)?(?:the\s+|a\s+|an\s+)?"
    r"|start\s+cooking\s+(?:the\s+|a\s+|an\s+)?"
    r"|let'?s\s+cook\s+(?:the\s+|a\s+|an\s+)?"
    r"|kook\s+(?:de\s+|het\s+|een\s+)?"
    r"|stap\s+voor\s+stap\s+(?:(?:met|door|voor)\s+)?(?:de\s+|het\s+|een\s+)?"
    r"|loop\s+me\s+door\s+(?:de\s+|het\s+|een\s+)?"
    r"|begeleid\s+me\s+bij\s+(?:de\s+|het\s+|een\s+)?"
    r")(.+?)\s*$",
    re.IGNORECASE,
)

_NL_COOK_START = re.compile(
    r"^(?:"
    r"kook\s+"
    r"|stap\s+voor\s+stap\s+"
    r"|loop\s+me\s+door\s+"
    r"|begeleid\s+me\s+bij\s+"
    r")",
    re.IGNORECASE,
)

_COOK_NEXT = re.compile(
    r"^(?:"
    r"next(?:\s+step)?"
    r"|volgende(?:\s+stap)?"
    r")[\s,.!?—-]*$",
    re.IGNORECASE,
)

_COOK_PREV = re.compile(
    r"^(?:"
    r"previous(?:\s+step)?"
    r"|prev(?:\s+step)?"
    r"|back(?:\s+a?\s*step)?"
    r"|vorige(?:\s+stap)?"
    r"|what\s+was\s+the\s+(?:last|previous)\s+step"
    r"|wat\s+was\s+de\s+(?:vorige|laatste)\s+stap"
    r")[\s,.!?—-]*$",
    re.IGNORECASE,
)

_COOK_REPEAT_STEP = re.compile(
    r"^(?:"
    r"repeat\s+(?:the\s+|this\s+|current\s+)?step"
    r"|current\s+step"
    r"|what(?:'s|\s+is)\s+the\s+current\s+step"
    r"|herhaal\s+(?:deze\s+|de\s+|huidige\s+)?stap"
    r"|huidige\s+stap"
    r"|wat\s+(?:is|was)\s+de\s+(?:huidige\s+)?stap"
    r")[\s,.!?—-]*$",
    re.IGNORECASE,
)

_COOK_STOP = re.compile(
    r"^(?:"
    r"stop\s+cooking"
    r"|stop\s+cook[- ]?through"
    r"|done\s+cooking"
    r"|finish\s+cooking"
    r"|klaar\s+met\s+koken"
    r"|stop\s+met\s+koken"
    r"|stop\s+koken"
    r"|stop"
    r")[\s,.!?—-]*$",
    re.IGNORECASE,
)

_NL_NAV = re.compile(
    r"^(?:"
    r"volgende|vorige|herhaal\s+|huidige\s+stap|wat\s+(?:is|was)\s+de|"
    r"klaar\s+met\s+koken|stop\s+met\s+koken|stop\s+koken"
    r")",
    re.IGNORECASE,
)

# Full-list / write intents — never steal these for cook-start.
_FULL_LIST_OR_LOOKUP = re.compile(
    r"(?:"
    r"\bshow\b|\blist\b|\bdisplay\b|\btoon\b|\blaat\s+zien\b|"
    r"\bhow\s+do\s+i\s+make\b|\bhoe\s+maak\s+ik\b|"
    r"\bingredients?\b|\bingredi[eë]nten\b|"
    r"\bcalories?\b|\bcalorie"
    r")",
    re.IGNORECASE,
)

_COMPOUND_WRITE = re.compile(
    r"(?:"
    r"\band\s+add\b|\band\s+put\b|\ben\s+zet\b|\ben\s+voeg\b|"
    r"\bshopping\s+list\b|\bboodschappen"
    r")",
    re.IGNORECASE,
)

_MEAL_PLAN = re.compile(
    r"(?:"
    r"\bwhat\s+can\s+(?:i|we)\s+cook\b|"
    r"\bwat\s+kunnen\s+(?:we|ik)\s+koken\b|"
    r"\brecept\s+met\b|"
    r"\bfind\s+a\s+recipe\b|"
    r"\bzoek\s+een\s+recept\b"
    r")",
    re.IGNORECASE,
)


class _CookDb(Protocol):
    def ensure_conversation(self, conversation_id: str) -> None: ...

    def get_cook_progress(
        self, conversation_id: str, recipe_id: str
    ) -> Any: ...

    def upsert_cook_progress(
        self,
        conversation_id: str,
        recipe_id: str,
        *,
        title: str,
        steps: Any,
        step_index: int,
        locale: str,
    ) -> Any: ...

    def get_cook_active_recipe_id(self, conversation_id: str) -> str | None: ...

    def set_cook_active(self, conversation_id: str, recipe_id: str) -> None: ...

    def clear_cook_session(self, conversation_id: str) -> None: ...

    def start_cook_session(
        self,
        conversation_id: str,
        recipe_id: str,
        *,
        title: str,
        steps: Any,
        locale: str,
    ) -> Any: ...


@dataclass(frozen=True)
class ResolvedRecipe:
    recipe_id: str
    title: str
    steps: list[str]


def _normalize_title(text: str) -> str:
    cleaned = (text or "").strip().casefold()
    cleaned = re.sub(r"[\"'`]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Drop trailing "recipe" / "recept" fluff from the query.
    cleaned = re.sub(r"\s+(recipe|recept)\s*$", "", cleaned)
    return cleaned.strip()


def is_cook_start(text: str) -> bool:
    """True for standalone explicit cook-through start phrases."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    if _FULL_LIST_OR_LOOKUP.search(normalized):
        return False
    if _COMPOUND_WRITE.search(normalized):
        return False
    if _MEAL_PLAN.search(normalized):
        return False
    # Edit/save intents — defer to write_guard / recipe_import.
    from brain.recipe_import import (
        user_message_requests_recipe_edit,
        user_message_requests_recipe_save,
        user_message_requests_recipe_to_shopping,
    )

    if user_message_requests_recipe_edit(normalized):
        return False
    if user_message_requests_recipe_save(normalized):
        return False
    if user_message_requests_recipe_to_shopping(normalized):
        return False
    match = _COOK_START.match(normalized)
    if match is None:
        return False
    name = (match.group(1) or "").strip()
    return bool(name)


def extract_cook_recipe_query(text: str) -> str | None:
    match = _COOK_START.match((text or "").strip())
    if match is None:
        return None
    name = (match.group(1) or "").strip().rstrip(".!?")
    return name or None


def is_cook_next(text: str) -> bool:
    normalized = (text or "").strip()
    return bool(normalized) and _COOK_NEXT.match(normalized) is not None


def is_cook_prev(text: str) -> bool:
    normalized = (text or "").strip()
    return bool(normalized) and _COOK_PREV.match(normalized) is not None


def is_cook_repeat_step(text: str) -> bool:
    normalized = (text or "").strip()
    return bool(normalized) and _COOK_REPEAT_STEP.match(normalized) is not None


def is_cook_stop(text: str) -> bool:
    normalized = (text or "").strip()
    return bool(normalized) and _COOK_STOP.match(normalized) is not None


def is_cook_nav(text: str) -> bool:
    return (
        is_cook_next(text)
        or is_cook_prev(text)
        or is_cook_repeat_step(text)
        or is_cook_stop(text)
    )


def cook_locale(text: str) -> Locale:
    normalized = (text or "").strip()
    if _NL_COOK_START.match(normalized) or _NL_NAV.match(normalized):
        return "nl"
    return "en"


def parse_search_hits(raw: str) -> list[dict[str, Any]]:
    """Normalize recipes.search tool JSON into a list of hit dicts."""
    text = (raw or "").strip()
    if not text or text.startswith("error:"):
        return []
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [h for h in data if isinstance(h, dict)]
    if isinstance(data, dict):
        for key in ("recipes", "items", "results", "hits"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [h for h in inner if isinstance(h, dict)]
        if "id" in data or "name" in data or "title" in data:
            return [data]
    return []


def parse_recipe_detail(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text or text.startswith("error:"):
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def resolve_recipe_from_hits(
    hits: list[dict[str, Any]], query: str
) -> ResolvedRecipe | str:
    """Return a ResolvedRecipe stub (id+title only) or an error/ask code string.

    Codes: ``not_found``, ``ambiguous``.
    """
    raw_query = (query or "").strip()
    needle = _normalize_title(query)
    if not needle and not raw_query:
        return "not_found"
    if not hits:
        return "not_found"

    # Exact id match among hits (cook by id).
    id_hits = [
        h
        for h in hits
        if str(h.get("id") or "").strip() == raw_query
    ]
    if len(id_hits) == 1:
        hit = id_hits[0]
        rid = str(hit.get("id") or "").strip()
        title = str(hit.get("name") or hit.get("title") or rid).strip()
        return ResolvedRecipe(recipe_id=rid, title=title, steps=[])

    exact: list[dict[str, Any]] = []
    for hit in hits:
        title = str(hit.get("name") or hit.get("title") or "").strip()
        if needle and _normalize_title(title) == needle:
            exact.append(hit)

    if len(exact) == 1:
        hit = exact[0]
        rid = str(hit.get("id") or "").strip()
        title = str(hit.get("name") or hit.get("title") or query).strip()
        if not rid:
            return "not_found"
        return ResolvedRecipe(recipe_id=rid, title=title, steps=[])

    if len(exact) > 1:
        return "ambiguous"

    # No exact match — sole hit only if its title contains the query (or vice versa).
    if len(hits) == 1 and needle:
        hit = hits[0]
        title = str(hit.get("name") or hit.get("title") or "").strip()
        norm = _normalize_title(title)
        if needle == norm or needle in norm or norm in needle:
            rid = str(hit.get("id") or "").strip()
            if rid:
                return ResolvedRecipe(
                    recipe_id=rid, title=title or query, steps=[]
                )
        return "not_found"

    return "ambiguous"


def steps_from_detail(detail: dict[str, Any]) -> list[str]:
    raw = detail.get("steps")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for s in raw:
        if not isinstance(s, str):
            continue
        text = s.strip()
        if text:
            out.append(text)
    return out


def format_step_reply(
    *,
    title: str,
    index: int,
    steps: list[str],
    locale: Locale,
    switched: bool = False,
) -> str:
    n = len(steps)
    if n == 0:
        return empty_steps_reply(title, locale)
    idx = max(0, min(index, n - 1))
    step = steps[idx]
    if locale == "nl":
        head = f"**{title}** — stap {idx + 1}/{n}"
        if switched:
            head = f"Gewisseld naar {head}"
        return f"{head}:\n{step}"
    head = f"**{title}** — step {idx + 1}/{n}"
    if switched:
        head = f"Switched to {head}"
    return f"{head}:\n{step}"


def empty_steps_reply(title: str, locale: Locale) -> str:
    if locale == "nl":
        return (
            f"**{title}** heeft geen kookstappen. "
            "Ik kan geen cook-through starten."
        )
    return (
        f"**{title}** has no cook steps. "
        "I can't start a cook-through."
    )


def not_found_reply(query: str, locale: Locale) -> str:
    if locale == "nl":
        return f"Ik kan geen recept vinden voor “{query}”."
    return f"I couldn't find a recipe for “{query}”."


def ambiguous_reply(query: str, locale: Locale) -> str:
    if locale == "nl":
        return (
            f"Meerdere recepten passen bij “{query}”. "
            "Noem de exacte titel."
        )
    return (
        f"Several recipes match “{query}”. "
        "Please name the exact title."
    )


def finished_reply(*, title: str, locale: Locale) -> str:
    if locale == "nl":
        return f"Klaar — dat was de laatste stap van **{title}**."
    return f"Done — that was the last step of **{title}**."


def stop_reply(*, title: str | None, locale: Locale) -> str:
    if locale == "nl":
        if title:
            return f"Ok, cook-through voor **{title}** gestopt."
        return "Ok, cook-through gestopt."
    if title:
        return f"Ok, stopped cook-through for **{title}**."
    return "Ok, stopped cook-through."


def no_active_reply(locale: Locale) -> str:
    if locale == "nl":
        return (
            "Er loopt geen cook-through. "
            "Zeg bijvoorbeeld “kook de pastasalade”."
        )
    return (
        "There's no cook-through in progress. "
        'Say e.g. "cook the pastasalade".'
    )


def unavailable_reply(locale: Locale) -> str:
    if locale == "nl":
        return "Homebase recepten zijn nu niet bereikbaar."
    return "Homebase recipes aren't available right now."


def get_active_progress(db: _CookDb, conversation_id: str) -> Any | None:
    recipe_id = db.get_cook_active_recipe_id(conversation_id)
    if not recipe_id:
        return None
    return db.get_cook_progress(conversation_id, recipe_id)


def seed_cook_session(
    db: _CookDb,
    conversation_id: str,
    *,
    recipe_id: str,
    title: str,
    steps: list[str],
    locale: Locale,
) -> tuple[Any, bool]:
    """Start or switch cook session. Returns (progress, switched)."""
    prev = db.get_cook_active_recipe_id(conversation_id)
    switched = bool(prev and prev != recipe_id)
    progress = db.start_cook_session(
        conversation_id,
        recipe_id,
        title=title,
        steps=steps,
        locale=locale,
    )
    return progress, switched


def advance_step(
    db: _CookDb,
    conversation_id: str,
    *,
    delta: int,
) -> tuple[str, str]:
    """Apply next/prev. Returns (reply, anomaly)."""
    progress = get_active_progress(db, conversation_id)
    if progress is None:
        locale: Locale = "en"
        return no_active_reply(locale), "cook_through_no_active"
    locale = "nl" if progress.locale == "nl" else "en"
    steps = list(progress.steps)
    if not steps:
        db.clear_cook_session(conversation_id)
        return empty_steps_reply(progress.title, locale), "cook_through_empty"

    idx = int(progress.step_index)
    if delta > 0 and idx >= len(steps) - 1:
        return (
            finished_reply(title=progress.title, locale=locale),
            "cook_through_finished",
        )
    new_idx = max(0, min(idx + delta, len(steps) - 1))
    db.upsert_cook_progress(
        conversation_id,
        progress.recipe_id,
        title=progress.title,
        steps=steps,
        step_index=new_idx,
        locale=locale,
    )
    db.set_cook_active(conversation_id, progress.recipe_id)
    reply = format_step_reply(
        title=progress.title,
        index=new_idx,
        steps=steps,
        locale=locale,
    )
    anomaly = "cook_through_next" if delta > 0 else "cook_through_prev"
    return reply, anomaly


def repeat_current_step(
    db: _CookDb, conversation_id: str
) -> tuple[str, str]:
    progress = get_active_progress(db, conversation_id)
    if progress is None:
        return no_active_reply("en"), "cook_through_no_active"
    locale: Locale = "nl" if progress.locale == "nl" else "en"
    steps = list(progress.steps)
    if not steps:
        return empty_steps_reply(progress.title, locale), "cook_through_empty"
    reply = format_step_reply(
        title=progress.title,
        index=int(progress.step_index),
        steps=steps,
        locale=locale,
    )
    return reply, "cook_through_repeat"


def stop_cook_session(
    db: _CookDb, conversation_id: str, *, locale: Locale
) -> tuple[str, str]:
    progress = get_active_progress(db, conversation_id)
    title = progress.title if progress is not None else None
    db.clear_cook_session(conversation_id)
    return stop_reply(title=title, locale=locale), "cook_through_stop"
