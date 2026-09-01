"""HTTP Auth token + Host-only checks for the brain (Phase 7)."""

from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from brain.config import Settings

logger = logging.getLogger("mimir.auth")

# Starlette TestClient reports this as the client host.
_TESTCLIENT_HOSTS = frozenset({"testclient", "localhost", "127.0.0.1", "::1"})

_AUTH_FAIL_LIMIT = 10
_AUTH_FAIL_WINDOW_S = 60.0


class AuthFailureTracker:
    """Per-IP sliding-window counter for failed bearer checks (T-015 / M7)."""

    def __init__(self, *, limit: int = _AUTH_FAIL_LIMIT, window_s: float = _AUTH_FAIL_WINDOW_S) -> None:
        self._limit = limit
        self._window_s = window_s
        self._failures: dict[str, list[float]] = defaultdict(list)

    def _prune(self, ip: str, now: float) -> None:
        cutoff = now - self._window_s
        self._failures[ip] = [t for t in self._failures[ip] if t > cutoff]
        if not self._failures[ip]:
            del self._failures[ip]

    def is_blocked(self, ip: str, *, now: float | None = None) -> bool:
        ts = time.monotonic() if now is None else now
        self._prune(ip, ts)
        return len(self._failures.get(ip, [])) >= self._limit

    def record_failure(self, ip: str, *, now: float | None = None) -> None:
        ts = time.monotonic() if now is None else now
        self._prune(ip, ts)
        self._failures[ip].append(ts)

    def reset(self) -> None:
        self._failures.clear()

    def clear_ip(self, ip: str) -> None:
        self._failures.pop(ip, None)


_auth_failures = AuthFailureTracker()


def reset_auth_failure_tracker_for_tests() -> None:
    """Clear in-memory auth failure state between tests."""
    _auth_failures.reset()


def is_loopback_client(request: Request) -> bool:
    """True when the request appears to come from the host machine."""
    client = request.client
    if client is None or not client.host:
        return True
    return client.host.strip().lower() in _TESTCLIENT_HOSTS


def client_ip(request: Request) -> str:
    """Best-effort client IP for auth logging and rate limiting."""
    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host.strip()


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def bearer_matches(request: Request, expected: str | None) -> bool:
    if not expected:
        return False
    got = _bearer_token(request)
    if got is None:
        return False
    return hmac.compare_digest(got, expected)


def _needs_host_only(path: str) -> bool:
    return path.startswith("/debug/") or path == "/v1/jellyfin/sync"


def _needs_bearer(path: str) -> bool:
    return path.startswith("/v1/") or path.startswith("/debug/")


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce Auth token and Host-only rules from settings on app.state."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Response]
    ) -> Response:
        path = request.url.path
        if path == "/health":
            return await call_next(request)

        settings: Settings | None = getattr(request.app.state, "settings", None)
        if settings is None:
            return await call_next(request)

        if _needs_host_only(path) and not is_loopback_client(request):
            return JSONResponse(status_code=403, content={"detail": "host-only"})

        if settings.auth.mode == "token" and _needs_bearer(path):
            ip = client_ip(request)
            if bearer_matches(request, settings.auth.token):
                _auth_failures.clear_ip(ip)
            elif _auth_failures.is_blocked(ip):
                return JSONResponse(status_code=429, content={"detail": "too many requests"})
            else:
                _auth_failures.record_failure(ip)
                logger.warning(
                    "auth failure client=%s path=%s user_agent=%s",
                    ip,
                    path,
                    request.headers.get("user-agent", ""),
                )
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})

        return await call_next(request)


# Re-export for callers that validate bind hosts.
__all__ = [
    "AuthFailureTracker",
    "AuthMiddleware",
    "bearer_matches",
    "client_ip",
    "is_loopback_client",
    "reset_auth_failure_tracker_for_tests",
]
