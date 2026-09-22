"""Native chat HTTP models and route registration."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from brain import __version__
from brain.active_model import ActiveModelStateError, load_active_profile, write_active_profile
from brain.config import Settings, normalized_ollama_profiles
from brain.db import CONVERSATIONS_LIST_DEFAULT, Database
from brain.jellyfin_sync import SyncManager, catalogue_status_dict
from brain.ollama import OllamaClient
from brain.service import BrainService, PreferenceError


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


class ConversationSummaryOut(BaseModel):
    id: str
    created_at: str
    updated_at: str
    preview: str
    message_count: int


class ConversationsListOut(BaseModel):
    conversations: list[ConversationSummaryOut]
    limit: int
    count: int


class PreferenceItemOut(BaseModel):
    key: str
    value: str | None = None


class PreferencesListOut(BaseModel):
    preferences: list[PreferenceItemOut]


class PreferencePutIn(BaseModel):
    value: str


class PreferenceOut(BaseModel):
    key: str
    value: str


class ModelProfileItemOut(BaseModel):
    name: str
    model: str
    num_ctx: int
    think: bool


class ModelProfilesOut(BaseModel):
    active_profile: str
    loaded_profile: str
    loaded_model: str
    restart_pending: bool
    env_masked_fields: list[str] = Field(default_factory=list)
    profiles: list[ModelProfileItemOut] = Field(default_factory=list)


class ModelProfileActiveIn(BaseModel):
    profile: str


class ModelProfileActiveOut(BaseModel):
    active_profile: str
    model: str
    changed: bool
    restart_required: bool


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

        voice_block: dict[str, object] | None = None
        voice_svc = getattr(request.app.state, "voice_service", None)
        if voice_svc is not None:
            vh = voice_svc.health_status()
            voice_block = {
                "enabled": vh.enabled,
                "stt": vh.stt,
                "tts": vh.tts,
                "ffmpeg": vh.ffmpeg,
                "warmed": vh.warmed,
            }

        # CONVENTIONS.md required keys; extras (ollama, db detail, jellyfin) stay.
        payload: dict[str, Any] = {
            "service": "mimir",
            "status": status,
            "version": __version__,
            "checks": {
                "db": "ok" if db_ok else "fail",
            },
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
        if voice_block is not None:
            payload["voice"] = voice_block
        return payload

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
        "/v1/conversations",
        response_model=ConversationsListOut,
    )
    def conversations_list(
        request: Request, limit: int = CONVERSATIONS_LIST_DEFAULT
    ) -> ConversationsListOut:
        service: BrainService = request.app.state.service
        rows, effective = service.list_conversations(limit=limit)
        return ConversationsListOut(
            conversations=[
                ConversationSummaryOut(
                    id=r.id,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    preview=r.preview,
                    message_count=r.message_count,
                )
                for r in rows
            ],
            limit=effective,
            count=len(rows),
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

    @application.get(
        "/v1/preferences",
        response_model=PreferencesListOut,
    )
    def preferences_list(request: Request) -> PreferencesListOut:
        service: BrainService = request.app.state.service
        rows = service.list_preferences()
        return PreferencesListOut(
            preferences=[
                PreferenceItemOut(key=str(r["key"]), value=r.get("value"))
                for r in rows
            ]
        )

    @application.put(
        "/v1/preferences/{key}",
        response_model=PreferenceOut,
    )
    def preference_put(
        key: str, body: PreferencePutIn, request: Request
    ) -> PreferenceOut:
        service: BrainService = request.app.state.service
        try:
            stored = service.set_preference(key, body.value)
        except PreferenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PreferenceOut(key=key.strip(), value=stored)

    @application.get(
        "/v1/model-profiles",
        response_model=ModelProfilesOut,
    )
    def model_profiles_list(request: Request) -> ModelProfilesOut:
        s: Settings = request.app.state.settings
        data_dir = Path(request.app.state.data_dir)
        catalog = normalized_ollama_profiles(s.ollama)
        try:
            sticky = load_active_profile(data_dir)
        except ActiveModelStateError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        active = sticky if sticky is not None else "default"
        loaded_profile = s.ollama.active_profile or "default"
        loaded_model = s.ollama.model
        restart_pending = active != loaded_profile
        if active in catalog and catalog[active].model != loaded_model:
            restart_pending = True
        profiles = [
            ModelProfileItemOut(
                name=name,
                model=str(p.model or ""),
                num_ctx=int(p.num_ctx if p.num_ctx is not None else s.ollama.num_ctx),
                think=bool(p.think if p.think is not None else s.ollama.think),
            )
            for name, p in sorted(catalog.items())
        ]
        return ModelProfilesOut(
            active_profile=active,
            loaded_profile=loaded_profile,
            loaded_model=loaded_model,
            restart_pending=restart_pending,
            env_masked_fields=list(s.ollama.env_masked_fields),
            profiles=profiles,
        )

    @application.put(
        "/v1/model-profiles/active",
        response_model=ModelProfileActiveOut,
    )
    def model_profiles_set_active(
        body: ModelProfileActiveIn, request: Request
    ) -> ModelProfileActiveOut:
        s: Settings = request.app.state.settings
        data_dir = Path(request.app.state.data_dir)
        catalog = normalized_ollama_profiles(s.ollama)
        name = (body.profile or "").strip()
        if name not in catalog:
            known = ", ".join(sorted(catalog))
            raise HTTPException(
                status_code=400,
                detail=f"unknown profile {name!r}; known: {known}",
            )
        try:
            sticky = load_active_profile(data_dir)
        except ActiveModelStateError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        current_sticky = sticky if sticky is not None else "default"
        loaded_profile = s.ollama.active_profile or "default"
        model_tag = str(catalog[name].model or "")
        changed = current_sticky != name
        if changed:
            try:
                write_active_profile(data_dir, name)
            except ActiveModelStateError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        restart_required = name != loaded_profile
        return ModelProfileActiveOut(
            active_profile=name,
            model=model_tag,
            changed=changed,
            restart_required=restart_required,
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
