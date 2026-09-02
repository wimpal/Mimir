"""Thin Ollama HTTP client — native /api/chat with tool support.

Phase 1 proof surface; Phase 2 FastAPI wraps the same client.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx


class OllamaError(RuntimeError):
    """Ollama unreachable, timed out, or returned a bad response."""


@dataclass(frozen=True)
class ToolCallFunction:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    function: ToolCallFunction
    type: str = "function"
    index: int | None = None


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_name: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize for Ollama /api/chat messages array."""
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            calls: list[dict[str, Any]] = []
            for i, tc in enumerate(self.tool_calls):
                entry: dict[str, Any] = {
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                idx = tc.index if tc.index is not None else i
                entry["function"]["index"] = idx
                calls.append(entry)
            out["tool_calls"] = calls
        if self.tool_name is not None:
            out["tool_name"] = self.tool_name
        return out


@dataclass(frozen=True)
class OllamaTimings:
    """Ollama /api/chat timing fields (nanoseconds unless noted)."""

    load_duration_ns: int | None = None
    prompt_eval_count: int | None = None
    prompt_eval_duration_ns: int | None = None
    eval_count: int | None = None
    eval_duration_ns: int | None = None

    @property
    def load_duration_ms(self) -> float | None:
        if self.load_duration_ns is None:
            return None
        return self.load_duration_ns / 1_000_000

    @property
    def prompt_eval_duration_ms(self) -> float | None:
        if self.prompt_eval_duration_ns is None:
            return None
        return self.prompt_eval_duration_ns / 1_000_000

    @property
    def eval_duration_ms(self) -> float | None:
        if self.eval_duration_ns is None:
            return None
        return self.eval_duration_ns / 1_000_000


def parse_ollama_timings(raw: dict[str, Any]) -> OllamaTimings:
    def _int(key: str) -> int | None:
        val = raw.get(key)
        return int(val) if isinstance(val, (int, float)) else None

    return OllamaTimings(
        load_duration_ns=_int("load_duration"),
        prompt_eval_count=_int("prompt_eval_count"),
        prompt_eval_duration_ns=_int("prompt_eval_duration"),
        eval_count=_int("eval_count"),
        eval_duration_ns=_int("eval_duration"),
    )


@dataclass(frozen=True)
class ChatResponse:
    message: ChatMessage
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    timings: OllamaTimings = field(default_factory=OllamaTimings)


@dataclass(frozen=True)
class ChatStreamChunk:
    """One NDJSON line from Ollama ``/api/chat`` with ``stream: true``."""

    delta: str
    done: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    timings: OllamaTimings = field(default_factory=OllamaTimings)


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    if not raw_calls:
        return []
    if not isinstance(raw_calls, list):
        raise OllamaError(f"tool_calls must be a list, got {type(raw_calls).__name__}")

    parsed: list[ToolCall] = []
    for i, item in enumerate(raw_calls):
        if not isinstance(item, dict):
            raise OllamaError(f"tool_calls[{i}] must be an object")
        fn = item.get("function")
        if not isinstance(fn, dict):
            raise OllamaError(f"tool_calls[{i}].function missing or invalid")
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            raise OllamaError(f"tool_calls[{i}].function.name missing")
        args = fn.get("arguments", {})
        if isinstance(args, str):
            # Some models/paths emit JSON strings; normalize to dict.
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError as exc:
                raise OllamaError(
                    f"tool_calls[{i}].function.arguments is not valid JSON"
                ) from exc
        if not isinstance(args, dict):
            raise OllamaError(f"tool_calls[{i}].function.arguments must be an object")
        index = fn.get("index", item.get("index"))
        if index is not None and not isinstance(index, int):
            index = None
        parsed.append(
            ToolCall(
                type=str(item.get("type", "function")),
                function=ToolCallFunction(name=name, arguments=args),
                index=index,
            )
        )
    return parsed


def parse_message(raw: dict[str, Any]) -> ChatMessage:
    role = raw.get("role", "assistant")
    if not isinstance(role, str):
        raise OllamaError("message.role must be a string")
    content = raw.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    tool_name = raw.get("tool_name")
    if tool_name is not None and not isinstance(tool_name, str):
        tool_name = str(tool_name)
    return ChatMessage(
        role=role,
        content=content,
        tool_calls=_parse_tool_calls(raw.get("tool_calls")),
        tool_name=tool_name,
    )


class OllamaClient:
    """Minimal sync client for Ollama native chat + tools."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        num_ctx: int = 8192,
        keep_alive: str | None = None,
        timeout_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.timeout_s = timeout_s
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _build_chat_body(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        think: bool,
        stream: bool,
    ) -> dict[str, Any]:
        api_messages: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, ChatMessage):
                api_messages.append(m.to_api_dict())
            elif isinstance(m, dict):
                api_messages.append(m)
            else:
                raise TypeError(f"unsupported message type: {type(m)!r}")

        body: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "stream": stream,
            "think": think,
            "options": {"num_ctx": self.num_ctx},
        }
        if self.keep_alive is not None:
            body["keep_alive"] = self.keep_alive
        if tools is not None:
            body["tools"] = tools
        return body

    def chat(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        think: bool = False,
        stream: bool = False,
    ) -> ChatResponse:
        if stream:
            raise OllamaError(
                "use chat_stream() for streaming (see docs/api-streaming.md)"
            )

        body = self._build_chat_body(messages, tools, think=think, stream=False)

        try:
            resp = self._client.post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama timed out after {self.timeout_s}s at {self.base_url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama unreachable at {self.base_url}: {exc}") from exc

        if resp.status_code >= 400:
            detail = resp.text.strip()[:300] or resp.reason_phrase
            raise OllamaError(f"Ollama HTTP {resp.status_code}: {detail}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned non-JSON body") from exc

        if not isinstance(data, dict):
            raise OllamaError("Ollama response must be a JSON object")

        raw_msg = data.get("message")
        if not isinstance(raw_msg, dict):
            raise OllamaError("Ollama response missing message object")

        return ChatResponse(
            message=parse_message(raw_msg),
            raw=data,
            timings=parse_ollama_timings(data),
        )

    def chat_stream(
        self,
        messages: list[ChatMessage | dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        think: bool = False,
    ) -> Iterator[ChatStreamChunk]:
        """Stream assistant content deltas from Ollama NDJSON."""
        body = self._build_chat_body(messages, tools, think=think, stream=True)

        try:
            with self._client.stream("POST", "/api/chat", json=body) as resp:
                if resp.status_code >= 400:
                    detail = resp.read().decode("utf-8", errors="replace").strip()[:300]
                    raise OllamaError(
                        f"Ollama HTTP {resp.status_code}: {detail or resp.reason_phrase}"
                    )

                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise OllamaError("Ollama stream returned non-JSON line") from exc
                    if not isinstance(data, dict):
                        raise OllamaError("Ollama stream line must be a JSON object")

                    raw_msg = data.get("message")
                    delta = ""
                    if isinstance(raw_msg, dict):
                        content = raw_msg.get("content") or ""
                        if not isinstance(content, str):
                            content = str(content)
                        delta = content

                    done = bool(data.get("done"))
                    timings = (
                        parse_ollama_timings(data) if done else OllamaTimings()
                    )
                    yield ChatStreamChunk(
                        delta=delta,
                        done=done,
                        raw=data,
                        timings=timings,
                    )
                    if done:
                        break
        except OllamaError:
            raise
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama timed out after {self.timeout_s}s at {self.base_url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama unreachable at {self.base_url}: {exc}") from exc

    def ping(self, *, timeout_s: float = 5.0) -> bool:
        """Cheap reachability check via GET /api/tags."""
        try:
            resp = self._client.get("/api/tags", timeout=httpx.Timeout(timeout_s))
        except httpx.HTTPError:
            return False
        return resp.status_code < 400
