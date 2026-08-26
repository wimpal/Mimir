"""HTTP Auth token + Host-only checks for the brain (Phase 7)."""

from __future__ import annotations

import hmac
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from brain.config import Settings

# Starlette TestClient reports this as the client host.
_TESTCLIENT_HOSTS = frozenset({"testclient", "localhost", "127.0.0.1", "::1"})


def is_loopback_client(request: Request) -> bool:
    """True when the request appears to come from the host machine."""
    client = request.client
    if client is None or not client.host:
        return True
    return client.host.strip().lower() in _TESTCLIENT_HOSTS


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
            if not bearer_matches(request, settings.auth.token):
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})

        return await call_next(request)


# Re-export for callers that validate bind hosts.
__all__ = [
    "AuthMiddleware",
    "bearer_matches",
    "is_loopback_client",
]
