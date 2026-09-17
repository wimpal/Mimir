"""When to offer Ollama tool schemas (T-057 chat-context vs live tools)."""

from __future__ import annotations

import re

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
    r"voorkeur|preference|genre|tone|"
    r"wat kun je|what can you|capabilities|uitleg jezelf|explain yourself"
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


def should_offer_tools(user_message: str) -> bool:
    """False when the turn is chat-context only (no live tool intent)."""
    text = (user_message or "").strip()
    if not text:
        return True
    if _TOOLISH.search(text):
        return True
    if _SHORT_ACK.match(text):
        return False
    if _CHAT_CONTEXT.search(text):
        return False
    return True
