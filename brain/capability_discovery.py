"""Capability discovery — live tools only (T-052).

Deterministic answers for "What can you do?" / "Wat kun je?" and
"How does X work?" from the registered tool registry. Never advertises
FUTURE_FEATURES, ROADMAP, or parked items.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

import httpx

from brain.config import McpServiceSettings, Settings
from brain.mcp.names import display_service_name
from brain.tools import Tool

logger = logging.getLogger("mimir.capability_discovery")

Locale = Literal["en", "nl"]

# Internal / plumbing tools omitted from overview (still explainable if named).
_OVERVIEW_EXCLUDE: frozenset[str] = frozenset(
    {
        "echo",
        "get_server_time",
        "get_preference",
        "set_preference",
        "list_preferences",
        "web.fetch",
    }
)

# Local household-facing tools → cluster id.
_LOCAL_CLUSTER: dict[str, str] = {
    "get_weather": "local.weather",
    "get_calendar": "local.calendar",
    "recommend_movies": "local.media",
    "list_recently_watched": "local.media",
}

# Cluster id → (label_en, label_nl) derived from name prefixes, not descriptions.
_CLUSTER_LABELS: dict[str, tuple[str, str]] = {
    "local.weather": ("weather", "weer"),
    "local.calendar": ("calendar", "agenda"),
    "local.media": ("movies and recently watched", "films en recent bekeken"),
    "homebase.shopping_list": ("shopping lists", "boodschappenlijsten"),
    "homebase.inventory": ("inventory", "voorraad"),
    "homebase.recipes": ("recipes", "recepten"),
    "homebase.tasks": ("household tasks", "huishoudtaken"),
    "homebase.lights": ("lights", "lampen"),
    "homebase.changes": ("Homebase change history", "Homebase-wijzigingsgeschiedenis"),
    "budgettracker.transactions": ("expenses", "uitgaven"),
    "budgettracker.summary": ("spending by category", "uitgaven per categorie"),
    "budgettracker.categories": ("expense categories", "uitgavencategorieën"),
    "budgettracker.budgets": ("budgets", "budgetten"),
    "budgettracker.people": ("people on the budget", "personen op het budget"),
    "budgettracker.accounts": ("accounts", "rekeningen"),
    "budgettracker.changes": (
        "BudgetTracker change history",
        "BudgetTracker-wijzigingsgeschiedenis",
    ),
}

# Audit/undo tools — live but not household "what can you do" brochure items.
_OVERVIEW_EXCLUDE_CLUSTERS: frozenset[str] = frozenset(
    {
        "homebase.changes",
        "budgettracker.changes",
    }
)

# Preferred overview order (unknown clusters append alphabetically at the end).
_OVERVIEW_ORDER: tuple[str, ...] = (
    "local.weather",
    "local.calendar",
    "local.media",
    "homebase.shopping_list",
    "homebase.inventory",
    "homebase.recipes",
    "homebase.tasks",
    "homebase.lights",
    "budgettracker.transactions",
    "budgettracker.summary",
    "budgettracker.categories",
    "budgettracker.people",
    "budgettracker.budgets",
    "budgettracker.accounts",
)

# For down-service wording when no live tools remain to reverse-map aliases.
_SERVICE_DOWN_CLUSTERS: dict[str, tuple[str, ...]] = {
    "homebase": (
        "homebase.shopping_list",
        "homebase.inventory",
        "homebase.recipes",
        "homebase.tasks",
        "homebase.lights",
    ),
    "budgettracker": (
        "budgettracker.transactions",
        "budgettracker.categories",
        "budgettracker.budgets",
        "budgettracker.people",
    ),
}

# Phrase aliases → cluster id (explain / down-service match).
_PHRASE_TO_CLUSTER: dict[str, str] = {
    "weather": "local.weather",
    "weer": "local.weather",
    "calendar": "local.calendar",
    "agenda": "local.calendar",
    "schedule": "local.calendar",
    "movies": "local.media",
    "films": "local.media",
    "jellyfin": "local.media",
    "shopping": "homebase.shopping_list",
    "shopping list": "homebase.shopping_list",
    "shopping lists": "homebase.shopping_list",
    "boodschappen": "homebase.shopping_list",
    "boodschappenlijst": "homebase.shopping_list",
    "boodschappenlijsten": "homebase.shopping_list",
    "inventory": "homebase.inventory",
    "voorraad": "homebase.inventory",
    "recipes": "homebase.recipes",
    "recipe": "homebase.recipes",
    "recepten": "homebase.recipes",
    "recept": "homebase.recipes",
    "tasks": "homebase.tasks",
    "chores": "homebase.tasks",
    "taken": "homebase.tasks",
    "huishoudtaken": "homebase.tasks",
    "lights": "homebase.lights",
    "lamps": "homebase.lights",
    "lampen": "homebase.lights",
    "expenses": "budgettracker.transactions",
    "budget": "budgettracker.budgets",
    "budgets": "budgettracker.budgets",
    "uitgaven": "budgettracker.transactions",
    "budgetten": "budgettracker.budgets",
    "categories": "budgettracker.categories",
    "categorieën": "budgettracker.categories",
    "categorien": "budgettracker.categories",
    "summary": "budgettracker.summary",
    "spending by category": "budgettracker.summary",
    "uitgaven per categorie": "budgettracker.summary",
    "change history": "homebase.changes",
    "wijzigingsgeschiedenis": "homebase.changes",
}

_OVERVIEW_RE = re.compile(
    r"^(?:"
    r"what\s+can\s+you\s+do|"
    r"what\s+are\s+you\s+(?:able|capable)\s+(?:to\s+do|of)|"
    r"what\s+(?:tools|capabilities)\s+(?:do\s+you\s+have|are\s+available)|"
    r"list\s+your\s+(?:tools|capabilities)|"
    r"wat\s+kun\s+je(?:\s+(?:allemaal\s+)?doen)?|"
    r"wat\s+kan\s+je(?:\s+(?:allemaal\s+)?doen)?|"
    r"wat\s+ben\s+je\s+in\s+staat|"
    r"welke\s+(?:tools|mogelijkheden)(?:\s+heb\s+je)?|"
    r"vertel\s+(?:me\s+)?wat\s+je\s+(?:kunt|kan)"
    r")[\s?.!]*$",
    re.IGNORECASE,
)

_EXPLAIN_RE = re.compile(
    r"^(?:"
    r"how\s+does\s+(?P<en_does>.+?)\s+work|"
    r"how\s+do\s+(?:the\s+)?(?P<en_do>.+?)\s+work|"
    r"what\s+does\s+(?:the\s+)?(?P<en_what>.+?)\s+do|"
    r"hoe\s+werkt\s+(?:de\s+|het\s+)?(?P<nl_werkt>.+?)|"
    r"hoe\s+werken\s+(?:de\s+)?(?P<nl_werken>.+?)|"
    r"wat\s+doet\s+(?:de\s+|het\s+)?(?P<nl_doet>.+?)"
    r")[\s?.!]*$",
    re.IGNORECASE,
)

HEALTH_PROBE_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class CapabilityEntry:
    """One overview cluster with provenance for tests."""

    cluster_id: str
    service: str | None
    label_en: str
    label_nl: str
    tool_names: tuple[str, ...]


def is_capability_overview(text: str) -> bool:
    """True when the message is a standalone capability overview question."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    return bool(_OVERVIEW_RE.match(normalized))


def is_capability_explain(text: str) -> bool:
    """True when the message asks how a named capability/tool works."""
    return extract_explain_target(text) is not None


def extract_explain_target(text: str) -> str | None:
    """Return the capability phrase from an explain question, else None."""
    normalized = (text or "").strip()
    if not normalized:
        return None
    match = _EXPLAIN_RE.match(normalized)
    if not match:
        return None
    for key in (
        "en_does",
        "en_do",
        "en_what",
        "nl_werkt",
        "nl_werken",
        "nl_doet",
    ):
        value = match.groupdict().get(key)
        if value:
            return value.strip(" \t\"'`")
    return None


def capability_locale(text: str) -> Locale:
    """Infer reply locale from the capability question."""
    normalized = (text or "").strip().lower()
    if re.match(
        r"^(wat\s+|hoe\s+|welke\s+|vertel\s+)",
        normalized,
    ):
        return "nl"
    return "en"


def should_short_circuit_capability(
    text: str,
    *,
    pending_confirmable: bool = False,
    is_morning: Callable[[str], bool] | None = None,
    is_evening: Callable[[str], bool] | None = None,
    requests_write: Callable[[str], bool] | None = None,
    requests_recipe_save: Callable[[str], bool] | None = None,
    requests_light_write: Callable[[str], bool] | None = None,
) -> bool:
    """True when capability discovery should answer without the model."""
    if pending_confirmable:
        return False
    morning = is_morning or (lambda _t: False)
    evening = is_evening or (lambda _t: False)
    write = requests_write or (lambda _t: False)
    recipe = requests_recipe_save or (lambda _t: False)
    lights = requests_light_write or (lambda _t: False)
    if (
        morning(text)
        or evening(text)
        or write(text)
        or recipe(text)
        or lights(text)
    ):
        return False
    return is_capability_overview(text) or is_capability_explain(text)


def cluster_id_for_tool(tool: Tool) -> str | None:
    """Map a registered tool to a household-facing cluster, or None to skip."""
    name = tool.name
    if name in _OVERVIEW_EXCLUDE:
        return None
    if name in _LOCAL_CLUSTER:
        return _LOCAL_CLUSTER[name]
    if tool.service:
        parts = name.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
        return f"{tool.service}.other"
    # Unknown local tool — omit from overview.
    return None


def _labels_for_cluster(cluster_id: str) -> tuple[str, str]:
    if cluster_id in _CLUSTER_LABELS:
        return _CLUSTER_LABELS[cluster_id]
    # Fallback: humanize last segment (same string EN/NL — prefer labeled clusters).
    segment = cluster_id.rsplit(".", 1)[-1].replace("_", " ")
    return (segment, segment)


def _overview_sort_key(cluster_id: str) -> tuple[int, str]:
    try:
        return (0, f"{_OVERVIEW_ORDER.index(cluster_id):03d}")
    except ValueError:
        return (1, cluster_id)


def live_capability_entries(
    tools: dict[str, Tool],
    *,
    unavailable: Iterable[str] = (),
) -> list[CapabilityEntry]:
    """Build overview clusters from live tools only (omit down services)."""
    down = {s.strip() for s in unavailable if s and str(s).strip()}
    grouped: dict[str, list[str]] = defaultdict(list)
    service_by_cluster: dict[str, str | None] = {}

    for name, tool in sorted(tools.items()):
        if tool.service and tool.service in down:
            continue
        cluster = cluster_id_for_tool(tool)
        if cluster is None or cluster in _OVERVIEW_EXCLUDE_CLUSTERS:
            continue
        grouped[cluster].append(name)
        service_by_cluster[cluster] = tool.service

    entries: list[CapabilityEntry] = []
    for cluster_id in sorted(grouped.keys(), key=_overview_sort_key):
        label_en, label_nl = _labels_for_cluster(cluster_id)
        entries.append(
            CapabilityEntry(
                cluster_id=cluster_id,
                service=service_by_cluster.get(cluster_id),
                label_en=label_en,
                label_nl=label_nl,
                tool_names=tuple(grouped[cluster_id]),
            )
        )
    return entries


def resolve_cluster_from_phrase(phrase: str) -> str | None:
    """Map a user phrase to a known cluster id."""
    key = re.sub(r"\s+", " ", (phrase or "").strip().lower())
    key = key.strip("?.!")
    if not key:
        return None
    if key in _PHRASE_TO_CLUSTER:
        return _PHRASE_TO_CLUSTER[key]
    # Tool name exact / prefix.
    if key.startswith(("homebase.", "budgettracker.", "local.")):
        parts = key.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
    # Fuzzy contains: longest alias wins.
    best: str | None = None
    best_len = 0
    for alias, cluster in _PHRASE_TO_CLUSTER.items():
        if alias in key and len(alias) > best_len:
            best = cluster
            best_len = len(alias)
    return best


def service_for_cluster(cluster_id: str) -> str | None:
    if cluster_id.startswith("local."):
        return None
    if "." in cluster_id:
        return cluster_id.split(".", 1)[0]
    return None


def probe_mcp_health(
    service_id: str,
    svc: McpServiceSettings,
    *,
    timeout_s: float = HEALTH_PROBE_TIMEOUT_S,
    client: httpx.Client | None = None,
) -> bool:
    """True when GET /health returns 200 and JSON status == ok."""
    url = f"http://{svc.host}:{svc.port}/health"
    headers: dict[str, str] = {}
    from brain.config import mcp_request_host_header

    host_header = mcp_request_host_header(svc)
    if host_header:
        headers["Host"] = host_header
    owns = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(timeout_s))
    try:
        resp = http.get(url, headers=headers)
        if resp.status_code != 200:
            return False
        try:
            payload = resp.json()
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        return str(payload.get("status", "")).lower() == "ok"
    except httpx.HTTPError:
        return False
    finally:
        if owns:
            http.close()


def probe_unavailable_services(
    settings: Settings,
    *,
    known_unavailable: Iterable[str] = (),
    timeout_s: float = HEALTH_PROBE_TIMEOUT_S,
    health_probe: Callable[[str, McpServiceSettings], bool] | None = None,
) -> list[str]:
    """Return configured MCP service ids that are unavailable right now."""
    down: list[str] = []
    known = {s for s in known_unavailable if s}
    probe = health_probe or (
        lambda sid, svc: probe_mcp_health(sid, svc, timeout_s=timeout_s)
    )

    for service_id, svc in settings.services.items():
        if not svc.enabled:
            continue
        if service_id in known:
            # Still re-probe: service may have recovered; if known from missing
            # token, keep unavailable without probe when token empty.
            if not (svc.token or "").strip():
                down.append(service_id)
                continue
        if not (svc.token or "").strip():
            down.append(service_id)
            continue
        try:
            ok = probe(service_id, svc)
        except Exception:  # noqa: BLE001
            logger.warning("capability health probe failed for %s", service_id)
            ok = False
        if not ok:
            down.append(service_id)
    return down


def build_capability_overview(
    *,
    locale: Locale,
    tools: dict[str, Tool],
    unavailable: Iterable[str] = (),
    configured_services: Iterable[str] | None = None,
) -> str:
    """Human reply listing live capability clusters only."""
    entries = live_capability_entries(tools, unavailable=unavailable)
    down = [s for s in unavailable if s]
    configured = list(configured_services) if configured_services is not None else []

    if locale == "nl":
        lines = ["Dit kan ik nu:"]
        if entries:
            for entry in entries:
                lines.append(f"- {entry.label_nl}")
        else:
            lines.append("- (geen huishoudtools verbonden)")
        down_lines = _format_unavailable_section(
            locale="nl",
            unavailable=down,
            configured=configured,
        )
        if down_lines:
            lines.append("")
            lines.extend(down_lines)
        return "\n".join(lines)

    lines = ["Here is what I can do right now:"]
    if entries:
        for entry in entries:
            lines.append(f"- {entry.label_en}")
    else:
        lines.append("- (no household tools connected)")
    down_lines = _format_unavailable_section(
        locale="en",
        unavailable=down,
        configured=configured,
    )
    if down_lines:
        lines.append("")
        lines.extend(down_lines)
    return "\n".join(lines)


def _format_unavailable_section(
    *,
    locale: Locale,
    unavailable: list[str],
    configured: list[str],
) -> list[str]:
    if not unavailable:
        return []
    configured_set = {s for s in configured if s}
    ids = [
        s
        for s in unavailable
        if not configured_set or s in configured_set
    ]
    if not ids:
        return []
    if locale == "nl":
        lines = ["Niet beschikbaar:"]
        for sid in ids:
            name = display_service_name(sid)
            clusters = _SERVICE_DOWN_CLUSTERS.get(sid, ())
            if clusters:
                labels = ", ".join(_labels_for_cluster(c)[1] for c in clusters)
                lines.append(f"- {name} is niet bereikbaar ({labels})")
            else:
                lines.append(f"- {name} is niet bereikbaar")
        return lines

    lines = ["Not available:"]
    for sid in ids:
        name = display_service_name(sid)
        clusters = _SERVICE_DOWN_CLUSTERS.get(sid, ())
        if clusters:
            labels = ", ".join(_labels_for_cluster(c)[0] for c in clusters)
            lines.append(f"- {name} is unreachable ({labels})")
        else:
            lines.append(f"- {name} is unreachable")
    return lines


def _first_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    match = re.match(r"(.+?[.!?])(?:\s|$)", cleaned)
    if match:
        return match.group(1).strip()
    if len(cleaned) > 180:
        return cleaned[:177].rstrip() + "…"
    return cleaned


def _required_args_summary(tool: Tool) -> list[str]:
    params = tool.parameters or {}
    required = params.get("required") or []
    props = params.get("properties") or {}
    out: list[str] = []
    for key in required:
        if not isinstance(key, str):
            continue
        prop = props.get(key) if isinstance(props, dict) else None
        if isinstance(prop, dict) and prop.get("description"):
            out.append(f"{key} ({prop['description']})")
        else:
            out.append(key)
    return out


def build_capability_explain(
    *,
    locale: Locale,
    tools: dict[str, Tool],
    phrase: str,
    unavailable: Iterable[str] = (),
) -> str:
    """Explain a named live capability without dumping raw JSON schemas."""
    down = {s for s in unavailable if s}
    cluster = resolve_cluster_from_phrase(phrase)
    needle = re.sub(r"\s+", " ", (phrase or "").strip().lower())

    # Prefer exact tool name match.
    matched_tool: Tool | None = None
    for name, tool in tools.items():
        if name.lower() == needle or name.lower().endswith("." + needle):
            if tool.service and tool.service in down:
                continue
            matched_tool = tool
            break

    if matched_tool is None and cluster:
        service = service_for_cluster(cluster)
        if service and service in down:
            return _unreachable_explain(locale, service, cluster)
        for _name, tool in sorted(tools.items()):
            if tool.service and tool.service in down:
                continue
            cid = cluster_id_for_tool(tool)
            if cid == cluster:
                matched_tool = tool
                break
        if matched_tool is None and service and service in down:
            return _unreachable_explain(locale, service, cluster)

    if matched_tool is None:
        # Phrase maps to a down service cluster with no live tools.
        if cluster:
            service = service_for_cluster(cluster)
            if service and service in down:
                return _unreachable_explain(locale, service, cluster)
        return _unknown_explain(locale, phrase)

    return _format_tool_explain(locale, matched_tool, cluster)


def _unreachable_explain(locale: Locale, service_id: str, cluster_id: str) -> str:
    name = display_service_name(service_id)
    label_en, label_nl = _labels_for_cluster(cluster_id)
    if locale == "nl":
        return (
            f"Ik kan {label_nl} nu niet gebruiken — {name} is niet bereikbaar."
        )
    return f"I can't use {label_en} right now — {name} is unreachable."


def _unknown_explain(locale: Locale, phrase: str) -> str:
    cleaned = (phrase or "").strip() or "that"
    if locale == "nl":
        return (
            f"Ik heb geen live tool voor “{cleaned}”. "
            "Vraag “wat kun je?” voor wat er nu wel beschikbaar is."
        )
    return (
        f"I don't have a live tool for “{cleaned}”. "
        'Ask “what can you do?” for what is available now.'
    )


def _format_tool_explain(
    locale: Locale,
    tool: Tool,
    cluster: str | None,
) -> str:
    label_en, label_nl = _labels_for_cluster(
        cluster or cluster_id_for_tool(tool) or tool.name
    )
    desc = _first_sentence(tool.description or "")
    args = _required_args_summary(tool)

    if locale == "nl":
        lines = [f"**{label_nl}** (`{tool.name}`)."]
        # Deterministic NL: cluster label + schema facts, not free translation.
        if desc:
            lines.append(f"Schema: {desc}")
        if args:
            lines.append("Vereiste argumenten: " + "; ".join(args) + ".")
        else:
            lines.append("Geen verplichte argumenten.")
        return " ".join(lines)

    lines = [f"**{label_en}** (`{tool.name}`)."]
    if desc:
        lines.append(desc)
    if args:
        lines.append("Required arguments: " + "; ".join(args) + ".")
    else:
        lines.append("No required arguments.")
    return " ".join(lines)


def build_capability_reply(
    user_message: str,
    *,
    tools: dict[str, Tool],
    unavailable: Iterable[str] = (),
    configured_services: Iterable[str] | None = None,
) -> str | None:
    """Return a capability reply when the message matches, else None."""
    if is_capability_overview(user_message):
        return build_capability_overview(
            locale=capability_locale(user_message),
            tools=tools,
            unavailable=unavailable,
            configured_services=configured_services,
        )
    target = extract_explain_target(user_message)
    if target is not None:
        return build_capability_explain(
            locale=capability_locale(user_message),
            tools=tools,
            phrase=target,
            unavailable=unavailable,
        )
    return None


def all_overview_tool_names(entries: Iterable[CapabilityEntry]) -> set[str]:
    names: set[str] = set()
    for entry in entries:
        names.update(entry.tool_names)
    return names
