"""When to offer Ollama tool schemas (T-057 chat-context vs live tools)."""

from __future__ import annotations

import re

from brain.eli5 import is_eli5_intent
from brain.tool_retry import user_message_requests_preference_write

# Live house/media/lookup intents — always keep tools available.
_TOOLISH = re.compile(
    r"(?i)\b("
    r"weer|weather|regen|rain|temperatuur|umbrella|paraplu|"
    r"licht|lights?|lamp|dim|helder|kleur|party|feest|"
    r"boodschap|shopping|inkoop|lijst|"
    r"agenda|calendar|afspraak|ochtend|morning|brief|"
    r"recept|recipe|kook|importeer|opslaan|"
    r"jellyfin|film|movie|serie|kijk|"
    r"budget|euro|uitgave|expense|transact|"
    r"wikipedia|weetje|random.?fact|valuta|currency|dollar|yen|"
    r"busy|druk|usage|stats|hoe druk|"
    r"voorkeur|preference|genres?|tone|"
    r"wat kun je|what can you|capabilities|uitleg jezelf|explain yourself"
    r")\b"
)

# Suite / prompt control: echo a literal token — never a weather/prefs ask.
_ECHO_EXACT = re.compile(r"(?i)^\s*echo\s+exactly\s*:")

# Two-sided / steelman prose mode (tools only if live facts also asked).
_TWO_SIDED = re.compile(
    r"(?i)\b("
    r"both\s+sides|steelman|voor-?\s*en\s*nadelen|beide\s+kanten|"
    r"pros?\s+and\s+cons|voordeel(?:en)?\s+en\s+nadeel(?:en)?"
    r")\b"
)

# Live-fact tokens that keep tools available during ELI5 / two-sided modes.
_LIVE_FACT = re.compile(
    r"(?i)\b("
    r"weer|weather|regen|rain|temperatuur|temperature|"
    r"agenda|calendar|afspraak|"
    r"licht|lights?|lamp|"
    r"boodschap|shopping|"
    r"budget|euro|uitgave|expense|"
    r"jellyfin|film|movie|"
    r"recept|recipe|"
    r"wikipedia|valuta|currency|weetje|random.?fact"
    r")\b"
)

# Chat-only memory / recall / short acks — tools distract Qwen into echo/JSON.
_CHAT_CONTEXT = re.compile(
    r"(?i)("
    r"\bonthoud\b|"
    r"\bremember\b|"
    r"voor deze chat|"
    r"codewoord|"
    r"herhaal de (drie )?feiten|"
    r"repeat the (three )?facts|"
    r"wat was mijn|"
    r"hoe heet (de|het|mijn)|"
    r"waar (ligt|staat) |"
    r"het project heet|"
    r"plant\s*=|"
    r"sleutel\s*=|"
    r"codewoord\s*="
    r")"
)

_SHORT_ACK = re.compile(
    r"(?i)^\s*(dank je|dankjewel|bedankt|thanks|thank you|ok|okay|prima|goed|top)\s*[.!?]?\s*$"
)


def is_echo_exact_intent(user_message: str) -> bool:
    """True for Echo exactly control prompts (suite + system prompt)."""
    return bool(_ECHO_EXACT.match((user_message or "").strip()))


def is_two_sided_intent(user_message: str) -> bool:
    return bool(_TWO_SIDED.search(user_message or ""))


def is_non_live_modes_turn(user_message: str) -> bool:
    """ELI5 / two-sided without live house facts — offer no tools."""
    text = user_message or ""
    if not (is_eli5_intent(text) or is_two_sided_intent(text)):
        return False
    if _LIVE_FACT.search(text):
        return False
    return True


def should_offer_tools(user_message: str) -> bool:
    """False when the turn is chat-context only (no live tool intent)."""
    text = (user_message or "").strip()
    if not text:
        return True
    # Echo-exact controls must not trip weather/prefs via hyphenated tokens.
    if is_echo_exact_intent(text):
        return True
    if user_message_requests_preference_write(text):
        return True
    if is_non_live_modes_turn(text):
        return False
    if _TOOLISH.search(text):
        return True
    if _SHORT_ACK.match(text):
        return False
    if _CHAT_CONTEXT.search(text):
        return False
    return True


def _schema_name(schema: dict) -> str:
    fn = schema.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "")
    return str(schema.get("name") or "")


def filter_schemas_for_turn(
    schemas: list[dict],
    user_message: str,
) -> list[dict]:
    """Shrink or clear the offered tool surface for this user turn."""
    text = (user_message or "").strip()
    if not should_offer_tools(text):
        return []
    if is_echo_exact_intent(text):
        return [s for s in schemas if _schema_name(s) == "echo"]
    if is_non_live_modes_turn(text):
        return []
    return schemas
