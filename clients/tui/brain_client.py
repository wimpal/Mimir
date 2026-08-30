"""Async HTTP + SSE client for the Mimir brain."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import httpx
from brain.config import client_token_from_env, load_mimir_dotenv
from brain.db import CONVERSATIONS_LIST_DEFAULT

DEFAULT_BRAIN_URL = "http://127.0.0.1:8000"
DEFAULT_TURN_TIMEOUT_S = 180.0
CONNECT_TIMEOUT_S = 5.0
CONTROL_TIMEOUT_S = 10.0
VOICE_STT_TIMEOUT_S = 90.0


class BrainClientError(RuntimeError):
    """Brain unreachable, timed out, or returned a bad response."""


@dataclass(frozen=True)
class HealthInfo:
    status: str
    reachable: bool
    detail: str = ""


@dataclass(frozen=True)
class SttResult:
    text: str
    language: str | None = None


def normalize_brain_url(url: str) -> str:
    """Strip trailing slash and accidental ``/v1`` API suffix."""
    text = (url or "").strip()
    if not text:
        raise ValueError("brain URL is empty")
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid brain URL: {url!r}")
    path = (parsed.path or "").rstrip("/")
    if path == "/v1" or path.endswith("/v1"):
        path = path[: -len("/v1")] if path.endswith("/v1") else ""
    cleaned = urlunparse(
        (parsed.scheme, parsed.netloc, path, "", "", "")
    ).rstrip("/")
    return cleaned


def parse_sse_chunk(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """Split a SSE buffer into complete events; return (events, remainder)."""
    events: list[dict[str, Any]] = []
    rest = buffer
    while True:
        sep = -1
        for candidate in ("\r\n\r\n", "\n\n"):
            idx = rest.find(candidate)
            if idx >= 0 and (sep < 0 or idx < sep):
                sep = idx
                sep_len = len(candidate)
        if sep < 0:
            break
        raw = rest[:sep]
        rest = rest[sep + sep_len :]
        data_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events, rest


class BrainClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BRAIN_URL,
        *,
        turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S,
        auth_token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = normalize_brain_url(base_url)
        self.turn_timeout_s = max(1.0, float(turn_timeout_s))
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_S,
            read=self.turn_timeout_s,
            write=CONNECT_TIMEOUT_S,
            pool=CONNECT_TIMEOUT_S,
        )
        headers: dict[str, str] = {"Accept": "application/json"}
        load_mimir_dotenv()  # repo-root .env — TUI cwd is often not the repo
        env_token = client_token_from_env() or ""
        token = (auth_token if auth_token is not None else env_token).strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            headers=headers,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> BrainClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def health(self) -> HealthInfo:
        try:
            resp = await self._client.get("/health")
        except httpx.TimeoutException as exc:
            raise BrainClientError(
                f"brain health timed out at {self.base_url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise BrainClientError(
                f"brain unreachable at {self.base_url}: {exc}"
            ) from exc

        try:
            body = resp.json()
        except ValueError as exc:
            raise BrainClientError("health returned non-JSON") from exc

        if not isinstance(body, dict):
            raise BrainClientError("health returned unexpected JSON")

        status = str(body.get("status", "unknown"))
        # HTTP 200 can still mean degraded/fail — inspect body.
        reachable = resp.status_code < 500
        detail = ""
        ollama = body.get("ollama")
        if isinstance(ollama, dict) and ollama.get("reachable") is False:
            detail = "ollama unreachable"
        elif status != "ok":
            detail = f"status={status}"
        return HealthInfo(status=status, reachable=reachable, detail=detail)

    async def list_conversations(
        self, *, limit: int = CONVERSATIONS_LIST_DEFAULT
    ) -> list[dict[str, Any]]:
        try:
            resp = await self._client.get(
                "/v1/conversations", params={"limit": limit}
            )
        except httpx.TimeoutException as exc:
            raise BrainClientError("list conversations timed out") from exc
        except httpx.HTTPError as exc:
            raise BrainClientError(f"list conversations failed: {exc}") from exc

        if resp.status_code >= 400:
            raise BrainClientError(
                f"list conversations HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise BrainClientError("list conversations returned non-JSON") from exc
        if not isinstance(body, dict):
            raise BrainClientError("list conversations returned unexpected JSON")
        convos = body.get("conversations")
        if not isinstance(convos, list):
            raise BrainClientError("list conversations returned unexpected JSON")
        out: list[dict[str, Any]] = []
        for item in convos:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                out.append(item)
        return out

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        cid = conversation_id.strip()
        if not cid:
            return []
        path = f"/v1/conversations/{quote(cid, safe='')}/messages"
        try:
            resp = await self._client.get(path)
        except httpx.TimeoutException as exc:
            raise BrainClientError("list messages timed out") from exc
        except httpx.HTTPError as exc:
            raise BrainClientError(f"list messages failed: {exc}") from exc

        if resp.status_code >= 400:
            raise BrainClientError(
                f"list messages HTTP {resp.status_code}: {resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise BrainClientError("list messages returned non-JSON") from exc
        msgs = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(msgs, list):
            return []
        out: list[dict[str, Any]] = []
        for item in msgs:
            if isinstance(item, dict):
                out.append(item)
        return out

    async def _control_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        label: str,
    ) -> dict[str, Any]:
        """GET/PUT-style control call with a short timeout (not chat-length)."""
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_S,
            read=CONTROL_TIMEOUT_S,
            write=CONNECT_TIMEOUT_S,
            pool=CONNECT_TIMEOUT_S,
        )
        try:
            resp = await self._client.request(
                method,
                path,
                json=json_body,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise BrainClientError(f"{label} timed out") from exc
        except httpx.HTTPError as exc:
            raise BrainClientError(f"{label} failed: {exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text[:200]
            try:
                err = resp.json()
                if isinstance(err, dict) and err.get("detail"):
                    detail = str(err["detail"])
            except ValueError:
                pass
            raise BrainClientError(f"{label} HTTP {resp.status_code}: {detail}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise BrainClientError(f"{label} returned non-JSON") from exc
        if not isinstance(body, dict):
            raise BrainClientError(f"{label} returned unexpected JSON")
        return body

    async def get_preferences(self) -> list[dict[str, Any]]:
        body = await self._control_request(
            "GET", "/v1/preferences", label="get preferences"
        )
        prefs = body.get("preferences")
        if not isinstance(prefs, list):
            raise BrainClientError("get preferences returned unexpected JSON")
        out: list[dict[str, Any]] = []
        for item in prefs:
            if isinstance(item, dict) and str(item.get("key") or "").strip():
                out.append(item)
        return out

    async def put_preference(self, key: str, value: str) -> dict[str, Any]:
        key = key.strip()
        path = f"/v1/preferences/{quote(key, safe='')}"
        body = await self._control_request(
            "PUT",
            path,
            json_body={"value": value},
            label="put preference",
        )
        if not str(body.get("key") or "").strip():
            raise BrainClientError("put preference returned unexpected JSON")
        return body

    async def stt(
        self,
        audio: bytes,
        *,
        language: str | None = None,
    ) -> SttResult:
        params: dict[str, str] = {}
        if language:
            params["language"] = language
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_S,
            read=VOICE_STT_TIMEOUT_S,
            write=CONNECT_TIMEOUT_S,
            pool=CONNECT_TIMEOUT_S,
        )
        try:
            resp = await self._client.post(
                "/v1/stt",
                content=audio,
                params=params or None,
                headers={"Content-Type": "audio/wav"},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise BrainClientError("speech recognition timed out") from exc
        except httpx.HTTPError as exc:
            raise BrainClientError(f"speech recognition failed: {exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text[:200]
            try:
                err = resp.json()
                if isinstance(err, dict):
                    nested = err.get("error")
                    if isinstance(nested, dict) and nested.get("message"):
                        detail = str(nested["message"])
                    elif err.get("detail"):
                        detail = str(err["detail"])
            except ValueError:
                pass
            raise BrainClientError(
                f"speech recognition HTTP {resp.status_code}: {detail}"
            )
        try:
            body = resp.json()
        except ValueError as exc:
            raise BrainClientError("speech recognition returned non-JSON") from exc
        if not isinstance(body, dict):
            raise BrainClientError("speech recognition returned unexpected JSON")
        text = str(body.get("text") or "").strip()
        if not text:
            raise BrainClientError("No speech detected.")
        lang = body.get("language")
        return SttResult(text=text, language=str(lang) if lang else None)

    async def stream_chat(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        payload: dict[str, Any] = {"message": message, "stream": True}
        if conversation_id and conversation_id.strip():
            payload["conversation_id"] = conversation_id.strip()

        try:
            async with self._client.stream(
                "POST",
                "/v1/chat",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", errors="replace")
                    detail = raw
                    try:
                        err = json.loads(raw)
                        if isinstance(err, dict) and err.get("detail"):
                            detail = str(err["detail"])
                    except json.JSONDecodeError:
                        pass
                    raise BrainClientError(detail or f"HTTP {resp.status_code}")

                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    events, buffer = parse_sse_chunk(buffer)
                    for event in events:
                        yield event

                if buffer.strip():
                    events, _ = parse_sse_chunk(
                        buffer if buffer.endswith("\n\n") else buffer + "\n\n"
                    )
                    for event in events:
                        yield event
        except BrainClientError:
            raise
        except httpx.TimeoutException as exc:
            raise BrainClientError(
                f"chat timed out after {self.turn_timeout_s:.0f}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise BrainClientError(f"chat failed: {exc}") from exc
