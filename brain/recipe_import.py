"""Recipe import staging / confirm gate (T-021)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

RECIPE_ADD_TOOL = "homebase.recipes.add"

MAX_TITLE_LEN = 200
MAX_INGREDIENTS = 50
MAX_STEPS = 100
MAX_STEP_CHARS = 2000
PENDING_TTL_S = 600.0

_STEP_NUMBER_RE = re.compile(r"^\s*\d+[\.\)]\s*")

_CONFIRM_RE = re.compile(
    r"^(ja|yes|yep|yeah|ok|okay|correct|bevestig|klopt)\.?$",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"^(nee|no|nope|cancel|annuleer|stop)\.?$",
    re.IGNORECASE,
)

# Explicit save/import — avoid bare "add … recipe" meal-talk false positives.
_RECIPE_SAVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bsave\b.*\brecipe\b",
        r"\brecipe\b.*\bsave\b",
        r"\badd\s+this\s+recipe\b",
        r"\badd\s+the\s+recipe\b",
        r"\badd\b.*\brecipe\b.*\b(to\s+)?homebase\b",
        r"\bimport(eer)?\b.*\b(recipe|recept)\b",
        r"\b(recipe|recept)\b.*\bimport(eer)?\b",
        r"\bbewaar\b.*\brecept\b",
        r"\brecept\b.*\bbewaar\b",
        r"\bvoeg\b.*\brecept\b.*\btoe\b",
        r"\brecept\b.*\b(toevoegen|opslaan)\b",
        r"\bsla\b.*\brecept\b.*\bop\b",
        r"\brecept\s+opslaan\b",
        # URL + recipe/recept in the same turn (common NL/EN paste/import phrasing)
        r"\b(recipe|recept)\b.*https?://",
        r"https?://.*\b(recipe|recept)\b",
    )
)

_RECIPE_SAVE_NEGATION = re.compile(
    r"\b(don'?t|do\s+not|niet|geen)\b.*\b(save|add|import|bewaar|opslaan|toevoegen)\b|"
    r"\b(don'?t|do\s+not|niet)\b.*\b(recipe|recept)\b",
    re.IGNORECASE,
)

# After duplicate title — allow restaging with a new name while pending exists.
_RECIPE_RENAME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brename\b",
        r"\bcall\s+it\b",
        r"\bnew\s+title\b",
        r"\btitle\b.*\b(to|=|:)\b",
        r"\bnoem\b.*(het|recept)\b",
        r"\bande(?:re|re)\s+titel\b",
        r"\btitel\b.*(is|=|:)\b",
        r"\bals\s+['\"]?\w",
        r"\bsave\s+as\b",
        r"\bopslaan\s+als\b",
    )
)


@dataclass
class PendingRecipe:
    payload: dict[str, Any]
    created_monotonic: float = field(default_factory=time.monotonic)
    # Only the immediate next confirm may commit; cleared/invalidated otherwise.
    confirmable: bool = True


class PendingRecipeStore:
    """In-memory pending recipe candidates keyed by conversation id."""

    def __init__(self, *, ttl_s: float = PENDING_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._by_conversation: dict[str, PendingRecipe] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, item in self._by_conversation.items()
            if now - item.created_monotonic > self._ttl_s
        ]
        for key in expired:
            del self._by_conversation[key]

    def set(self, conversation_id: str, payload: dict[str, Any]) -> None:
        self._purge_expired()
        self._by_conversation[conversation_id] = PendingRecipe(
            payload=dict(payload),
            confirmable=True,
        )

    def get(self, conversation_id: str | None) -> dict[str, Any] | None:
        if not conversation_id:
            return None
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return dict(item.payload) if item is not None else None

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

    def invalidate_confirm(self, conversation_id: str | None) -> None:
        """Keep payload for rename restage, but block bare yes until restaged."""
        if not conversation_id:
            return
        item = self._by_conversation.get(conversation_id)
        if item is not None:
            item.confirmable = False

    def has(self, conversation_id: str | None) -> bool:
        return self.get(conversation_id) is not None


def is_bare_confirm(text: str) -> bool:
    normalized = (text or "").strip().rstrip(".!?").strip()
    return bool(normalized) and _CONFIRM_RE.match(normalized) is not None


def is_bare_cancel(text: str) -> bool:
    normalized = (text or "").strip().rstrip(".!?").strip()
    return bool(normalized) and _CANCEL_RE.match(normalized) is not None


def recipe_save_negated(text: str) -> bool:
    return bool((text or "").strip()) and _RECIPE_SAVE_NEGATION.search(text) is not None


def user_message_requests_recipe_save(text: str) -> bool:
    """True when the user explicitly asked to save/import a recipe."""
    if not (text or "").strip():
        return False
    if recipe_save_negated(text):
        return False
    return any(p.search(text) for p in _RECIPE_SAVE_PATTERNS)


def user_message_renames_pending_recipe(text: str) -> bool:
    """True when the user is renaming a staged recipe (duplicate recovery)."""
    if not (text or "").strip() or recipe_save_negated(text):
        return False
    return any(p.search(text) for p in _RECIPE_RENAME_PATTERNS)


def may_stage_recipe_add(text: str, *, has_pending: bool) -> bool:
    """Whether this turn may stage (or restage) homebase.recipes.add."""
    if user_message_requests_recipe_save(text):
        return True
    return has_pending and user_message_renames_pending_recipe(text)


def should_keep_pending_recipe(text: str) -> bool:
    """True when the user message continues a pending import dialog."""
    if is_bare_confirm(text) or is_bare_cancel(text):
        return True
    if user_message_requests_recipe_save(text):
        return True
    if user_message_renames_pending_recipe(text):
        return True
    return False


def normalize_step(step: str) -> str:
    text = (step or "").strip()
    text = _STEP_NUMBER_RE.sub("", text).strip()
    return text


# Leading amount(+unit) stuck in name — common when the model dumps the whole line.
_QTY_IN_NAME_RE = re.compile(
    r"^(?P<qty>"
    r"\d+\s+\d/\d|"  # 1 1/2
    r"\d+\s*[-/]\s*\d+|"  # 1-2 / 1/2
    r"\d+/\d+|"
    r"\d+(?:[.,]\d+)?"
    r")"
    r"(?:\s*(?P<unit>"
    r"gr|g|kg|ml|l|oz|lb|lbs|tsp|tbsp|el|tl|"
    r"teaspoons?|tablespoons?|cups?|grams?|kilograms?|ounces?|pounds?|"
    r"eetlepels?|theelepels?|gram|snufje"
    r"))?"
    r"\s+(?P<name>.+)$",
    re.IGNORECASE,
)

# Bare quantity words the model puts in both quantity and name (no leading number).
_BARE_QTY_WORD_RE = re.compile(
    r"^(?P<qty>snufje|pinch|to\s+taste)\s+(?P<name>.+)$",
    re.IGNORECASE,
)

_DEFAULT_QUANTITY = "to taste"


def _strip_qty_prefix_from_name(name: str, quantity: str) -> str:
    """Remove a leading amount from name when it duplicates quantity."""
    if not name or not quantity:
        return name
    prefix = re.match(re.escape(quantity) + r"\s+", name, re.IGNORECASE)
    if prefix:
        return name[prefix.end() :].strip()
    return name


def normalize_ingredient(item: Any) -> dict[str, str] | None:
    """Coerce one ingredient to ``{name, quantity}``; return None if unusable."""
    if isinstance(item, str):
        name = item.strip()
        quantity = ""
    elif isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        quantity = str(item.get("quantity") or "").strip()
        # Model sometimes puts the amount only in name and leaves quantity blank,
        # or swaps fields.
        if not name and quantity:
            name, quantity = quantity, ""
    else:
        return None

    # Always strip leading amount from name when present. Keep an existing
    # quantity field; only fill quantity when it was empty (avoids
    # "300 gr" + name "300 gr kipgehakt" → doubled UI display).
    match = _QTY_IN_NAME_RE.match(name) if name else None
    if match:
        qty = match.group("qty").strip()
        unit = (match.group("unit") or "").strip()
        parsed = f"{qty} {unit}".strip() if unit else qty
        if not quantity:
            quantity = parsed
        name = match.group("name").strip()
    else:
        bare = _BARE_QTY_WORD_RE.match(name) if name else None
        if bare:
            if not quantity:
                quantity = bare.group("qty").strip()
            name = bare.group("name").strip()

    name = _strip_qty_prefix_from_name(name, quantity)

    if not name:
        return None
    if not quantity:
        quantity = _DEFAULT_QUANTITY
    return {"name": name, "quantity": quantity}


def normalize_recipe_payload(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize and validate a recipes.add payload.

    Returns ``(payload, None)`` on success or ``(None, error_message)``.
    """
    title = str(raw.get("title") or "").strip()
    if not title:
        return None, "error: Invalid recipe payload — title is required"
    if len(title) > MAX_TITLE_LEN:
        return None, "error: Recipe too large — title exceeds 200 characters"

    servings = raw.get("servings")
    if servings is not None and not isinstance(servings, int):
        try:
            servings = int(servings)
        except (TypeError, ValueError):
            return None, "error: Invalid recipe payload — servings must be an integer"

    ingredients_in = raw.get("ingredients")
    if not isinstance(ingredients_in, list) or not ingredients_in:
        return None, "error: Invalid recipe payload — ingredients required"
    if len(ingredients_in) > MAX_INGREDIENTS:
        return None, "error: Recipe too large — too many ingredients"

    ingredients: list[dict[str, str]] = []
    for item in ingredients_in:
        normalized = normalize_ingredient(item)
        if normalized is None:
            continue
        ingredients.append(normalized)
    if not ingredients:
        return None, "error: Invalid recipe payload — ingredients required"

    steps_in = raw.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        return None, "error: Invalid recipe payload — steps required"
    if len(steps_in) > MAX_STEPS:
        return None, "error: Recipe too large — too many steps"

    steps: list[str] = []
    for step in steps_in:
        normalized = normalize_step(str(step))
        if not normalized:
            continue
        if len(normalized) > MAX_STEP_CHARS:
            return None, "error: Recipe too large — step exceeds 2000 characters"
        steps.append(normalized)
    if not steps:
        return None, "error: Invalid recipe payload — steps required"

    payload: dict[str, Any] = {
        "title": title,
        "ingredients": ingredients,
        "steps": steps,
    }
    if servings is not None:
        payload["servings"] = servings
    source_url = raw.get("source_url")
    if isinstance(source_url, str) and source_url.strip():
        payload["source_url"] = source_url.strip()
    return payload, None


# Show every step in the confirm UI; cap only for pathological recipes (voice length).
_CONFIRM_STEPS_CAP = 40


def awaiting_confirmation_result(payload: dict[str, Any]) -> str:
    steps = list(payload.get("steps") or [])
    body = {
        "status": "awaiting_confirmation",
        "title": payload.get("title"),
        "servings": payload.get("servings"),
        "ingredient_count": len(payload.get("ingredients") or []),
        "step_count": len(steps),
        "preview_steps": steps[:_CONFIRM_STEPS_CAP],
        "saved": False,
        "note": "Not saved yet — ask the user to confirm before claiming success.",
    }
    return json.dumps(body, ensure_ascii=False)


def build_recipe_confirm_reply(
    payload: dict[str, Any],
    *,
    user_message: str = "",
) -> str:
    """Deterministic M3 confirm copy — never claim the recipe was saved yet."""
    title = str(payload.get("title") or "recept")
    servings = payload.get("servings")
    n_ing = len(payload.get("ingredients") or [])
    steps = list(payload.get("steps") or [])
    n_steps = len(steps)
    shown = steps[:_CONFIRM_STEPS_CAP]
    omitted = n_steps - len(shown)
    dutch = bool(
        re.search(
            r"\b(importeer|bewaar|recept|voeg|opslaan|alsjeblieft|jeblieft)\b",
            user_message or "",
            re.IGNORECASE,
        )
    )
    if dutch:
        serving_bit = f", {servings} personen" if isinstance(servings, int) else ""
        lines = [
            f"Ik heb **{title}** klaargezet ({n_ing} ingrediënten, {n_steps} stappen"
            f"{serving_bit}) — nog niet opgeslagen.",
        ]
        for i, step in enumerate(shown, start=1):
            lines.append(f"{i}. {step}")
        if omitted > 0:
            lines.append(f"…en nog {omitted} stappen.")
        lines.append("Opslaan in Homebase? Zeg *ja* of tik Confirm.")
        return "\n".join(lines)
    serving_bit = f", {servings} servings" if isinstance(servings, int) else ""
    lines = [
        f"Ready to save **{title}** ({n_ing} ingredients, {n_steps} steps"
        f"{serving_bit}) — not saved yet.",
    ]
    for i, step in enumerate(shown, start=1):
        lines.append(f"{i}. {step}")
    if omitted > 0:
        lines.append(f"…and {omitted} more steps.")
    lines.append("Save to Homebase? Say *yes* or tap Confirm.")
    return "\n".join(lines)


def duplicate_title_error(text: str) -> bool:
    return "Recipe title already exists" in (text or "")


def suggest_duplicate_title(title: str) -> str:
    """Next free disambiguated title: ``Name (2)``, ``Name (3)``, …"""
    base = (title or "").strip() or "Recipe"
    # If already ends with (N), bump N; otherwise start at 2.
    suffix_m = re.match(r"^(.*)\s+\((\d+)\)\s*$", base)
    if suffix_m:
        stem = suffix_m.group(1).strip() or base
        n = int(suffix_m.group(2)) + 1
    else:
        stem = base
        n = 2
    while True:
        candidate = f"{stem} ({n})"
        if len(candidate) <= MAX_TITLE_LEN:
            return candidate
        # Shrink stem to fit suffix.
        room = MAX_TITLE_LEN - len(f" ({n})")
        if room < 1:
            return candidate[:MAX_TITLE_LEN]
        stem = stem[:room].rstrip()
        n += 1


def restage_after_duplicate_title(
    store: PendingRecipeStore,
    conversation_id: str,
) -> tuple[str, dict[str, Any]]:
    """Bump pending title and re-enable confirm. Returns (original_title, new_payload)."""
    payload = store.get(conversation_id)
    if payload is None:
        raise KeyError(conversation_id)
    original = str(payload.get("title") or "")
    updated = dict(payload)
    updated["title"] = suggest_duplicate_title(original)
    store.set(conversation_id, updated)
    return original, updated


def build_duplicate_rename_confirm_reply(
    *,
    original_title: str,
    proposed_title: str,
    user_message: str = "",
) -> str:
    """Forced copy after a title conflict — invites ja to save under proposed title."""
    dutch = bool(
        re.search(
            r"\b(importeer|bewaar|recept|voeg|opslaan|alsjeblieft|jeblieft|ja)\b",
            user_message or "",
            re.IGNORECASE,
        )
    )
    if dutch:
        return (
            f"De titel **{original_title}** bestaat al. "
            f"Opslaan als **{proposed_title}**? Zeg *ja* of tik Confirm."
        )
    return (
        f"The title **{original_title}** already exists. "
        f"Save as **{proposed_title}**? Say *yes* or tap Confirm."
    )
