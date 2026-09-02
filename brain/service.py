"""Chat service facade — system prompt + agent loop + graceful failures."""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from brain.agent import StoppedReason, TurnResult, run_turn
from brain.config import Settings
from brain.db import (
    CONVERSATIONS_LIST_DEFAULT,
    ConversationSummary,
    Database,
    StoredMessage,
    clamp_conversations_limit,
)
from brain.ollama import ChatMessage, OllamaClient
from brain.prefs import (
    ALLOWED_KEYS,
    PREFERENCE_KEYS,
    build_system_prompt,
    normalize_preference_value,
)
from brain.tools import Tool, build_registry
from brain.turn_log import append_turn_trace
from brain.voice.sentences import SentenceBuffer

logger = logging.getLogger("mimir.service")


class PreferenceError(ValueError):
    """Unknown key or invalid Preference value (maps to HTTP 400)."""

MSG_OLLAMA_DOWN = "The brain can't reach the language model right now."
MSG_TURN_TIMEOUT = "That request took too long and was stopped. Please try again."
MSG_MAX_ITERATIONS = "I got stuck calling tools and had to stop. Please try a simpler request."
MSG_EMPTY = "I didn't get a usable reply from the model. Please try again."
MSG_STREAMING = (
    "Streaming is not implemented on the OpenAI-compatible endpoint yet. "
    "Use native POST /v1/chat with stream=true, or stream=false here. "
    "See docs/api-streaming.md."
)
MSG_CONVERSATION_NEEDS_MESSAGE = "conversation_id requires message"
MSG_STREAM_ERROR = "Something went wrong while handling that request."

_REPLY_BY_REASON: dict[StoppedReason, str] = {
    StoppedReason.OLLAMA_ERROR: MSG_OLLAMA_DOWN,
    StoppedReason.TURN_TIMEOUT: MSG_TURN_TIMEOUT,
    StoppedReason.MAX_ITERATIONS: MSG_MAX_ITERATIONS,
    StoppedReason.EMPTY_RESPONSE: MSG_EMPTY,
}


class ChatStopReason(StrEnum):
    """Service-layer outcomes that never come from ``run_turn``."""

    STREAMING_NOT_IMPLEMENTED = "streaming_not_implemented"
    BAD_REQUEST = "bad_request"


@dataclass
class ChatOutcome:
    reply: str
    conversation_id: str | None
    stopped_reason: str
    tools_used: list[str] = field(default_factory=list)
    turn_id: str | None = None
    http_status: int = 200


def _user_facing_reply(result: TurnResult) -> str:
    if result.stopped_reason == StoppedReason.FINAL and (result.content or "").strip():
        return result.content
    mapped = _REPLY_BY_REASON.get(result.stopped_reason)
    if mapped is not None:
        return mapped
    if (result.content or "").strip():
        return result.content
    return MSG_EMPTY


def _normalize_conversation_id(conversation_id: str | None) -> str | None:
    if conversation_id is None:
        return None
    text = str(conversation_id).strip()
    return text or None


def build_messages(
    system_prompt: str,
    *,
    message: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> list[ChatMessage]:
    """Build Ollama messages: Mimir system prompt + user/assistant turns.

    If both ``message`` and ``messages`` are provided, ``messages`` wins.
    Client ``system`` roles are dropped — the brain owns personality.
    """
    out: list[ChatMessage] = [ChatMessage(role="system", content=system_prompt)]

    if messages is not None:
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                continue
            if role not in ("user", "assistant"):
                continue
            if not isinstance(content, str):
                content = "" if content is None else str(content)
            out.append(ChatMessage(role=role, content=content))
        return out

    if message is None or not str(message).strip():
        raise ValueError("either message or messages is required")
    out.append(ChatMessage(role="user", content=str(message)))
    return out


class BrainService:
    def __init__(
        self,
        settings: Settings,
        client: OllamaClient,
        *,
        system_prompt: str,
        prompt_id: str,
        data_dir: Any,
        db: Database | None = None,
        tools: dict[str, Tool] | None = None,
        unavailable_services: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.system_prompt = system_prompt
        self.prompt_id = prompt_id
        self.data_dir = data_dir
        self.db = db
        self.unavailable_services = list(unavailable_services or [])
        if tools is not None:
            self.tools = tools
        else:
            self.tools = build_registry(settings, db=db, data_dir=data_dir)

    def _system_prompt_for_turn(self, prefs: dict[str, str] | None = None) -> str:
        stored = prefs
        if stored is None:
            stored = self.db.get_preferences() if self.db is not None else {}
        return build_system_prompt(
            self.system_prompt,
            stored,
            unavailable_services=self.unavailable_services,
            timezone=self.settings.location.timezone,
        )

    def _refresh_system_after_pref_tool(
        self,
        name: str,
        result: str,
        working: list[ChatMessage],
    ) -> None:
        if name != "set_preference" or result.startswith("error:") or self.db is None:
            return
        if not working or working[0].role != "system":
            return
        prefs = self.db.get_preferences()
        working[0] = ChatMessage(
            role="system",
            content=self._system_prompt_for_turn(prefs),
        )

    def list_conversation_messages(self, conversation_id: str) -> list[StoredMessage]:
        """Full Conversation Messages; unknown id → empty (no create)."""
        if self.db is None:
            return []
        cid = _normalize_conversation_id(conversation_id)
        if cid is None:
            return []
        return self.db.list_messages(cid)

    def list_conversations(
        self, *, limit: int = CONVERSATIONS_LIST_DEFAULT
    ) -> tuple[list[ConversationSummary], int]:
        """List Conversations for `/history`. Returns (rows, effective_limit)."""
        capped = clamp_conversations_limit(limit)
        if self.db is None:
            return [], capped
        return self.db.list_conversations(limit=capped), capped

    def list_preferences(self) -> list[dict[str, str | None]]:
        """Allowlisted Preferences in ``PREFERENCE_KEYS`` order; unset → null."""
        stored: dict[str, str] = {}
        if self.db is not None:
            stored = self.db.get_preferences()
        return [
            {"key": key, "value": stored.get(key)}
            for key in PREFERENCE_KEYS
        ]

    def set_preference(self, key: str, value: str) -> str:
        """Normalize and store a Preference. Raises ``PreferenceError`` on bad input."""
        key = (key or "").strip()
        if key not in ALLOWED_KEYS:
            allowed = ", ".join(PREFERENCE_KEYS)
            raise PreferenceError(
                f"unknown preference key '{key}' (allowed: {allowed})"
            )
        stored = normalize_preference_value(key, value)
        if stored is None:
            raise PreferenceError(f"invalid value for preference '{key}'")
        if self.db is None:
            raise PreferenceError("preferences store unavailable")
        self.db.set_preference(key, stored)
        return stored

    def _run_persist_turn(
        self,
        *,
        user_text: str,
        conversation_id: str,
        on_tool_start: Any = None,
        on_tool_end: Any = None,
    ) -> ChatOutcome:
        assert self.db is not None
        self.db.ensure_conversation(conversation_id)
        limit = max(0, self.settings.memory.history_pairs) * 2
        history = self.db.list_recent_messages(conversation_id, limit=limit)
        prefs = self.db.get_preferences()
        system = self._system_prompt_for_turn(prefs)
        chat_messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system),
            *[ChatMessage(role=m.role, content=m.content) for m in history],
            ChatMessage(role="user", content=user_text),
        ]

        deadline = time.monotonic() + self.settings.timeouts.turn_s
        result = run_turn(
            self.client,
            chat_messages,
            tools=self.tools,
            max_iterations=self.settings.agent.max_iterations,
            think=self.settings.ollama.think,
            deadline_monotonic=deadline,
            default_tool_timeout_s=self.settings.timeouts.tool_s,
            after_tool=self._refresh_system_after_pref_tool,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            data_dir=self.data_dir,
        )

        reply = _user_facing_reply(result)
        self.db.append_message(conversation_id, "user", user_text)
        self.db.append_message(conversation_id, "assistant", reply)

        tools_used = result.tools_used()
        turn_id = append_turn_trace(
            self.data_dir,
            prompt_id=self.prompt_id,
            result=result,
            conversation_id=conversation_id,
        )
        return ChatOutcome(
            reply=reply,
            conversation_id=conversation_id,
            stopped_reason=str(result.stopped_reason),
            tools_used=tools_used,
            turn_id=turn_id,
            http_status=200,
        )

    def _run_stateless_turn(
        self,
        *,
        message: str | None,
        messages: list[dict[str, Any]] | None,
        conversation_id: str | None,
        on_tool_start: Any = None,
        on_tool_end: Any = None,
    ) -> ChatOutcome:
        try:
            chat_messages = build_messages(
                self._system_prompt_for_turn(), message=message, messages=messages
            )
        except ValueError as exc:
            return ChatOutcome(
                reply=str(exc),
                conversation_id=conversation_id,
                stopped_reason=ChatStopReason.BAD_REQUEST,
                http_status=400,
            )

        if len(chat_messages) < 2:
            return ChatOutcome(
                reply="either message or messages is required",
                conversation_id=conversation_id,
                stopped_reason=ChatStopReason.BAD_REQUEST,
                http_status=400,
            )

        deadline = time.monotonic() + self.settings.timeouts.turn_s
        result = run_turn(
            self.client,
            chat_messages,
            tools=self.tools,
            max_iterations=self.settings.agent.max_iterations,
            think=self.settings.ollama.think,
            deadline_monotonic=deadline,
            default_tool_timeout_s=self.settings.timeouts.tool_s,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            data_dir=self.data_dir,
        )

        reply = _user_facing_reply(result)
        tools_used = result.tools_used()
        turn_id = append_turn_trace(
            self.data_dir,
            prompt_id=self.prompt_id,
            result=result,
            conversation_id=conversation_id,
        )
        return ChatOutcome(
            reply=reply,
            conversation_id=conversation_id,
            stopped_reason=str(result.stopped_reason),
            tools_used=tools_used,
            turn_id=turn_id,
            http_status=200,
        )

    def _execute_turn(
        self,
        chat_messages: list[ChatMessage],
        *,
        on_tool_start: Any = None,
        on_tool_end: Any = None,
        on_assistant_delta: Any = None,
    ) -> TurnResult:
        deadline = time.monotonic() + self.settings.timeouts.turn_s
        return run_turn(
            self.client,
            chat_messages,
            tools=self.tools,
            max_iterations=self.settings.agent.max_iterations,
            think=self.settings.ollama.think,
            deadline_monotonic=deadline,
            default_tool_timeout_s=self.settings.timeouts.tool_s,
            after_tool=self._refresh_system_after_pref_tool,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            on_assistant_delta=on_assistant_delta,
            data_dir=self.data_dir,
        )

    def _outcome_from_turn(
        self,
        result: TurnResult,
        *,
        conversation_id: str | None,
        user_text: str | None = None,
    ) -> ChatOutcome:
        reply = _user_facing_reply(result)
        if user_text is not None and self.db is not None and conversation_id is not None:
            self.db.append_message(conversation_id, "user", user_text)
            self.db.append_message(conversation_id, "assistant", reply)
        tools_used = result.tools_used()
        turn_id = append_turn_trace(
            self.data_dir,
            prompt_id=self.prompt_id,
            result=result,
            conversation_id=conversation_id,
        )
        return ChatOutcome(
            reply=reply,
            conversation_id=conversation_id,
            stopped_reason=str(result.stopped_reason),
            tools_used=tools_used,
            turn_id=turn_id,
            http_status=200,
        )

    def _iter_streaming_turn_events(
        self,
        chat_messages: list[ChatMessage],
        *,
        user_text: str | None,
        conversation_id: str | None,
    ) -> Iterator[dict[str, Any] | ChatOutcome]:
        """Run turn in a worker thread; yield SSE dicts as they are produced."""
        event_q: queue.Queue[Any] = queue.Queue()
        _DONE = object()

        sentence_buf = SentenceBuffer()
        sentence_idx = 0
        turn_started = time.monotonic()
        first_sentence_logged = False
        streamed_parts: list[str] = []

        def on_tool_start(name: str, arguments: dict[str, Any] | None) -> None:
            event: dict[str, Any] = {"type": "tool_start", "name": name}
            if arguments is not None:
                event["arguments"] = arguments
            event_q.put(event)

        def on_tool_end(name: str, ok: bool, preview: str) -> None:
            event_q.put(
                {
                    "type": "tool_end",
                    "name": name,
                    "ok": ok,
                    "result_preview": preview,
                }
            )

        def emit_assistant_text(text: str) -> None:
            nonlocal sentence_idx, first_sentence_logged
            if not text:
                return
            event_q.put({"type": "token", "text": text})
            for sent in sentence_buf.feed(text):
                event_q.put(
                    {"type": "sentence", "index": sentence_idx, "text": sent}
                )
                if not first_sentence_logged:
                    first_sentence_logged = True
                    ms = int((time.monotonic() - turn_started) * 1000)
                    logger.info("time_to_first_sentence_ms=%s", ms)
                sentence_idx += 1

        def on_assistant_delta(delta: str) -> None:
            streamed_parts.append(delta)
            emit_assistant_text(delta)

        def worker() -> None:
            try:
                result = self._execute_turn(
                    chat_messages,
                    on_tool_start=on_tool_start,
                    on_tool_end=on_tool_end,
                    on_assistant_delta=on_assistant_delta,
                )
                outcome = self._outcome_from_turn(
                    result,
                    conversation_id=conversation_id,
                    user_text=user_text,
                )
                streamed = "".join(streamed_parts)
                reply = outcome.reply
                if reply:
                    if not streamed:
                        emit_assistant_text(reply)
                    elif reply.startswith(streamed):
                        unsent = reply[len(streamed) :]
                        if unsent:
                            emit_assistant_text(unsent)
                    elif streamed != reply:
                        emit_assistant_text(reply)
                tail = sentence_buf.flush()
                if tail:
                    event_q.put(
                        {
                            "type": "sentence",
                            "index": sentence_idx,
                            "text": tail,
                        }
                    )
                event_q.put(outcome)
            except Exception as exc:  # noqa: BLE001
                event_q.put(exc)
            finally:
                event_q.put(_DONE)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = event_q.get()
            if item is _DONE:
                break
            if isinstance(item, Exception):
                raise item
            yield item

        thread.join(timeout=1.0)

    def run_chat(
        self,
        *,
        message: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        conversation_id: str | None = None,
        stream: bool = False,
    ) -> ChatOutcome:
        # OpenAI-compat and defense-in-depth: native SSE uses iter_chat_events.
        if stream:
            return ChatOutcome(
                reply=MSG_STREAMING,
                conversation_id=_normalize_conversation_id(conversation_id),
                stopped_reason=ChatStopReason.STREAMING_NOT_IMPLEMENTED,
                http_status=501,
            )

        cid = _normalize_conversation_id(conversation_id)
        has_message = message is not None and bool(str(message).strip())

        if cid is not None and not has_message:
            return ChatOutcome(
                reply=MSG_CONVERSATION_NEEDS_MESSAGE,
                conversation_id=cid,
                stopped_reason=ChatStopReason.BAD_REQUEST,
                http_status=400,
            )

        if has_message and self.db is not None:
            resolved_id = cid or str(uuid.uuid4())
            return self._run_persist_turn(
                user_text=str(message).strip(),
                conversation_id=resolved_id,
            )

        return self._run_stateless_turn(
            message=message,
            messages=messages,
            conversation_id=cid,
        )

    def iter_chat_events(
        self,
        *,
        message: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        conversation_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield SSE event dicts for native ``/v1/chat`` streaming.

        Order: meta → tool_start/tool_end* → token* → sentence* → done | error.
        Validation failures yield a single error-shaped dict with ``http_status``.
        """
        cid = _normalize_conversation_id(conversation_id)
        has_message = message is not None and bool(str(message).strip())

        if cid is not None and not has_message:
            yield {
                "type": "error",
                "message": MSG_CONVERSATION_NEEDS_MESSAGE,
                "http_status": 400,
            }
            return

        resolved_id: str | None
        persist = has_message and self.db is not None
        user_text = str(message).strip() if has_message else None

        if persist:
            resolved_id = cid or str(uuid.uuid4())
        else:
            resolved_id = cid

        if resolved_id is not None:
            yield {"type": "meta", "conversation_id": resolved_id}

        chat_messages: list[ChatMessage] | None = None
        if persist:
            assert self.db is not None
            assert resolved_id is not None
            assert user_text is not None
            self.db.ensure_conversation(resolved_id)
            limit = max(0, self.settings.memory.history_pairs) * 2
            history = self.db.list_recent_messages(resolved_id, limit=limit)
            prefs = self.db.get_preferences()
            system = self._system_prompt_for_turn(prefs)
            chat_messages = [
                ChatMessage(role="system", content=system),
                *[ChatMessage(role=m.role, content=m.content) for m in history],
                ChatMessage(role="user", content=user_text),
            ]
        else:
            try:
                chat_messages = build_messages(
                    self._system_prompt_for_turn(),
                    message=message,
                    messages=messages,
                )
            except ValueError as exc:
                yield {
                    "type": "error",
                    "message": str(exc),
                    "http_status": 400,
                    "conversation_id": resolved_id,
                }
                return
            if len(chat_messages) < 2:
                yield {
                    "type": "error",
                    "message": "either message or messages is required",
                    "http_status": 400,
                    "conversation_id": resolved_id,
                }
                return

        outcome: ChatOutcome | None = None
        try:
            for item in self._iter_streaming_turn_events(
                chat_messages,
                user_text=user_text if persist else None,
                conversation_id=resolved_id,
            ):
                if isinstance(item, ChatOutcome):
                    outcome = item
                else:
                    yield item
        except Exception:  # noqa: BLE001
            logger.exception("iter_chat_events failed")
            err: dict[str, Any] = {"type": "error", "message": MSG_STREAM_ERROR}
            if resolved_id is not None:
                err["conversation_id"] = resolved_id
            yield err
            return

        if outcome is None:
            yield {"type": "error", "message": MSG_STREAM_ERROR}
            return

        if outcome.http_status == 400:
            yield {
                "type": "error",
                "message": outcome.reply,
                "http_status": 400,
                "conversation_id": outcome.conversation_id,
            }
            return

        done: dict[str, Any] = {
            "type": "done",
            "stopped_reason": outcome.stopped_reason,
            "tools_used": outcome.tools_used,
            "turn_id": outcome.turn_id,
        }
        if outcome.conversation_id is not None:
            done["conversation_id"] = outcome.conversation_id
        yield done
