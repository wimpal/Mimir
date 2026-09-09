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
MAX_GROUP_LEN = 40
PENDING_TTL_S = 600.0


class RecipeNormalizeError(Exception):
    """Raised when an ingredient/field fails hard limits (e.g. oversized group)."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)

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
    # Locale of the original save/import turn — bare yes/ja must not flip EN/NL.
    dutch: bool = False


@dataclass
class PostSaveRecipeState:
    """TTL-limited state after a successful recipes.add (T-048)."""

    title: str
    soft_followup_offered: bool = False
    dutch: bool = False
    created_monotonic: float = field(default_factory=time.monotonic)


class PendingRecipeStore:
    """In-memory pending recipe candidates keyed by conversation id."""

    def __init__(self, *, ttl_s: float = PENDING_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._by_conversation: dict[str, PendingRecipe] = {}
        self._post_save: dict[str, PostSaveRecipeState] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, item in self._by_conversation.items()
            if now - item.created_monotonic > self._ttl_s
        ]
        for key in expired:
            del self._by_conversation[key]
        expired_post = [
            key
            for key, item in self._post_save.items()
            if now - item.created_monotonic > self._ttl_s
        ]
        for key in expired_post:
            del self._post_save[key]

    def set(
        self,
        conversation_id: str,
        payload: dict[str, Any],
        *,
        dutch: bool | None = None,
    ) -> None:
        self._purge_expired()
        prior = self._by_conversation.get(conversation_id)
        if dutch is not None:
            locale = dutch
        elif prior is not None:
            locale = prior.dutch
        else:
            locale = False
        self._by_conversation[conversation_id] = PendingRecipe(
            payload=dict(payload),
            confirmable=True,
            dutch=locale,
        )

    def get(self, conversation_id: str | None) -> dict[str, Any] | None:
        if not conversation_id:
            return None
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return dict(item.payload) if item is not None else None

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

    def invalidate_confirm(self, conversation_id: str | None) -> None:
        """Keep payload for rename restage, but block bare yes until restaged."""
        if not conversation_id:
            return
        item = self._by_conversation.get(conversation_id)
        if item is not None:
            item.confirmable = False

    def has(self, conversation_id: str | None) -> bool:
        return self.get(conversation_id) is not None

    def set_post_save(
        self,
        conversation_id: str,
        *,
        title: str,
        soft_followup_offered: bool = False,
        dutch: bool = False,
    ) -> None:
        self._purge_expired()
        self._post_save[conversation_id] = PostSaveRecipeState(
            title=(title or "").strip() or "recept",
            soft_followup_offered=soft_followup_offered,
            dutch=dutch,
        )

    def get_post_save(self, conversation_id: str | None) -> PostSaveRecipeState | None:
        if not conversation_id:
            return None
        self._purge_expired()
        return self._post_save.get(conversation_id)

    def mark_soft_followup_offered(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        self._purge_expired()
        item = self._post_save.get(conversation_id)
        if item is not None:
            item.soft_followup_offered = True

    def clear_post_save(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        self._post_save.pop(conversation_id, None)

    def has_soft_followup(self, conversation_id: str | None) -> bool:
        item = self.get_post_save(conversation_id)
        return bool(item is not None and item.soft_followup_offered)


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


def should_keep_post_save(text: str) -> bool:
    """True when bare ja/nee may still answer a post-save soft follow-up."""
    return is_bare_confirm(text) or is_bare_cancel(text)


def recipe_locale_dutch(user_message: str = "", *, prefer_dutch: bool | None = None) -> bool:
    if prefer_dutch is not None:
        return prefer_dutch
    return bool(
        re.search(
            r"\b(importeer|bewaar|recept|voeg|opslaan|alsjeblieft|jeblieft|ja|nee)\b",
            user_message or "",
            re.IGNORECASE,
        )
    )


_SOFT_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"nog\s+iets\b.*\b(?:aanpassen|toevoegen|wijzigen)\b|"
    r"\b(?:aanpassen|toevoegen|wijzigen)\b.*\?|"
    r"anything\s+else\b|"
    r"\b(?:change|add|adjust|modify)\b.*\?|"
    r"would\s+you\s+like\s+to\s+(?:change|add|adjust)|"
    r"wilt\s+u\s+nog\b"
    r")",
    re.IGNORECASE,
)


def is_soft_followup_offer(text: str) -> bool:
    """True when assistant copy offers a rhetorical post-save follow-up."""
    normalized = (text or "").strip()
    if not normalized or "?" not in normalized:
        return False
    return _SOFT_FOLLOWUP_RE.search(normalized) is not None


def parse_recipe_add_success(result: str) -> dict[str, Any] | None:
    """Parse a successful recipes.add tool JSON; require a non-empty id."""
    raw = (result or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    recipe_id = data.get("id")
    if recipe_id is None or str(recipe_id).strip() == "":
        return None
    return data


def build_recipe_saved_reply(
    payload: dict[str, Any],
    *,
    user_message: str = "",
    dutch: bool | None = None,
) -> str:
    """Deterministic post-save confirm — no rhetorical soft follow-up (T-048)."""
    title = str(
        payload.get("title") or payload.get("name") or "recept"
    ).strip() or "recept"
    servings = payload.get("servings")
    ingredients = payload.get("ingredients") or []
    steps = payload.get("steps") or []
    n_ing = len(ingredients) if isinstance(ingredients, list) else 0
    if isinstance(steps, list):
        n_steps = len(steps)
    elif isinstance(payload.get("instructions"), str) and payload["instructions"].strip():
        n_steps = len([ln for ln in str(payload["instructions"]).splitlines() if ln.strip()])
    else:
        n_steps = 0
    use_dutch = recipe_locale_dutch(user_message, prefer_dutch=dutch)
    if use_dutch:
        serving_bit = f", {servings} personen" if isinstance(servings, int) else ""
        return (
            f"**{title}** is opgeslagen in Homebase "
            f"({n_ing} ingrediënten, {n_steps} stappen{serving_bit})."
        )
    serving_bit = f", {servings} servings" if isinstance(servings, int) else ""
    return (
        f"**{title}** is saved to Homebase "
        f"({n_ing} ingredients, {n_steps} steps{serving_bit})."
    )


def build_post_save_clarify_reply(
    *,
    title: str = "",
    user_message: str = "",
    dutch: bool | None = None,
) -> str:
    use_dutch = recipe_locale_dutch(user_message, prefer_dutch=dutch)
    label = (title or "").strip()
    if use_dutch:
        if label:
            return f"Wat wilt u aanpassen aan **{label}**?"
        return "Wat wilt u aanpassen aan het recept?"
    if label:
        return f"What would you like to change about **{label}**?"
    return "What would you like to change about the recipe?"


def build_post_save_close_reply(
    *,
    user_message: str = "",
    dutch: bool | None = None,
) -> str:
    use_dutch = recipe_locale_dutch(user_message, prefer_dutch=dutch)
    if use_dutch:
        return "Oké, dan laten we het zo."
    return "Okay, we'll leave it as is."


def normalize_step(step: str) -> str:
    text = (step or "").strip()
    text = _STEP_NUMBER_RE.sub("", text).strip()
    return text


# Split multi-action blobs into one cook beat per element (T-043).
# Require a following capital so "ca. 1 minuut" / "el. zonnebloemolie" stay intact.
_STEP_SENTENCE_RE = re.compile(
    r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])"
)
_STEP_CONNECTOR_RE = re.compile(
    r"\s*(?:\band\s+then\b|\ben\s+dan\b|\bthen\b|\bdan\b|\bvervolgens\b)\s+",
    re.IGNORECASE,
)


def split_atomic_steps(steps: list[str]) -> list[str]:
    """Expand step blobs on ``;``, sentence ends, and then/dan connectors.

    Operates on cook-step strings only — never touches ingredients or ``group``.
    """
    out: list[str] = []
    for raw in steps:
        text = normalize_step(str(raw))
        if not text:
            continue
        for semi in text.split(";"):
            semi = semi.strip()
            if not semi:
                continue
            for sentence in _STEP_SENTENCE_RE.split(semi):
                sentence = sentence.strip()
                if not sentence:
                    continue
                for part in _STEP_CONNECTOR_RE.split(sentence):
                    part = normalize_step(part)
                    if part:
                        out.append(part)
    return out


# Leading amount(+unit) stuck in name — common when the model dumps the whole line.
_QTY_UNIT_ALTS = (
    r"gr|g|kg|ml|l|oz|lb|lbs|tsp|tbsp|el|tl|bos|"
    r"teaspoons?|tablespoons?|cups?|grams?|kilograms?|ounces?|pounds?|"
    r"eetlepels?|theelepels?|gram|snufje"
)
_QTY_IN_NAME_RE = re.compile(
    r"^(?P<qty>"
    r"\d+\s+\d/\d|"  # 1 1/2
    r"\d+\s*[-/]\s*\d+|"  # 1-2 / 1/2
    r"\d+/\d+|"
    r"\d+(?:[.,]\d+)?"
    r")"
    r"(?:\s*(?P<unit>"
    + _QTY_UNIT_ALTS
    + r"))?"
    r"\s+(?P<name>.+)$",
    re.IGNORECASE,
)

# Bare quantity words the model puts in both quantity and name (no leading number).
_BARE_QTY_WORD_RE = re.compile(
    r"^(?P<qty>snufje|pinch|to\s+taste)\s+(?P<name>.+)$",
    re.IGNORECASE,
)

_DEFAULT_QUANTITY = "to taste"

# LLM sometimes invents Dutch "to taste" phrasing instead of leaving qty empty.
_INVENTED_QTY_FLUFF_RE = re.compile(
    r"^(aan\s+de\s+smaak|te\s+bespreken|teugen|naar\s+(?:eigen\s+)?smaak)$",
    re.IGNORECASE,
)

_KNOWN_UNITS = frozenset(
    u.lower()
    for u in (
        "gr",
        "g",
        "kg",
        "ml",
        "l",
        "oz",
        "lb",
        "lbs",
        "tsp",
        "tbsp",
        "el",
        "tl",
        "bos",
        "teaspoon",
        "teaspoons",
        "tablespoon",
        "tablespoons",
        "cup",
        "cups",
        "gram",
        "grams",
        "kilogram",
        "kilograms",
        "ounce",
        "ounces",
        "pound",
        "pounds",
        "eetlepel",
        "eetlepels",
        "theelepel",
        "theelepels",
        "snufje",
    )
)

_STEM_MIN_LEN = 3

# Only collapse known LLM double-stem artifacts (avoid paprika/paprikapoeder).
_KNOWN_NAME_STEM_PAIRS = frozenset(
    {
        frozenset({"kom", "komkommer"}),
        frozenset({"bosui", "bosuitjes"}),
    }
)


def _strip_qty_prefix_from_name(name: str, quantity: str) -> str:
    """Remove a leading amount from name when it duplicates quantity."""
    if not name or not quantity:
        return name
    prefix = re.match(re.escape(quantity) + r"\s+", name, re.IGNORECASE)
    if prefix:
        return name[prefix.end() :].strip()
    return name


def _is_stem_of(short: str, long: str) -> bool:
    """True when short is a proper prefix of long (min length, case-insensitive)."""
    s = short.lower()
    l = long.lower()
    return len(s) >= _STEM_MIN_LEN and len(l) > len(s) and l.startswith(s)


def _is_known_unit_token(token: str) -> bool:
    return token.lower() in _KNOWN_UNITS


def _collapse_doubled_name_stems(name: str) -> str:
    """Collapse adjacent known doubles like ``bosuitjes bosui`` → ``bosuitjes``."""
    tokens = name.split()
    if len(tokens) < 2:
        return name
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens):
            a, b = tokens[i], tokens[i + 1]
            pair = frozenset({a.lower(), b.lower()})
            if pair in _KNOWN_NAME_STEM_PAIRS:
                # Prefer the longer surface form.
                out.append(a if len(a) >= len(b) else b)
                i += 2
                continue
        out.append(tokens[i])
        i += 1
    return " ".join(out)


def _strip_redundant_qty_name_stem(name: str, quantity: str) -> tuple[str, str]:
    """Fix mirrored LLM mistakes between quantity and name stems.

    - ``1/2 kom`` + ``komkommer`` → ``1/2`` + ``komkommer`` (non-unit prefix)
    - ``2 bosuitjes`` + ``bosui`` → ``2`` + ``bosuitjes`` (prefer longer form)
    - ``1 bos`` + ``bosui`` stays unchanged (``bos`` is a known unit)
    - ``2 theelepels`` + ``thee`` stays unchanged (unit, not ingredient stem)
    """
    if not name or not quantity:
        return name, quantity
    qty_parts = quantity.split()
    name_parts = name.split()
    if not qty_parts or not name_parts:
        return name, quantity
    last_qty = qty_parts[-1]
    first_name = name_parts[0]

    # Quantity ends with longer form; name is a short stem of that form.
    # Skip when the longer token is a measurement unit.
    if (
        not _is_known_unit_token(last_qty)
        and frozenset({first_name.lower(), last_qty.lower()}) in _KNOWN_NAME_STEM_PAIRS
        and _is_stem_of(first_name, last_qty)
    ):
        name = last_qty + (" " + " ".join(name_parts[1:]) if len(name_parts) > 1 else "")
        quantity = " ".join(qty_parts[:-1]).strip()
        return name, quantity

    # Quantity's last word is a non-unit stem prefix of the name (known pairs only).
    if (
        not _is_known_unit_token(last_qty)
        and frozenset({last_qty.lower(), first_name.lower()}) in _KNOWN_NAME_STEM_PAIRS
        and _is_stem_of(last_qty, first_name)
    ):
        quantity = " ".join(qty_parts[:-1]).strip()
        return name, quantity

    return name, quantity


def _strip_fluff_from_name(name: str) -> str:
    """Remove leading invented Dutch qty fluff stuck in the name field."""
    stripped = re.sub(
        r"^(aan\s+de\s+smaak|te\s+bespreken|teugen|naar\s+(?:eigen\s+)?smaak)\s+",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    return stripped or name


def normalize_ingredient(item: Any) -> dict[str, str] | None:
    """Coerce one ingredient to ``{name, quantity, group?}``; return None if unusable.

    Raises ``RecipeNormalizeError`` when ``group`` exceeds ``MAX_GROUP_LEN``.
    """
    group_raw: str | None = None
    if isinstance(item, str):
        name = item.strip()
        quantity = ""
    elif isinstance(item, dict):
        name = str(item.get("name") or "").strip()
        quantity = str(item.get("quantity") or "").strip()
        if item.get("group") is not None:
            group_raw = str(item.get("group") or "")
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
    name = _strip_fluff_from_name(name)
    name = _collapse_doubled_name_stems(name)
    name, quantity = _strip_redundant_qty_name_stem(name, quantity)
    name = _collapse_doubled_name_stems(name)

    # Legacy T-047 name suffixes → group (never keep both).
    suffix_group = _group_from_name_suffix(name)
    if suffix_group:
        name = _strip_subsection_suffix(name)

    if not name:
        return None
    if quantity and _INVENTED_QTY_FLUFF_RE.match(quantity.strip()):
        quantity = _DEFAULT_QUANTITY
    if not quantity:
        quantity = _DEFAULT_QUANTITY

    group = _canonical_group(group_raw) if group_raw is not None else None
    if group is None and suffix_group:
        group = suffix_group

    out: dict[str, str] = {"name": name, "quantity": quantity}
    if group:
        out["group"] = group
    return out


# Paste subsection headers the LLM often drops (T-047 / T-049).
_SUBSECTION_HEADER_RE = re.compile(
    r"^(?:voor\s+de\s+|for\s+the\s+)?"
    r"(?P<label>dressing|marinade|sauce|saus|topping|garnish|garnering)\s*:?\s*$",
    re.IGNORECASE,
)
_SUBSECTION_STOP_RE = re.compile(
    r"^(bereidingswijze|instructions?|method|methode|directions|steps?|"
    r"benodigdheden|ingredients?)\b",
    re.IGNORECASE,
)

_GROUP_ALIASES = {
    "dressing": "dressing",
    "marinade": "marinade",
    "sauce": "sauce",
    "saus": "sauce",
    "topping": "topping",
    "garnish": "garnish",
    "garnering": "garnish",
}

_SUBSECTION_SUFFIX_STRIP_RE = re.compile(
    r"\s*\((?:voor\s+)?"
    r"(?P<label>dressing|marinade|sauce|saus|topping|garnish|garnering)\)\s*$",
    re.IGNORECASE,
)

_QTY_UNIT_CANON = {
    "gr": "g",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "ml": "ml",
    "l": "l",
    "el": "el",
    "tl": "tl",
    "tbsp": "tbsp",
    "tsp": "tsp",
}


def _canonical_group(raw: str | None) -> str | None:
    """Return a canonical group key, or None when empty. Raises if too long."""
    if raw is None:
        return None
    trimmed = str(raw).strip()
    if not trimmed:
        return None
    if len(trimmed) > MAX_GROUP_LEN:
        raise RecipeNormalizeError(
            "error: Recipe too large — ingredient group exceeds 40 characters"
        )
    key = trimmed.casefold()
    return _GROUP_ALIASES.get(key, trimmed.casefold())


def _group_from_name_suffix(name: str) -> str | None:
    match = _SUBSECTION_SUFFIX_STRIP_RE.search(name or "")
    if not match:
        return None
    return _canonical_group(match.group("label"))


def _strip_subsection_suffix(name: str) -> str:
    return _SUBSECTION_SUFFIX_STRIP_RE.sub("", name).strip()


def _normalize_qty_key(quantity: str) -> str:
    """Casefold quantity and collapse g/gr/gram(s) for merge/dedupe keys."""
    parts = (quantity or "").strip().casefold().split()
    if not parts:
        return ""
    out: list[str] = []
    for part in parts:
        out.append(_QTY_UNIT_CANON.get(part, part))
    return " ".join(out)


_PAREN_NOTE_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _name_match_key(name: str) -> str:
    """Casefold name with trailing ``(brand/note)`` stripped for twin matching."""
    return _PAREN_NOTE_RE.sub("", name or "").strip().casefold()


def _ingredient_key(norm: dict[str, str]) -> tuple[str, str, str]:
    return (
        _name_match_key(norm["name"]),
        _normalize_qty_key(norm["quantity"]),
        (norm.get("group") or "").casefold(),
    )


def _base_qty_key(norm: dict[str, str]) -> tuple[str, str]:
    return (_name_match_key(norm["name"]), _normalize_qty_key(norm["quantity"]))


_MAIN_INGREDIENTS_START_RE = re.compile(
    r"^(benodigdheden|ingredi[eë]nten|ingredients?)\b",
    re.IGNORECASE,
)


def parse_subsection_ingredients_from_text(
    source_text: str,
) -> list[dict[str, str]]:
    """Pull ingredient lines under dressing/marinade/… headers from a paste."""
    if not (source_text or "").strip():
        return []
    lines = source_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    found: list[dict[str, str]] = []
    i = 0
    while i < len(lines):
        header = _SUBSECTION_HEADER_RE.match(lines[i].strip())
        if not header:
            i += 1
            continue
        group = _canonical_group(header.group("label"))
        i += 1
        while i < len(lines):
            raw = lines[i].strip()
            if not raw:
                i += 1
                # Blank line inside a block is fine; stop only on next header/section.
                if i < len(lines) and (
                    _SUBSECTION_HEADER_RE.match(lines[i].strip())
                    or _SUBSECTION_STOP_RE.match(lines[i].strip())
                ):
                    break
                continue
            if _SUBSECTION_HEADER_RE.match(raw) or _SUBSECTION_STOP_RE.match(raw):
                break
            parsed = normalize_ingredient(raw)
            i += 1
            if parsed is None:
                continue
            row = {"name": parsed["name"], "quantity": parsed["quantity"]}
            if group:
                row["group"] = group
            found.append(row)
    return found


def parse_main_ingredient_keys_from_text(source_text: str) -> set[tuple[str, str]]:
    """``(base_name, qty_key)`` keys from the main list before any subsection."""
    if not (source_text or "").strip():
        return set()
    lines = source_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    keys: set[tuple[str, str]] = set()
    started = False
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        if _MAIN_INGREDIENTS_START_RE.match(raw):
            started = True
            continue
        if _SUBSECTION_HEADER_RE.match(raw):
            break
        if started and re.match(
            r"^(bereidingswijze|instructions?|method|methode|directions|steps?)\b",
            raw,
            re.IGNORECASE,
        ):
            break
        if not started:
            continue
        parsed = normalize_ingredient(raw)
        if parsed is None:
            continue
        keys.add(_base_qty_key(parsed))
    return keys


def merge_subsection_ingredients_from_source(
    source_text: str,
    ingredients: list[Any],
) -> list[Any]:
    """Fold paste subsection lines into the LLM list without duplicating.

    - Identical ``(name, qty, group)`` → skip.
    - Ungrouped twin of a subsection row: upgrade to ``group`` unless that
      base+qty is in the paste *main* list (then append the grouped twin).
    - Qty match uses ``g``/``gr`` normalization.
    - Final pass drops duplicate ``(name, qty, group)`` rows.
    """
    extras = parse_subsection_ingredients_from_text(source_text)
    if not extras:
        return list(ingredients)

    main_keys = parse_main_ingredient_keys_from_text(source_text)
    merged: list[Any] = list(ingredients)

    def _norm_at(idx: int) -> dict[str, str] | None:
        return normalize_ingredient(merged[idx])

    for extra in extras:
        extra_norm = normalize_ingredient(extra)
        if extra_norm is None:
            continue
        extra_full = _ingredient_key(extra_norm)
        extra_base = _base_qty_key(extra_norm)
        extra_group = extra_norm.get("group") or ""

        already = False
        ungrouped_matches: list[int] = []
        for idx in range(len(merged)):
            norm = _norm_at(idx)
            if norm is None:
                continue
            if _ingredient_key(norm) == extra_full:
                already = True
                continue
            if _base_qty_key(norm) != extra_base:
                continue
            if (norm.get("group") or "").casefold() == extra_group.casefold():
                already = True
                continue
            if not (norm.get("group") or "").strip():
                ungrouped_matches.append(idx)

        if already:
            # Drop ungrouped twins that are not in the paste main list
            # (plain + grouped duplicate from LLM suffix leftovers).
            for idx in reversed(ungrouped_matches):
                if extra_base not in main_keys:
                    del merged[idx]
            continue
        if len(ungrouped_matches) >= 2:
            # Prefer upgrading the last twin (dressing usually listed after main).
            idx = ungrouped_matches[-1]
            upgraded = dict(normalize_ingredient(merged[idx]) or {})
            # Prefer paste/extra name (often includes brand note).
            upgraded["name"] = extra_norm["name"]
            upgraded["quantity"] = extra_norm["quantity"]
            if extra_group:
                upgraded["group"] = extra_group
            merged[idx] = upgraded
            continue
        if len(ungrouped_matches) == 1:
            if extra_base in main_keys:
                merged.append(extra_norm)
            else:
                idx = ungrouped_matches[0]
                upgraded = dict(normalize_ingredient(merged[idx]) or {})
                upgraded["name"] = extra_norm["name"]
                upgraded["quantity"] = extra_norm["quantity"]
                if extra_group:
                    upgraded["group"] = extra_group
                merged[idx] = upgraded
            continue
        merged.append(extra_norm)

    # Dedupe identical (name, qty, group); keep first occurrence.
    deduped: list[Any] = []
    seen: set[tuple[str, str, str]] = set()
    for item in merged:
        norm = normalize_ingredient(item)
        if norm is None:
            continue
        key = _ingredient_key(norm)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(norm)
    return deduped


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
    try:
        for item in ingredients_in:
            normalized = normalize_ingredient(item)
            if normalized is None:
                continue
            ingredients.append(normalized)
    except RecipeNormalizeError as exc:
        return None, exc.message
    if not ingredients:
        return None, "error: Invalid recipe payload — ingredients required"

    steps_in = raw.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        return None, "error: Invalid recipe payload — steps required"
    if len(steps_in) > MAX_STEPS:
        return None, "error: Recipe too large — too many steps"

    steps: list[str] = []
    for fragment in split_atomic_steps([str(s) for s in steps_in]):
        if len(fragment) > MAX_STEP_CHARS:
            return None, "error: Recipe too large — step exceeds 2000 characters"
        steps.append(fragment)
    if len(steps) > MAX_STEPS:
        return None, "error: Recipe too large — too many steps"
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

_GROUP_HEADING_NL = {
    "dressing": "Voor de dressing",
    "marinade": "Voor de marinade",
    "sauce": "Voor de saus",
    "topping": "Voor de topping",
    "garnish": "Voor de garnering",
}
_GROUP_HEADING_EN = {
    "dressing": "Dressing",
    "marinade": "Marinade",
    "sauce": "Sauce",
    "topping": "Topping",
    "garnish": "Garnish",
}


def _group_heading(group: str, *, dutch: bool) -> str:
    key = (group or "").casefold()
    table = _GROUP_HEADING_NL if dutch else _GROUP_HEADING_EN
    return table.get(key, group)


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
    dutch: bool | None = None,
) -> str:
    """Deterministic M3 confirm copy — never claim the recipe was saved yet."""
    title = str(payload.get("title") or "recept")
    servings = payload.get("servings")
    ingredients = list(payload.get("ingredients") or [])
    n_ing = len(ingredients)
    steps = list(payload.get("steps") or [])
    n_steps = len(steps)
    shown = steps[:_CONFIRM_STEPS_CAP]
    omitted = n_steps - len(shown)
    use_dutch = recipe_locale_dutch(user_message, prefer_dutch=dutch)

    def _append_ingredients(lines: list[str]) -> None:
        prev_group: str | None = None
        for item in ingredients:
            group = str(item.get("group") or "").strip()
            if group and group.casefold() != (prev_group or "").casefold():
                lines.append(_group_heading(group, dutch=use_dutch))
            prev_group = group or None
            qty = str(item.get("quantity") or "").strip()
            name = str(item.get("name") or "").strip()
            lines.append(f"- {qty} {name}".strip())

    if use_dutch:
        serving_bit = f", {servings} personen" if isinstance(servings, int) else ""
        lines = [
            f"Ik heb **{title}** klaargezet ({n_ing} ingrediënten, {n_steps} stappen"
            f"{serving_bit}) — nog niet opgeslagen.",
        ]
        _append_ingredients(lines)
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
    _append_ingredients(lines)
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
    dutch: bool | None = None,
) -> str:
    """Forced copy after a title conflict — invites ja to save under proposed title."""
    use_dutch = recipe_locale_dutch(user_message, prefer_dutch=dutch)
    if use_dutch:
        return (
            f"De titel **{original_title}** bestaat al. "
            f"Opslaan als **{proposed_title}**? Zeg *ja* of tik Confirm."
        )
    return (
        f"The title **{original_title}** already exists. "
        f"Save as **{proposed_title}**? Say *yes* or tap Confirm."
    )
