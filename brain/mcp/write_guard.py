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

_WRITE_NUDGE_GENERIC = (
    "System correction (do not repeat to the user): the user asked to change data "
    "THIS turn. Call the appropriate write tool before confirming. Do not claim the "
    "change happened without a tool result from this turn."
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
        r"\bwat\s+hebben\s+we\s+(nodig|te\s+kopen)\b",
        r"\bwat\s+moeten\s+we\s+kopen\b",
        r"\b(toon|laat)\s+(de\s+)?(boodschappen)?lijst\b",
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
        r"\bop\s+de\s+boodschappenlijst\b",
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
    )
)


def user_message_requests_write(text: str) -> bool:
    """True when the latest user message shows mutation intent."""
    if not (text or "").strip():
        return False
    normalized = text.strip()
    if any(p.search(normalized) for p in _READ_ONLY_PATTERNS):
        return False
    return any(p.search(normalized) for p in _MUTATION_PATTERNS)


def write_retry_nudge(user_message: str) -> str:
    """Prompt the model to call a write tool when it replied without one."""
    normalized = (user_message or "").strip()
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
