"""Native chat HTTP models and route registration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from brain import __version__
from brain.config import Settings
from brain.db import Database
from brain.jellyfin_sync import SyncManager, catalogue_status_dict
from brain.ollama import OllamaClient
from brain.service import BrainService


class ChatMessageIn(BaseModel):
    role: str
    content: str | None = ""


class ChatRequest(BaseModel):
    message: str | None = None
    messages: list[ChatMessageIn] | None = None
    conversation_id: str | None = None
    stream: bool = False


class ChatResponseBody(BaseModel):
    reply: str
    conversation_id: str | None = None
    stopped_reason: str
    tools_used: list[str] = Field(default_factory=list)
    turn_id: str | None = None


class StoredMessageOut(BaseModel):
    role: str
    content: str
    created_at: str | None = None


class ConversationMessagesOut(BaseModel):
    conversation_id: str
    messages: list[StoredMessageOut]


def _sse_data(event: dict[str, Any]) -> str:
    payload = {k: v for k, v in event.items() if k != "http_status"}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _iter_sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    for event in events:
        yield _sse_data(event)


def register_chat_routes(application: FastAPI) -> None:
    """Attach health, chat, Sync, messages, and Host-only debug routes."""

    @application.get("/health")
    def health(request: Request) -> dict[str, Any]:
        s: Settings = request.app.state.settings
        db: Database = request.app.state.db
        ollama_client: OllamaClient = request.app.state.ollama

        ollama_ok = ollama_client.ping()
        db_ok = db.ping()

        if ollama_ok and db_ok:
            status = "ok"
        elif not ollama_ok and not db_ok:
            status = "fail"
        else:
            status = "degraded"

        sync_mgr: SyncManager | None = getattr(request.app.state, "sync_manager", None)
        if sync_mgr is not None:
            jellyfin_sync = sync_mgr.sync_status_dict()
        else:
            jellyfin_sync = catalogue_status_dict(db, s, configured=False)

        return {
            "status": status,
            "service": "mimir-brain",
            "version": __version__,
            "config_loaded": True,
            "single_user": True,
            "ollama": {
                "url": s.ollama.url,
                "model": s.ollama.model,
                "reachable": ollama_ok,
            },
            "db": {
                "ok": db_ok,
                "schema_version": db.schema_version(),
            },
            "jellyfin_sync": jellyfin_sync,
            "prompt_id": request.app.state.prompt_id,
        }

    @application.get("/debug/recent-traces")
    def recent_traces(request: Request, limit: int = 50) -> dict[str, Any]:
        from brain.turn_log import read_recent_traces, turns_log_path

        capped = max(1, min(int(limit), 200))
        data_dir = request.app.state.data_dir
        traces = read_recent_traces(turns_log_path(data_dir), limit=capped)
        return {"traces": traces, "limit": capped, "count": len(traces)}

    @application.post("/v1/jellyfin/sync")
    async def jellyfin_sync(request: Request) -> JSONResponse:
        sync_mgr: SyncManager | None = getattr(request.app.state, "sync_manager", None)
        if sync_mgr is None:
            return JSONResponse(
                status_code=503,
                content={
                    "ok": False,
                    "busy": False,
                    "configured": False,
                    "message": "jellyfin sync not available",
                    "state": {},
                },
            )

        result = await asyncio.to_thread(sync_mgr.run_sync, force=True)
        if not result.configured:
            return JSONResponse(
                status_code=503,
                content={
                    "ok": result.ok,
                    "busy": result.busy,
                    "configured": False,
                    "message": result.message,
                    "state": result.state,
                },
            )
        if result.busy:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "busy": True,
                    "configured": True,
                    "message": result.message,
                    "state": result.state,
                },
            )
        return JSONResponse(
            status_code=200,
            content={
                "ok": result.ok,
                "busy": False,
                "configured": True,
                "message": result.message,
                "state": result.state,
                "triggered": True,
            },
        )

    @application.get(
        "/v1/conversations/{conversation_id}/messages",
        response_model=ConversationMessagesOut,
    )
    def conversation_messages(
        conversation_id: str, request: Request
    ) -> ConversationMessagesOut:
        service: BrainService = request.app.state.service
        stored = service.list_conversation_messages(conversation_id)
        return ConversationMessagesOut(
            conversation_id=conversation_id.strip(),
            messages=[
                StoredMessageOut(
                    role=m.role, content=m.content, created_at=m.created_at
                )
                for m in stored
            ],
        )

    @application.post(
        "/v1/chat",
        response_model=ChatResponseBody,
        responses={
            200: {
                "description": "JSON reply, or SSE when stream=true",
            }
        },
    )
    def chat(
        body: ChatRequest, request: Request
    ) -> ChatResponseBody | JSONResponse | StreamingResponse:
        """Native Mimir chat. ``stream=true`` → SSE (docs/api-streaming.md)."""
        service: BrainService = request.app.state.service

        if body.conversation_id is not None and str(body.conversation_id).strip():
            if body.message is None or not str(body.message).strip():
                return JSONResponse(
                    status_code=400,
                    content={"detail": "conversation_id requires message"},
                )

        messages = None
        if body.messages is not None:
            messages = [m.model_dump() for m in body.messages]

        if body.stream:
            events = service.iter_chat_events(
                message=body.message,
                messages=messages,
                conversation_id=body.conversation_id,
            )
            # Peek first event for early 400 without starting SSE body wrongly.
            first: dict[str, Any] | None = None
            try:
                first = next(events)
            except StopIteration:
                first = None

            if first is not None and first.get("http_status") == 400:
                return JSONResponse(
                    status_code=400,
                    content={"detail": first.get("message", "bad request")},
                )

            def generate() -> Iterator[str]:
                if first is not None:
                    yield _sse_data(first)
                yield from _iter_sse(events)

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        outcome = service.run_chat(
            message=body.message,
            messages=messages,
            conversation_id=body.conversation_id,
            stream=False,
        )
        if outcome.http_status == 400:
            return JSONResponse(
                status_code=400,
                content={"detail": outcome.reply},
            )

        return ChatResponseBody(
            reply=outcome.reply,
            conversation_id=outcome.conversation_id,
            stopped_reason=outcome.stopped_reason,
            tools_used=outcome.tools_used,
            turn_id=outcome.turn_id,
        )
