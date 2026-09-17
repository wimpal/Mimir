"""wikipedia_lookup — MediaWiki API summary (T-056)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import quote

import httpx

from brain.config import Settings
from brain.tools import Tool

Language = Literal["nl", "en"]
DEFAULT_LANGUAGE: Language = "nl"
MAX_EXTRACT_CHARS = 1200
USER_AGENT = (
    "MimirBot/1.0 (https://github.com/local/mimir; mimir-local@localhost) "
    "household-assistant wikipedia_lookup"
)


def _normalize_language(raw: str | None) -> Language | str:
    """Return nl/en, or an error string when an explicit invalid value is given."""
    if raw is None:
        return DEFAULT_LANGUAGE
    text = str(raw).strip().lower()
    if not text:
        return DEFAULT_LANGUAGE
    if text in {"nl", "en"}:
        return text  # type: ignore[return-value]
    if text.startswith("en"):
        return "en"
    if text.startswith("nl"):
        return "nl"
    return f"error: wikipedia unavailable (invalid language {raw!r})"


def _api_url(language: Language) -> str:
    return f"https://{language}.wikipedia.org/w/api.php"


def _page_url(language: Language, title: str) -> str:
    return f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


def normalize_wikipedia_payload(
    raw: dict[str, Any],
    *,
    language: Language,
    query: str,
) -> dict[str, Any] | str:
    """Return success dict or an error: string."""
    if raw.get("error") is not None:
        err = raw["error"]
        if isinstance(err, dict):
            code = str(err.get("code") or "api").strip() or "api"
            return f"error: wikipedia unavailable (api {code})"
        return "error: wikipedia unavailable (api error)"
    pages = (raw.get("query") or {}).get("pages")
    if not isinstance(pages, dict) or not pages:
        return f"error: wikipedia not found ({query!r})"
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return f"error: wikipedia not found ({query!r})"
    if page.get("missing") is not None or page.get("invalid") is not None:
        return f"error: wikipedia not found ({query!r})"
    title = str(page.get("title") or "").strip()
    extract = str(page.get("extract") or "").strip()
    if not title or not extract:
        return f"error: wikipedia not found ({query!r})"
    if len(extract) > MAX_EXTRACT_CHARS:
        extract = extract[: MAX_EXTRACT_CHARS - 1].rstrip() + "…"
    fullurl = str(page.get("fullurl") or "").strip()
    url = fullurl or _page_url(language, title)
    return {
        "title": title,
        "extract": extract,
        "url": url,
        "language": language,
    }


def _execute_wikipedia_lookup(
    *,
    query: str,
    language: str | None,
    timeout_s: float,
    http_client: httpx.Client | None = None,
) -> str:
    q = (query or "").strip()
    if not q:
        return "error: wikipedia unavailable (empty query)"
    lang_or_err = _normalize_language(language)
    if isinstance(lang_or_err, str) and lang_or_err.startswith("error:"):
        return lang_or_err
    lang: Language = lang_or_err  # type: ignore[assignment]
    params = {
        "action": "query",
        "format": "json",
        "prop": "extracts|info",
        "exintro": "1",
        "explaintext": "1",
        "exchars": str(MAX_EXTRACT_CHARS),
        "inprop": "url",
        "redirects": "1",
        "titles": q,
    }
    owns_client = http_client is None
    client = http_client or httpx.Client(
        timeout=httpx.Timeout(timeout_s),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )
    try:
        response = client.get(
            _api_url(lang),
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, dict):
            return "error: wikipedia unavailable (bad response)"
        result = normalize_wikipedia_payload(raw, language=lang, query=q)
        if isinstance(result, str):
            # Title miss: try search → first hit (common for fuzzy / NL casing).
            if result.startswith("error: wikipedia not found"):
                found = _search_then_extract(
                    client,
                    language=lang,
                    query=q,
                    timeout_s=timeout_s,
                )
                if found is not None:
                    return found
            return result
        return json.dumps(result, separators=(",", ":"))
    except httpx.TimeoutException:
        return f"error: wikipedia unavailable (timed out after {timeout_s}s)"
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            return "error: wikipedia unavailable (blocked by Wikimedia robot policy)"
        return f"error: wikipedia unavailable (HTTP {status})"
    except httpx.HTTPError as exc:
        return f"error: wikipedia unavailable ({exc.__class__.__name__})"
    except (ValueError, TypeError, KeyError) as exc:
        return f"error: wikipedia unavailable ({exc})"
    finally:
        if owns_client:
            client.close()


def _search_then_extract(
    client: httpx.Client,
    *,
    language: Language,
    query: str,
    timeout_s: float,
) -> str | None:
    """Resolve a missing titles= hit via list=search, then re-fetch extracts."""
    _ = timeout_s
    try:
        search = client.get(
            _api_url(language),
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": query,
                "srlimit": "1",
            },
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        search.raise_for_status()
        raw = search.json()
        hits = ((raw.get("query") or {}).get("search") or []) if isinstance(raw, dict) else []
        if not hits or not isinstance(hits[0], dict):
            return None
        title = str(hits[0].get("title") or "").strip()
        if not title:
            return None
        page = client.get(
            _api_url(language),
            params={
                "action": "query",
                "format": "json",
                "prop": "extracts|info",
                "exintro": "1",
                "explaintext": "1",
                "exchars": str(MAX_EXTRACT_CHARS),
                "inprop": "url",
                "redirects": "1",
                "titles": title,
            },
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        page.raise_for_status()
        page_raw = page.json()
        if not isinstance(page_raw, dict):
            return None
        result = normalize_wikipedia_payload(page_raw, language=language, query=query)
        if isinstance(result, str):
            return None
        return json.dumps(result, separators=(",", ":"))
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return None


def make_wikipedia_lookup_tool(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
) -> Tool:
    timeout_s = settings.timeouts.tool_s

    def execute(*, query: str, language: str | None = None) -> str:
        if fetch_override is not None:
            return fetch_override()
        return _execute_wikipedia_lookup(
            query=query,
            language=language,
            timeout_s=timeout_s,
            http_client=http_client,
        )

    return Tool(
        name="wikipedia_lookup",
        description=(
            "Look up a topic on Wikipedia (MediaWiki API). "
            "Use for 'Wikipedia: …' / 'volgens Wikipedia' questions. "
            "Pass language nl or en matching the user's message (default nl). "
            "Cite title and url in the reply. Not general web search. "
            "Timeout/offline returns an error string."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Topic or page title to look up.",
                },
                "language": {
                    "type": "string",
                    "enum": ["nl", "en"],
                    "description": "Wikipedia language edition; match the user (default nl).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        execute=execute,
    )


def wikipedia_tools(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    fetch_override: Callable[[], str] | None = None,
) -> dict[str, Tool]:
    tool = make_wikipedia_lookup_tool(
        settings,
        http_client=http_client,
        fetch_override=fetch_override,
    )
    return {tool.name: tool}
