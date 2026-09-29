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
    "THIS turn. Call homebase.lights.set_state now — pass lamp **name**, room:room "
    "(e.g. room:woonkamer for all lamps in Woonkamer), or all: for house-wide on/off "
    "as device_id. For dim/warmth/colour: set `on: true` plus `brightness` (0–100), "
    "`color_temp_kelvin` (warm≈2700, cool≈4000), or `color_preset` (prefer over "
    "color_hex); never colour and color temperature together. For aan/uit use `on` "
    "true/false. Do not call party_mode for routine all-lights on/off. Do not list "
    "again without set_state. Do not confirm without a tool result showing success: true."
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

_RECIPE_SAVE_NUDGE = (
    "System correction (do not repeat to the user): the user asked to save/import a "
    "recipe THIS turn. From the web.fetch / paste text, extract title, "
    "ingredients[{name,quantity}], and steps[] (plain sentences, no leading 1./2.), "
    "then call homebase.recipes.add once to stage it. Do NOT call recipes.search. "
    "Do not claim it was saved yet — the brain will ask the user to confirm."
)

_RECIPE_EDIT_NUDGE = (
    "System correction (do not repeat to the user): the user asked to edit/retag/"
    "set-calories/mark-optional a recipe THIS turn. Resolve the recipe "
    "(recipes.search / known id) → recipes.get → merge deltas into a COMPLETE "
    "homebase.recipes.update payload (full-replace) → call recipes.update once to "
    "stage it. Never invent calories or nutrition the user did not state."
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
        r"\bwhat\s+can\s+we\s+cook\b",
        r"\bwat\s+kunnen\s+we\s+koken\b",
        r"\bwat\s+kunnen\s+we\s+eten\b",
        r"\brecept\s+met\b",
        r"\bhow\s+many\s+calories\b",
        r"\bhoeveel\s+calorie",
        r"\blunch\s+recipes\b",
        r"\brecepten\s+voor\s+lunch\b",
        r"\brecipe(s)?\s+for\s+lunch\b",
        r"\brecipe\s+with\b",
        r"\bfind\s+a\s+recipe\b",
        r"\bzoek\s+een\s+recept\b",
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
        # Colour/brightness status questions (T-040) — before appearance mutations
        # Only pure questions (no make/zet/dim imperative in the same message).
        r"^(?=.*\b(what|which|welke|hoe)\b)(?!.*(make|maak|zet|doe|dim|turn\s+\w+\s+to))\b.*\b(colour|color|kleur|brightness|warmth)\b",
        r"^(?=.*\bwhat\s+(colour|color)\s+is\b)(?!.*(make|maak|zet|doe|dim))",
        r"^(?=.*\bwelke\s+kleur\b)(?!.*(make|maak|zet|doe|dim))",
        r"^(?=.*\bhoe\s+fel\b)(?!.*(make|maak|zet|doe|dim))",
        # Notes reads (T-099) — before add/remove/forget; skip if mutation verbs present
        r"^(?!.*(add|remove|delete|forget|verwijder|vergeet|noteer|maak|voeg))\b.*\b"
        r"(list|show)\s+(the\s+)?notes?\b",
        r"^(?!.*(add|remove|delete|forget|verwijder|vergeet|noteer|maak|voeg))\b.*\b"
        r"what\s+(notes?\s+do\s+we\s+have|did\s+we\s+note)\b",
        r"^(?!.*(add|remove|delete|forget|verwijder|vergeet|noteer|maak|voeg))\b.*\b"
        r"(welke|toon|laat)\s+(de\s+)?notities?\b",
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
        # Notes (T-099) — non-secret household facts (not uitgave / chat remember)
        r"\badd\b.*\b(a\s+)?note\b",
        r"\b(maak|voeg)\b.*\b(een\s+)?notitie\b",
        r"\bnoteer\s+dat\b",
        r"\b(forget|remove|delete)\b.*\b(the\s+)?notes?\b",
        r"\b(verwijder|vergeet)\b.*\b(de\s+)?notitie\b",
        # Delivery / packages (T-096) — manual Homebase tracking
        r"\badd\b.*\b(a\s+)?(delivery|package|parcel)\b",
        r"\btrack\b.*\b(package|parcel|delivery|shipment)\b",
        r"\b(voeg|registreer)\b.*\b(pakket|bezorging|levering)\b",
        r"\bmark\b.*\b(as\s+)?(delivered|out\s+for\s+delivery|in\s+transit|pending|exception)\b",
        r"\bmarkeer\b.*\b(als\s+)?(bezorgd|onderweg|uit\s+voor\s+bezorging|in\s+afwachting|uitzondering)\b",
        r"\bzet\b.*\b(pakket|bezorging)\b.*\bop\b",
        r"\bzet\b.*\bop\b.*\b(onderweg|bezorgd|in\s+afwachting|uitzondering|"
        r"uit\s+voor\s+bezorging)\b",
        r"\b(set|zet)\b.*\b(status|pakket|bezorging)\b.*\b(to\s+|op\s+)?"
        r"(pending|exception|delivered|in\s+transit|out\s+for\s+delivery|"
        r"onderweg|bezorgd|in\s+afwachting|uitzondering|uit\s+voor\s+bezorging)\b",
        # Lights / smart home (IKEA Dirigera via Homebase)
        r"\bturn\s+(on|off)\b",
        # Particle-final: "turn the office light on" / "turn Ballon off"
        r"\bturn\b.+\b(on|off)\b",
        r"\bswitch\b.*\b(on|off)\b",
        r"\bdim\b.*\b\d+\s*%",
        r"\bdim\b.*\b(lamp|light|ballon|licht)\b",
        r"\b(bright(?:en)?|brightness)\b.*\b(lamp|light|to\s+\d+)\b",
        r"\bzet\b.*\bop\s+\d+\s*%",
        r"\b(make|maak|zet|doe)\b.*\b(warm(?:e)?\s+wit|warm\s+white|cool\s+white|koel)\b",
        r"\bturn\b.*\b(cool\s+white|warm\s+white|warm(?:e)?\s+wit)\b",
        r"\b(warme?\s+wit|warm\s+white)\b.*\b(lamp|licht|ballon|kantoor|in)\b",
        r"\b(make|maak|zet|doe|turn)\b.*\b\d{4}\s*(k|kelvin)\b",
        r"\b(make|maak|zet|doe|turn)\b.*\b(red|rood|blue|blauw|green|groen|yellow|geel|"
        r"pink|roze|purple|paars|orange|oranje|cyan)\b",
        r"\bzet\b.*\blamp\b.*\bop\b",
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
        # Recipe save patterns live in recipe_import.user_message_requests_recipe_save
        # (checked first) so negation cannot fall through to these.
    )
)


_DEVICE_WAKE_NUDGE = (
    "THIS turn is Wake-on-LAN for a Network device (NAS/PC/TV) — not IKEA lights. "
    "Call homebase.devices.list first, find a wake_capable match by name, then "
    "homebase.devices.wake with device_id only. Never ask for a MAC. Never call "
    "homebase.lights.set_state or party_mode for wake/wek phrases."
)
_DEVICE_TV_NUDGE = (
    "THIS turn is LG webOS TV control (SSAP) via homebase.devices.go_home / "
    "launch_app / set_input / power_off — not CEC and not Home Assistant. "
    "Call homebase.devices.list, prefer tv_capable, M3-confirm, then the tool. "
    "Jellyfin (start/run/open/launch/switch to; turn on TV and run jellyfin) → "
    "launch_app target jellyfin — beats bare Home/wake. Use wake_if_needed for "
    "start/run/open/launch and compound wake (not for switch-to; not for "
    "power_off); do not invent fixed sleeps. Console / PlayStation → input hdmi1. "
    "TV-off phrases → homebase.devices.power_off with device_id only. "
    "Play <title> on the TV is brain-orchestrated (catalogue + Sessions) — do not "
    "invent a bare launch_app for that. Never ask for SSAP client key or MAC."
)
_PROTOCOL_RUN_NUDGE = (
    "System correction (do not repeat to the user): the user asked for a "
    "household Protocol THIS turn (cinema/bioscoop protocol). Call "
    "homebase.protocols.run once with name Cinema now — do not ask for "
    "confirm; do not call devices.* or lights.set_state or party_mode."
)


def user_message_requests_write(text: str) -> bool:
    """True when the latest user message shows mutation intent."""
    if not (text or "").strip():
        return False
    from brain.mcp.lights import message_for_hints
    from brain.mcp.party_mode import (
        LightsWriteIntent,
        classify_lights_write_intent,
        user_message_requests_house_wide_lights,
        user_message_requests_party_mode,
    )
    from brain.device_inventory import user_message_requests_device_write
    from brain.mcp.protocols import user_message_requests_protocol_run
    from brain.recipe_import import (
        recipe_save_negated,
        user_message_renames_pending_recipe,
        user_message_requests_recipe_edit,
        user_message_requests_recipe_save,
        user_message_requests_recipe_to_shopping,
    )

    normalized = message_for_hints(text)
    # Hard-stop recipe negations before any mutation / meal-plan carve-outs.
    if recipe_save_negated(normalized):
        return False
    # Recipe save before meal-plan read-only carve-outs ("recept met kip"
    # must not suppress "voeg dit recept met kip toe").
    if user_message_requests_recipe_save(normalized):
        return True
    if user_message_requests_recipe_edit(normalized):
        return True
    if user_message_requests_recipe_to_shopping(normalized):
        return True
    if user_message_renames_pending_recipe(normalized):
        return True
    if user_message_requests_protocol_run(normalized):
        return True
    if user_message_requests_device_write(normalized):
        return True
    # Lights classifiers before broad lights-in-room read-only patterns
    # ("light in the house" must not suppress house-wide on/off).
    intent = classify_lights_write_intent(normalized)
    if intent in {
        LightsWriteIntent.PARTY,
        LightsWriteIntent.HOUSE_WIDE,
    }:
        return True
    if intent == LightsWriteIntent.REFUSED_PARTY:
        return user_message_requests_house_wide_lights(
            normalized
        ) or any(p.search(normalized) for p in _MUTATION_PATTERNS)
    if any(p.search(normalized) for p in _READ_ONLY_PATTERNS):
        return False
    # Negated appearance / toggle ("don't make it red", "niet rood")
    if re.search(
        r"\b(don'?t|do\s+not|niet|geen)\b.*\b(make|zet|doe|turn|dim|"
        r"red|rood|warm|cool|lamp|licht)\b",
        normalized,
        re.IGNORECASE,
    ):
        return False
    if user_message_requests_party_mode(normalized):
        return True
    return any(p.search(normalized) for p in _MUTATION_PATTERNS)


def write_retry_nudge(user_message: str) -> str:
    """Prompt the model to call a write tool when it replied without one."""
    from brain.mcp.lights import message_for_hints
    from brain.mcp.party_mode import (
        LightsWriteIntent,
        classify_lights_write_intent,
        party_mode_disallowed_this_turn,
    )
    from brain.device_inventory import (
        user_message_requests_device_tv,
        user_message_requests_device_wake,
    )
    from brain.tv_play import user_message_requests_play_on_tv
    from brain.mcp.protocols import user_message_requests_protocol_run
    from brain.recipe_import import (
        user_message_requests_recipe_edit,
        user_message_requests_recipe_save,
    )

    normalized = message_for_hints(user_message)
    if user_message_requests_protocol_run(normalized):
        return _PROTOCOL_RUN_NUDGE
    if user_message_requests_play_on_tv(normalized):
        return (
            "System correction (do not repeat to the user): the user asked to play "
            "a movie on the TV THIS turn. The brain stages play-on-TV (catalogue "
            "resolve + M3 + launch_app + Jellyfin Sessions). Do not invent a bare "
            "launch_app or a fictional play tool call."
        )
    if user_message_requests_device_tv(normalized):
        return _DEVICE_TV_NUDGE
    if user_message_requests_device_wake(normalized):
        return _DEVICE_WAKE_NUDGE
    if user_message_requests_recipe_save(normalized):
        return _RECIPE_SAVE_NUDGE
    if user_message_requests_recipe_edit(normalized):
        return _RECIPE_EDIT_NUDGE
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
    intent = classify_lights_write_intent(normalized)
    if intent == LightsWriteIntent.PARTY and not party_mode_disallowed_this_turn(
        normalized
    ):
        return _PARTY_MODE_NUDGE
    if intent in {
        LightsWriteIntent.HOUSE_WIDE,
        LightsWriteIntent.REFUSED_PARTY,
    } or re.search(
        r"\b(turn\s+(on|off)|switch\b.*\b(on|off)\b|"
        r"zet\b.*\blicht\b|doe\b.*\blicht\b.*\b(aan|uit)\b|"
        r"lamp(en)?\b.*\b(aan|uit)\b|dim\b|"
        r"(?:make|zet|doe|turn)\b.*\b(warm|cool|koel|red|rood|\d+\s*%|\d{4}\s*k)|"
        r"warm(?:e)?\s+wit|warm\s+white|\d{4}\s*(?:k|kelvin)|"
        r"(?:licht|lamp)\b.*\bin\s+(?:het\s+|de\s+)?\w+\b.*\b(aan|uit)\b|"
        r"(?:zet|doe|turn|switch)\b.*\blampen\b)\b",
        normalized,
        re.IGNORECASE,
    ):
        return _LIGHTS_SET_STATE_NUDGE
    return _WRITE_NUDGE_GENERIC


def check_write_allowed(
    tool_name: str,
    user_message: str,
    *,
    recipe_pending: bool = False,
    device_pending: bool = False,
    protocol_pending: bool = False,
) -> str | None:
    """Return an error string when a write tool must be blocked, else None."""
    if not is_write_tool(tool_name):
        return None
    from brain.mcp.party_mode import (
        LightsWriteIntent,
        classify_lights_write_intent,
        party_mode_disallowed_this_turn,
    )
    from brain.device_inventory import (
        user_message_requests_device_power_off,
        user_message_requests_device_wake,
    )
    from brain.mcp.protocols import (
        user_message_requests_protocol_run,
    )
    from brain.recipe_import import is_bare_confirm

    if user_message_requests_protocol_run(user_message):
        if tool_name != "homebase.protocols.run":
            return (
                "error: write blocked — cinema/bioscoop protocol uses "
                "homebase.protocols.run only (not freestyle TV/lights/party_mode "
                "or other writes)"
            )
    if user_message_requests_device_wake(user_message) and tool_name in {
        "homebase.lights.set_state",
        "homebase.lights.party_mode",
    }:
        return (
            "error: write blocked — wake/wek is Wake-on-LAN via homebase.devices.wake, "
            "not lights (list wake_capable devices then wake by device_id)"
        )
    if user_message_requests_device_power_off(user_message) and tool_name in {
        "homebase.lights.set_state",
        "homebase.lights.party_mode",
    }:
        return (
            "error: write blocked — TV off uses homebase.devices.power_off, "
            "not lights (list tv_capable devices then power_off by device_id)"
        )
    if tool_name == "homebase.lights.party_mode" and party_mode_disallowed_this_turn(
        user_message
    ):
        return (
            "error: write blocked — party_mode not allowed for this turn "
            "(need explicit party/feest/disco; house-wide on/off uses set_state)"
        )
    if tool_name == "homebase.lights.set_state":
        intent = classify_lights_write_intent(user_message)
        if intent == LightsWriteIntent.PARTY:
            return (
                "error: write blocked — party mode uses homebase.lights.party_mode, "
                "not set_state"
            )
    # T-053: evening wind-down may only write house-wide lights off (forced path).
    from brain.evening_wind_down import is_evening_wind_down

    if is_evening_wind_down(user_message):
        if tool_name == "homebase.lights.set_state":
            return None
        return (
            f"error: write blocked — evening wind-down only turns lights off "
            f"({tool_name})"
        )
    # T-021 / T-106: bare ja/yes unlocks recipes.add/update when staged.
    if (
        tool_name in {"homebase.recipes.add", "homebase.recipes.update"}
        and recipe_pending
        and is_bare_confirm(user_message)
    ):
        return None
    # T-108/T-101/T-112: bare ja/yes unlocks staged Network device writes.
    if (
        tool_name
        in {
            "homebase.devices.add",
            "homebase.devices.update",
            "homebase.devices.remove",
            "homebase.devices.wake",
            "homebase.devices.go_home",
            "homebase.devices.launch_app",
            "homebase.devices.set_input",
            "homebase.devices.power_off",
        }
        and device_pending
        and is_bare_confirm(user_message)
    ):
        return None
    # T-115: bare ja/yes unlocks staged protocols.run.
    if (
        tool_name == "homebase.protocols.run"
        and protocol_pending
        and is_bare_confirm(user_message)
    ):
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
