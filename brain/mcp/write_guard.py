"""Confirmation-before-write guard for MCP mutation tools.

Conservative EN/NL keyword heuristics — safety net, not full NLU.
See contracts/mimir.client.md and T-012 acceptance utterances.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from brain.mcp.errors import is_write_tool
from brain.mcp.log import append_mcp_tool_log

MAX_WRITE_TOOL_NUDGES = 1

_TASK_COMPLETE_NUDGE = (
    "System correction (do not repeat to the user): the user asked to complete a chore "
    "THIS turn. Call homebase.tasks.complete now — pass the chore title as id. Do not "
    "confirm completion without a tool result containing completion_recorded."
)

_LIGHTS_SET_STATE_NUDGE = (
    "System correction (do not repeat to the user): the user asked to change a light "
    "THIS turn. Call homebase.lights.set_state now — pass lamp **name** or room:room "
    "(e.g. room:woonkamer for all lamps in Woonkamer) as device_id, and set `on` from "
    "aan/uit (true/false). Do not list again without set_state. Do not confirm without "
    "a tool result showing success: true."
)

_WRITE_NUDGE_GENERIC = (
    "System correction (do not repeat to the user): the user asked to change data "
    "THIS turn. Call the appropriate write tool before confirming. Do not claim the "
    "change happened without a tool result from this turn."
)

_PARTY_MODE_NUDGE = (
    "System correction (do not repeat to the user): the user asked for party mode "
    "THIS turn. Call homebase.lights.party_mode now — one call only; do not call "
    "lights.list or set_state. Confirm from tool result success: true."
)

# Read-only questions — checked before mutation patterns.
_READ_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwhat('s| is)\s+(on\s+the\s+)?(shopping\s+)?list\b",
        r"\bwhat('s| is)\s+low\b",
        r"\bhow\s+much\s+(did\s+we\s+)?spend",
        r"\bwhat\s+did\s+we\s+spend\b",
        r"\bhoeveel\b.*\b(uitgegeven|betaald|gedaan|besteed)\b",
        r"\bwat\s+hebben\s+we\b.*\b(uitgegeven|betaald|gedaan|besteed)\b",
        r"\bwhat\s+can\s+i\s+cook\b",
        r"\bwhat\s+do\s+we\s+need\s+to\s+buy\b",
        r"\bwhat('s| is)\s+in\s+the\s+pantry\b",
        r"\bwhat\s+needs\s+doing\b",
        r"\bwhat('s| is)\s+(over)?due\b",
        r"\bwelke\s+taken\b",
        r"\bhoeveel\b.*\b(voorraad|op\s+voorraad)\b",
        # NL shopping list reads — before mutation "op de boodschappenlijst"
        r"\bwat\s+(staat|is|zit)\s+(er\s+)?op\s+de\s+(boodschappen)?lijst\b",
        # Inverted NL word order: "wat er op de (boodschappen)lijst staat"
        r"\bwat\s+er\b.*\b(op\s+de\s+)?(boodschappen)?lijst\b",
        r"\bwat\s+hebben\s+we\s+(nodig|te\s+kopen)\b",
        r"\bwat\s+moeten\s+we\s+kopen\b",
        r"\b(toon|laat)\s+(de\s+)?(boodschappen)?lijst\b",
        # Lights reads — before mutation "turn on/off" / "licht uit"
        r"\b(which|what)\s+(ikea\s+)?lights\b",
        r"\blights?\s+in\s+(the\s+)?\w+",
        r"\blist\s+(the\s+)?lamps?\b",
        r"\bwelke\s+(lampen|lichten)\b",
        r"\blichten?\s+in\s+\w+",
    )
)

# Phrases that indicate the user asked for a mutation this turn.
# Order: longer phrases first where regex alternation matters.
_MUTATION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # Shopping list
        r"\badd\b.*\b(to\s+the\s+)?(shopping\s+)?list\b",
        r"\bput\b.*\bon\s+the\s+(shopping\s+)?list\b",
        r"\bvoeg\b.*\b(toe|toe aan)\b",
        r"\bzet\b.*\b(op\s+de\s+)?(boodschappen)?lijst\b",
        r"\bshopping\s+list\b.*\badd\b",
        # Spending / expenses
        r"\bwe\s+spent\b",
        r"\b(i|we)\s+(paid|bought|purchased)\b",
        r"\bspent\s+€",
        r"\b€\s*\d",
        r"\b(betaald|uitgegeven|gekocht|boodschappen\s+gedaan)\b",
        r"\brecord\b.*\b(expense|transaction|uitgave)\b",
        r"\bvoeg\b.*\buitgave\b",
        r"\bnoteer\b.*\buitgave\b",
        r"\bregistreer\b.*\buitgave\b",
        # Inventory / stock changes
        r"\bwe\s+used\b",
        r"\bused\s+(two|three|\d+)\b",
        r"\bset\b.*\b(to|quantity)\b",
        r"\bwe\s+have\s+\d+\s+left\b",
        r"\b(gebruikt|opgebruikt|op\s+is|op\s+raakt)\b",
        r"\bvoorraad\b.*\b(aanpassen|bijwerken|update)\b",
        r"\binventory\b.*\bupdate\b",
        # Out-of-stock statements implying restock (M3 demo)
        r"\b(out\s+of|we'?re\s+out\s+of|op\s+is)\b",
        r"\bniet\s+meer\b.*\b(koffie|melk|eieren|brood)\b",
        # Generic mutation verbs when clearly imperative
        r"\b(add|remove|delete|update|record|mark)\b\s+\w",
        r"\b(voeg|verwijder|pas\s+aan|noteer|registreer)\b",
        # Tasks / chores
        r"\b(voeg|maak)\b.*\b(taak|karwee|taken)\b",
        r"\b(taak|karwee)\b.*\b(afvinken|klaar|gedaan)\b",
        r"\bafvinken\b.*\b(taak|karwee)\b",
        r"\bmarkeer\b.*\b(compleet|klaar|af|gedaan|voltooid)\b",
        # Lights / smart home (IKEA Dirigera via Homebase)
        r"\bturn\s+(on|off)\b",
        r"\bswitch\b.*\b(on|off)\b",
        r"\bdim\b.*\b\d+\s*%",
        r"\bzet\b.*\b(het\s+)?licht\b.*\b(aan|uit)\b",
        r"\bzet\b.*\b(\w+)\s+lamp\b.*\b(aan|uit)\b",
        r"\bdoe\b.*\b(het\s+)?licht\b.*\b(aan|uit)\b",
        r"\blamp(en)?\b.*\b(aan|uit)\b",
        r"\b(?:licht|lamp)\b.*\bin\s+(?:het\s+|de\s+)?\w+\b.*\b(aan|uit|uitzetten)\b",
        r"\b(licht|lamp)\b.*\b(in\s+)?kantoor\b.*\b(aan|uit|uitzetten)\b",
        r"\b(?:zet|doe|turn|switch)\b.*\blampen\b",
        r"\blichten\s+in\s+\w+\b",
        # Party mode (T-039) — all IKEA lights flicker show
        r"\bparty\s+mode\b",
        r"\blet'?s\s+party\b",
        r"\bstart\s+the\s+party\b",
        r"\bdisco\s+mode\b",
        r"\bparty\s+tijd\b",
        r"\bfeestmodus\b",
        r"\bfeest\b",
        r"\bdisco\b",
    )
)


def user_message_requests_write(text: str) -> bool:
    """True when the latest user message shows mutation intent."""
    if not (text or "").strip():
        return False
    from brain.mcp.lights import message_for_hints
    from brain.mcp.party_mode import user_message_requests_party_mode

    normalized = message_for_hints(text)
    if any(p.search(normalized) for p in _READ_ONLY_PATTERNS):
        return False
    if user_message_requests_party_mode(normalized):
        return True
    return any(p.search(normalized) for p in _MUTATION_PATTERNS)


def write_retry_nudge(user_message: str) -> str:
    """Prompt the model to call a write tool when it replied without one."""
    from brain.mcp.lights import message_for_hints
    from brain.mcp.party_mode import user_message_requests_party_mode

    normalized = message_for_hints(user_message)
    if re.search(
        r"\b(markeer|mark|complete|afvinken)\b.*\b(compleet|klaar|af|gedaan|voltooid|done)\b",
        normalized,
        re.IGNORECASE,
    ):
        return _TASK_COMPLETE_NUDGE
    if re.search(
        r"\b(task|taak|karwee|chore)\b.*\b(done|complete|afvinken|klaar|gedaan)\b",
        normalized,
        re.IGNORECASE,
    ):
        return _TASK_COMPLETE_NUDGE
    if user_message_requests_party_mode(normalized):
        return _PARTY_MODE_NUDGE
    if re.search(
        r"\b(turn\s+(on|off)|switch\b.*\b(on|off)\b|"
        r"zet\b.*\blicht\b|doe\b.*\blicht\b.*\b(aan|uit)\b|"
        r"lamp(en)?\b.*\b(aan|uit)\b|dim\b.*\d+\s*%|"
        r"(?:licht|lamp)\b.*\bin\s+(?:het\s+|de\s+)?\w+\b.*\b(aan|uit)\b|"
        r"(?:zet|doe|turn|switch)\b.*\blampen\b)\b",
        normalized,
        re.IGNORECASE,
    ):
        return _LIGHTS_SET_STATE_NUDGE
    return _WRITE_NUDGE_GENERIC


def check_write_allowed(tool_name: str, user_message: str) -> str | None:
    """Return an error string when a write tool must be blocked, else None."""
    if not is_write_tool(tool_name):
        return None
    if user_message_requests_write(user_message):
        return None
    return (
        f"error: write blocked — user did not request a mutation this turn "
        f"({tool_name})"
    )


def log_blocked_write(
    data_dir: Path,
    *,
    service: str | None,
    tool_name: str,
    args: dict[str, Any] | None,
) -> None:
    """Record a blocked write attempt in mcp_tools.jsonl."""
    append_mcp_tool_log(
        data_dir,
        service=service or "unknown",
        tool=tool_name,
        args=args or {},
        latency_ms=0.0,
        outcome="blocked",
        detail="user did not request mutation",
    )
