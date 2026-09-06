"""Local web.fetch — SSRF-safe GET for recipe URL import (T-021)."""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from brain.config import Settings
from brain.tools import Tool

DEFAULT_TIMEOUT_S = 15.0
MAX_BODY_BYTES = 512 * 1024
MAX_REDIRECTS = 5
# Prefer a tighter snippet for the model (recipe pages are mostly chrome).
MAX_MODEL_CHARS = 12_000

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


class _HTMLStripper(HTMLParser):
    """Collect visible text; drop script/style content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return "\n".join(self._chunks)


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
    if "html" in ctype or re.search(r"<\s*html\b", text, re.IGNORECASE):
        stripper = _HTMLStripper()
        try:
            stripper.feed(text)
            stripper.close()
            text = stripper.text()
        except Exception:  # noqa: BLE001 — fall back to raw text
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
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
    if len(text) > MAX_MODEL_CHARS:
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
        else:
            text = text[: MAX_MODEL_CHARS - 20] + "\n…[truncated]"
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
