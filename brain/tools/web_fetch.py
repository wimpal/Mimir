"""Local web.fetch — SSRF-safe GET for recipe URL import (T-021)."""

from __future__ import annotations

import html as html_lib
import http.client
import ipaddress
import json
import re
import socket
import ssl
from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from brain.config import Settings
from brain.tools import Tool

DEFAULT_TIMEOUT_S = 15.0
# Modern recipe SPAs (e.g. Picnic Next.js) can exceed 512 KiB HTML.
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
# Prefer a tighter snippet for the model (recipe pages are mostly chrome).
MAX_MODEL_CHARS = 12_000

_RECIPE_OBJECT_START_RE = re.compile(
    r'\{\s*"@context"\s*:\s*"https?://schema\.org/?"\s*,\s*"@type"\s*:\s*"Recipe"',
)
_RECIPE_ESCAPED_IN_STRING_RE = re.compile(
    r',"\{\\"@context\\":\\"https?://schema\.org/?\\",\\"@type\\":\\"Recipe\\"',
)

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

_LD_JSON_TYPE_RE = re.compile(
    r"^application/ld\+json\b",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_JSON_COMMENT_RE = re.compile(r"^\s*/\*.*?\*/\s*", re.DOTALL)


class _HTMLStripper(HTMLParser):
    """Collect visible text; drop script/style; capture application/ld+json."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._ld_json_depth = 0
        self._ld_json_buf: list[str] = []
        self.ld_json_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "script":
            type_val = ""
            for key, value in attrs:
                if key.lower() == "type" and value:
                    type_val = value.strip()
                    break
            if _LD_JSON_TYPE_RE.match(type_val):
                self._ld_json_depth += 1
                return
            self._skip_depth += 1
            return
        if lower in {"style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "script" and self._ld_json_depth:
            self._ld_json_depth -= 1
            if self._ld_json_depth == 0 and self._ld_json_buf:
                self.ld_json_blocks.append("".join(self._ld_json_buf))
                self._ld_json_buf = []
            return
        if lower in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ld_json_depth:
            self._ld_json_buf.append(data)
            return
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


def _types_of(node: Any) -> list[str]:
    raw = node.get("@type") if isinstance(node, dict) else None
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.rsplit("/", 1)[-1]]
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item.rsplit("/", 1)[-1])
        return out
    return []


def _has_type(node: Any, *names: str) -> bool:
    types = {t.lower() for t in _types_of(node)}
    return any(name.lower() in types for name in names)


def _iter_jsonld_nodes(payload: Any) -> Iterator[Any]:
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_jsonld_nodes(item)
        return
    if not isinstance(payload, dict):
        return
    yield payload
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _iter_jsonld_nodes(item)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return ""
    text = html_lib.unescape(text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _walk_instruction_node(node: Any, out: list[str]) -> None:
    if node is None:
        return
    if isinstance(node, str):
        cleaned = _clean_text(node)
        if cleaned:
            out.append(cleaned)
        return
    if isinstance(node, list):
        for item in node:
            _walk_instruction_node(item, out)
        return
    if not isinstance(node, dict):
        return
    if _has_type(node, "HowToStep", "HowToDirection", "HowToTip"):
        text = _clean_text(node.get("text")) or _clean_text(node.get("name"))
        if text:
            out.append(text)
        return
    if _has_type(node, "HowToSection", "ItemList", "HowTo"):
        elements = node.get("itemListElement")
        if elements is not None:
            _walk_instruction_node(elements, out)
        step = node.get("step")
        if step is not None:
            _walk_instruction_node(step, out)
        return
    if _has_type(node, "ListItem") or "item" in node:
        item = node.get("item")
        if item is not None:
            _walk_instruction_node(item, out)
            return
        text = _clean_text(node.get("text")) or _clean_text(node.get("name"))
        if text:
            out.append(text)
        return
    # Untyped dict that still looks like a step.
    text = _clean_text(node.get("text")) or _clean_text(node.get("name"))
    if text:
        out.append(text)
        return
    elements = node.get("itemListElement")
    if elements is not None:
        _walk_instruction_node(elements, out)


def _extract_ingredients(node: dict[str, Any]) -> list[str]:
    raw = node.get("recipeIngredient")
    if raw is None:
        return []
    if isinstance(raw, str):
        cleaned = _clean_text(raw)
        return [cleaned] if cleaned else []
    if not isinstance(raw, list):
        cleaned = _clean_text(raw)
        return [cleaned] if cleaned else []
    out: list[str] = []
    for item in raw:
        cleaned = _clean_text(item)
        if cleaned:
            out.append(cleaned)
    return out


def _extract_instructions(node: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _walk_instruction_node(node.get("recipeInstructions"), out)
    return out


def _parse_ld_json_blob(raw: str) -> Any | None:
    text = raw.strip().lstrip("\ufeff")
    text = _JSON_COMMENT_RE.sub("", text, count=1).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _brace_match_json_object(text: str, start: int) -> Any | None:
    """Parse a JSON object starting at ``start``, respecting string literals."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _load_json_string_literal(text: str, start: int) -> Any | None:
    """Parse a JSON string literal at ``start`` (opening quote)."""
    if start >= len(text) or text[start] != '"':
        return None
    escape = False
    for i in range(start + 1, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            try:
                return json.loads(text[start : i + 1])
            except json.JSONDecodeError:
                return None
    return None


def _iter_embedded_recipe_payloads(text: str) -> Iterator[Any]:
    """Yield Recipe JSON payloads from classic or Next.js-embedded HTML."""
    seen: set[int] = set()
    for match in _RECIPE_OBJECT_START_RE.finditer(text):
        payload = _brace_match_json_object(text, match.start())
        if isinstance(payload, dict) and id(payload) not in seen:
            seen.add(id(payload))
            yield payload
    for match in _RECIPE_ESCAPED_IN_STRING_RE.finditer(text):
        # match starts at ,"{\"@context\"... — JSON string begins at the quote.
        quote_at = match.start() + 1
        loaded = _load_json_string_literal(text, quote_at)
        if isinstance(loaded, str):
            try:
                payload = json.loads(loaded)
            except json.JSONDecodeError:
                continue
        elif isinstance(loaded, dict):
            payload = loaded
        else:
            continue
        if isinstance(payload, dict) and _has_type(payload, "Recipe"):
            if id(payload) not in seen:
                seen.add(id(payload))
                yield payload


def _select_recipe_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    with_both: dict[str, Any] | None = None
    with_instructions: dict[str, Any] | None = None
    with_ingredients: dict[str, Any] | None = None
    for node in nodes:
        if not _has_type(node, "Recipe"):
            continue
        instructions = _extract_instructions(node)
        ingredients = _extract_ingredients(node)
        if instructions and ingredients and with_both is None:
            with_both = node
        if instructions and with_instructions is None:
            with_instructions = node
        if ingredients and with_ingredients is None:
            with_ingredients = node
    return with_both or with_instructions or with_ingredients


def recipe_structured_from_html(html: str, ld_json_blocks: list[str] | None = None) -> str:
    """Build Ingredients/Directions plain text from Recipe JSON-LD in HTML."""
    recipe_nodes: list[dict[str, Any]] = []
    for block in ld_json_blocks or []:
        payload = _parse_ld_json_blob(block)
        if payload is None:
            continue
        for node in _iter_jsonld_nodes(payload):
            if isinstance(node, dict) and _has_type(node, "Recipe"):
                recipe_nodes.append(node)
    for node in _iter_embedded_recipe_payloads(html):
        if isinstance(node, dict) and _has_type(node, "Recipe"):
            recipe_nodes.append(node)
        else:
            for child in _iter_jsonld_nodes(node):
                if isinstance(child, dict) and _has_type(child, "Recipe"):
                    recipe_nodes.append(child)
    recipe = _select_recipe_node(recipe_nodes)
    if recipe is None:
        return ""
    ingredients = _extract_ingredients(recipe)
    instructions = _extract_instructions(recipe)
    if not ingredients and not instructions:
        return ""
    parts: list[str] = []
    if ingredients:
        parts.append("Ingredients")
        parts.extend(ingredients)
    if instructions:
        if parts:
            parts.append("")
        parts.append("Directions")
        parts.extend(instructions)
    return "\n".join(parts).strip()


def recipe_structured_from_ld_json(blocks: list[str]) -> str:
    """Backward-compatible helper: structured text from ld+json script bodies."""
    return recipe_structured_from_html("", ld_json_blocks=blocks)

def _truncate_for_model(text: str, *, structured: str = "") -> str:
    """Prefer structured Ingredients/Directions over chrome when truncating."""
    if structured:
        structured = structured.strip()
        chrome = text.strip()
        if chrome and structured:
            combined = f"{structured}\n\n{chrome}"
        else:
            combined = structured or chrome
        if len(combined) <= MAX_MODEL_CHARS:
            return combined
        # Keep structured first; drop chrome before cutting Directions.
        budget = MAX_MODEL_CHARS
        if len(structured) >= budget:
            # Prefer Directions section if structured alone is huge.
            lower = structured.lower()
            dir_anchor = lower.find("\ndirections\n")
            if dir_anchor < 0:
                dir_anchor = lower.find("directions\n")
            if dir_anchor >= 0:
                directions = structured[dir_anchor:].lstrip("\n")
                if directions.lower().startswith("directions"):
                    keep = directions
                else:
                    keep = "Directions\n" + directions
                if len(keep) > budget:
                    return keep[: budget - 20] + "\n…[truncated]"
                # Fill remaining with leading Ingredients if any.
                head = structured[:dir_anchor].rstrip()
                room = max(0, budget - len(keep) - 2)
                if head and room > 40:
                    return head[:room] + "\n\n" + keep
                return keep[:budget]
            return structured[: budget - 20] + "\n…[truncated]"
        room = max(0, budget - len(structured) - 2)
        if room == 0:
            return structured[:budget]
        if len(chrome) > room:
            # room may be small; keep slice non-negative and within budget.
            chrome_budget = max(0, room - 20)
            truncated_chrome = chrome[:chrome_budget] + "\n…[truncated]"
        else:
            truncated_chrome = chrome
        if truncated_chrome.strip():
            out = f"{structured}\n\n{truncated_chrome}"
            return out if len(out) <= budget else out[: budget - 20] + "\n…[truncated]"
        return structured[:budget]

    if len(text) <= MAX_MODEL_CHARS:
        return text
    # Prefer the slice that contains Ingredients / Directions when truncating.
    lower = text.lower()
    anchor = -1
    for marker in (
        "ingredients",
        "ingrediënten",
        "directions",
        "instructions",
        "method",
        "bereiding",
    ):
        anchor = lower.find(marker)
        if anchor >= 0:
            break
    if anchor > 200:
        start = max(0, anchor - 200)
        text = text[start : start + MAX_MODEL_CHARS]
        if start > 0:
            text = "…\n" + text
        if len(text) >= MAX_MODEL_CHARS:
            text = text[: MAX_MODEL_CHARS - 20] + "\n…[truncated]"
        return text
    return text[: MAX_MODEL_CHARS - 20] + "\n…[truncated]"


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ip.ipv4_mapped)
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return True
    if ip.is_reserved or ip.is_unspecified:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def validate_fetch_url(url: str) -> str | None:
    """Return an error string if ``url`` must not be fetched, else None."""
    text = (url or "").strip()
    if not text:
        return "error: url is required"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return "error: only http and https URLs are allowed"
    if not parsed.hostname:
        return "error: url host is required"
    if parsed.username or parsed.password:
        return "error: urls with credentials are not allowed"
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and _is_blocked_ip(ip):
        return "error: url targets a blocked address"
    return None


def resolve_public_host(hostname: str, *, timeout_s: float = 5.0) -> list[str]:
    """Resolve hostname; raise ValueError if any address is blocked or resolution fails."""
    previous = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout_s)
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host: {hostname}") from exc
    finally:
        socket.setdefaulttimeout(previous)
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ValueError(f"host resolves to blocked address: {addr}")
        addresses.append(addr)
    if not addresses:
        raise ValueError(f"cannot resolve host: {hostname}")
    return addresses


def simplify_body(raw: bytes, *, content_type: str | None) -> str:
    """Decode bytes to a plain-text / simplified HTML snippet for the model."""
    ctype = (content_type or "").lower()
    if "octet-stream" in ctype or "image/" in ctype or "audio/" in ctype or "video/" in ctype:
        return "error: binary content is not supported"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    structured = ""
    if "html" in ctype or re.search(r"<\s*html\b", text, re.IGNORECASE):
        raw_html = text
        stripper = _HTMLStripper()
        try:
            stripper.feed(text)
            stripper.close()
            structured = recipe_structured_from_html(
                raw_html, ld_json_blocks=stripper.ld_json_blocks
            )
            text = stripper.text()
        except Exception:  # noqa: BLE001 — fall back to raw text
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            structured = recipe_structured_from_html(raw_html)
    text = text.strip()
    # Drop common CMP / cookie-wall chrome that crowds out recipe body.
    text = re.sub(
        r"(?is)(manage consent|manage options|manage vendors|accept\s*deny|"
        r"view preferences|save preferences|statistics\s*statistics|"
        r"marketing\s*marketing|\{title\}).*",
        "",
        text,
    )
    text = re.sub(r"(?m)^(Mark as complete|Share:|fb|ig|tt|p)\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = _truncate_for_model(text, structured=structured)
    return text or "(empty page)"


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TCP to a validated IP; TLS SNI + cert verify use the original hostname."""

    def __init__(self, hostname: str, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(hostname, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        context = self._context if self._context is not None else ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """TCP to a validated IP; Host header still uses the original hostname."""

    def __init__(self, hostname: str, pinned_ip: str, **kwargs: Any) -> None:
        super().__init__(hostname, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
            source_address=self.source_address,
        )


def _request_path(parsed: Any) -> str:
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _http_get_pinned(
    url: str,
    pinned_ip: str,
    *,
    timeout_s: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes] | str:
    """GET keeping hostname for TLS; dial ``pinned_ip`` only. Return status/headers/body or error."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        return "error: url host is required"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    path = _request_path(parsed)
    try:
        if parsed.scheme == "https":
            conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
                hostname,
                pinned_ip,
                port=port,
                timeout=timeout_s,
                context=ssl.create_default_context(),
            )
        else:
            conn = _PinnedHTTPConnection(
                hostname,
                pinned_ip,
                port=port,
                timeout=timeout_s,
            )
        try:
            conn.request("GET", path, headers={"Host": hostname, "User-Agent": "Mimir/1"})
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    return f"error: response exceeds {max_bytes} byte limit"
                chunks.append(chunk)
            return resp.status, headers, b"".join(chunks)
        finally:
            conn.close()
    except TimeoutError:
        return "error: request timed out"
    except ssl.SSLError as exc:
        return f"error: TLS failed: {exc}"
    except OSError as exc:
        return f"error: request failed: {exc}"


def _pin_addresses(
    host: str,
    *,
    resolver: Callable[[str], list[str]],
) -> str | list[str]:
    """Return pinned IP list or an error string."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        addrs = resolver(host)
    except ValueError as exc:
        return f"error: {exc}"
    if not addrs:
        return f"error: cannot resolve host: {host}"
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return f"error: cannot resolve host: {host}"
        if _is_blocked_ip(ip):
            return f"error: host resolves to blocked address: {addr}"
    return addrs


def fetch_url(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = MAX_BODY_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    client_factory: Callable[[], httpx.Client] | None = None,
    resolve_host: Callable[[str], list[str]] | None = None,
) -> str:
    """GET ``url`` with SSRF guards; return text snippet or ``error: …``.

    Production path dials a pre-validated IP while keeping the hostname in the
    TLS SNI / certificate check (avoids DNS rebinding without breaking HTTPS).
    ``client_factory`` is for tests (MockTransport) and uses the hostname URL.
    """
    err = validate_fetch_url(url)
    if err is not None:
        return err

    current = url.strip()
    resolver = resolve_host or (
        lambda host: resolve_public_host(host, timeout_s=min(5.0, timeout_s))
    )

    try:
        for _ in range(max_redirects + 1):
            err = validate_fetch_url(current)
            if err is not None:
                return err
            host = urlparse(current).hostname
            assert host is not None
            pinned = _pin_addresses(host, resolver=resolver)
            if isinstance(pinned, str):
                return pinned
            pinned_ip = pinned[0]

            if client_factory is not None:
                # Test / inject path — transport is caller-controlled.
                with client_factory() as client:
                    try:
                        with client.stream("GET", current) as resp:
                            if resp.status_code in {301, 302, 303, 307, 308}:
                                location = resp.headers.get("location")
                                if not location:
                                    return "error: redirect without location"
                                current = urljoin(current, location)
                                continue
                            if resp.status_code >= 400:
                                return f"error: HTTP {resp.status_code}"
                            ctype = resp.headers.get("content-type")
                            chunks = []
                            total = 0
                            for chunk in resp.iter_bytes():
                                total += len(chunk)
                                if total > max_bytes:
                                    return (
                                        f"error: response exceeds {max_bytes} byte limit"
                                    )
                                chunks.append(chunk)
                            return simplify_body(
                                b"".join(chunks), content_type=ctype
                            )
                    except httpx.TimeoutException:
                        return "error: request timed out"
                    except httpx.HTTPError as exc:
                        return f"error: request failed: {exc}"
                continue

            result = _http_get_pinned(
                current,
                pinned_ip,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
            )
            if isinstance(result, str):
                return result
            status, headers, body = result
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    return "error: redirect without location"
                current = urljoin(current, location)
                continue
            if status >= 400:
                return f"error: HTTP {status}"
            return simplify_body(body, content_type=headers.get("content-type"))
        return "error: too many redirects"
    except Exception as exc:  # noqa: BLE001
        return f"error: fetch failed: {exc}"


def web_fetch_tools(
    settings: Settings,
    *,
    fetch_override: Callable[[str], str] | None = None,
) -> dict[str, Tool]:
    """Register local ``web.fetch`` (unused settings reserved for future caps)."""
    _ = settings

    def execute(*, url: str) -> str:
        if fetch_override is not None:
            return fetch_override(url)
        return fetch_url(url)

    tool = Tool(
        name="web.fetch",
        description=(
            "Fetch a public http(s) URL and return plain text / simplified HTML for "
            "extraction. Use only for user-supplied recipe URLs before "
            "homebase.recipes.add. GET only; blocks private/LAN/metadata addresses. "
            "Do not use for Homebase, BudgetTracker, or local services."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Public http or https URL supplied by the user",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        execute=execute,
        timeout_s=DEFAULT_TIMEOUT_S,
    )
    return {tool.name: tool}
