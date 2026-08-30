"""Mimir brain — FastAPI service (Phase 2+).

Endpoints:
  GET  /health
  POST /v1/chat
  POST /v1/chat/completions   (OpenAI-compatible; tools run server-side)
  POST /v1/stt                (speech-to-text)
  POST /v1/tts                (text-to-speech)
  GET  /v1/models
  GET  /v1/conversations
  GET  /v1/conversations/{id}/messages
  GET  /v1/preferences
  PUT  /v1/preferences/{key}
  POST /v1/jellyfin/sync
  GET  /debug/recent-traces   (Host-only)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from brain import __version__
from brain.api_chat import register_chat_routes
from brain.auth import AuthMiddleware
from brain.config import Settings, jellyfin_sync_configured, load_config, validate_bind_auth
from brain.db import Database
from brain.jellyfin_sync import SyncManager
from brain.mcp.bridge import McpBridge
from brain.ollama import OllamaClient
from brain.openai_compat import models_list, run_openai_chat
from brain.prompt import PromptError, load_system_prompt
from brain.service import BrainService
from brain.voice.api import register_voice_routes
from brain.voice.service import VoiceService

logger = logging.getLogger("mimir")


def _configure_logging(level: str) -> None:
    root = logging.getLogger("mimir")
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False


def _wire_state(
    app: FastAPI,
    settings: Settings,
    *,
    client: OllamaClient | None = None,
    system_prompt: str | None = None,
    prompt_id: str | None = None,
    data_dir: Path | None = None,
    mcp_bridge: McpBridge | None = None,
) -> None:
    validate_bind_auth(settings)
    resolved_data = data_dir or settings.runtime.ensure_data_dir()
    if system_prompt is None or prompt_id is None:
        text, pid = load_system_prompt(settings.agent.system_prompt_path)
        system_prompt = system_prompt or text
        prompt_id = prompt_id or pid

    db = Database(resolved_data / "mimir.db")
    ollama = client or OllamaClient(
        settings.ollama.url,
        settings.ollama.model,
        num_ctx=settings.ollama.num_ctx,
        timeout_s=settings.timeouts.ollama_s,
    )
    from brain.tools import build_registry

    tools = build_registry(settings, db=db, data_dir=resolved_data, mcp=mcp_bridge)
    service = BrainService(
        settings,
        ollama,
        system_prompt=system_prompt,
        prompt_id=prompt_id,
        data_dir=resolved_data,
        db=db,
        tools=tools,
        unavailable_services=mcp_bridge.unavailable if mcp_bridge else [],
    )
    sync_manager = SyncManager(settings, db)
    voice_service = VoiceService(settings, data_dir=resolved_data)

    app.state.settings = settings
    app.state.data_dir = resolved_data
    app.state.db = db
    app.state.ollama = ollama
    app.state.service = service
    app.state.sync_manager = sync_manager
    app.state.voice_service = voice_service
    app.state.prompt_id = prompt_id
    app.state.mcp_bridge = mcp_bridge
    app.state._owns_ollama = client is None


async def _jellyfin_sync_loop(app: FastAPI) -> None:
    """Background Catalogue Sync: initial if needed, then periodic."""
    sync_mgr: SyncManager = app.state.sync_manager
    settings: Settings = app.state.settings
    if not jellyfin_sync_configured(settings):
        return

    await asyncio.sleep(2.0)
    try:
        if sync_mgr.needs_sync():
            await asyncio.to_thread(sync_mgr.run_sync, force=True)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("initial jellyfin sync failed")

    interval_h = settings.jellyfin.sync_interval_hours
    if interval_h <= 0:
        return

    while True:
        try:
            await asyncio.sleep(interval_h * 3600.0)
            await asyncio.to_thread(sync_mgr.run_sync, force=False)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("periodic jellyfin sync failed")


def create_app(
    settings: Settings | None = None,
    *,
    client: OllamaClient | None = None,
    system_prompt: str | None = None,
    prompt_id: str | None = None,
    data_dir: Path | None = None,
) -> FastAPI:
    """Build a FastAPI app. Pass ``settings``/``client`` to skip production lifespan I/O."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sync_task: asyncio.Task[None] | None = None
        mcp_bridge: McpBridge | None = None
        if getattr(app.state, "service", None) is None:
            cfg = settings or load_config()
            _configure_logging(cfg.runtime.log_level)
            try:
                resolved_data = cfg.runtime.ensure_data_dir()
                mcp_bridge = await McpBridge.connect(
                    cfg,
                    data_dir=resolved_data,
                    loop=asyncio.get_running_loop(),
                )
                _wire_state(
                    app,
                    cfg,
                    client=client,
                    system_prompt=system_prompt,
                    prompt_id=prompt_id,
                    data_dir=data_dir,
                    mcp_bridge=mcp_bridge,
                )
            except PromptError as exc:
                if mcp_bridge is not None:
                    await mcp_bridge.close()
                raise RuntimeError(str(exc)) from exc
            logger.info(
                "brain ready model=%s prompt_id=%s data_dir=%s mcp_unavailable=%s",
                cfg.ollama.model,
                app.state.prompt_id,
                app.state.data_dir,
                mcp_bridge.unavailable if mcp_bridge else [],
            )
        if jellyfin_sync_configured(app.state.settings):
            sync_task = asyncio.create_task(
                _jellyfin_sync_loop(app),
                name="jellyfin-sync-loop",
            )
            app.state._sync_task = sync_task
        try:
            yield
        finally:
            if sync_task is not None:
                sync_task.cancel()
                try:
                    await sync_task
                except asyncio.CancelledError:
                    pass
            sync_mgr: SyncManager | None = getattr(app.state, "sync_manager", None)
            if sync_mgr is not None:
                sync_mgr.close()
            bridge: McpBridge | None = getattr(app.state, "mcp_bridge", None)
            if bridge is not None:
                await bridge.close()
            if getattr(app.state, "_owns_ollama", False):
                app.state.ollama.close()

    application = FastAPI(
        title="Mimir Brain",
        version=__version__,
        lifespan=lifespan,
        description=(
            "Offline personal-assistant brain. Native /v1/chat supports SSE "
            "(docs/api-streaming.md); OpenAI-compat streaming remains 501."
        ),
    )

    application.add_middleware(AuthMiddleware)

    if settings is not None:
        _configure_logging(settings.runtime.log_level)
        _wire_state(
            application,
            settings,
            client=client,
            system_prompt=system_prompt or "You are Mimir, a test assistant.",
            prompt_id=prompt_id or "test:prompt",
            data_dir=data_dir,
        )

    register_chat_routes(application)
    register_voice_routes(application)

    @application.post("/v1/chat/completions")
    def openai_chat_completions(request: Request, body: dict[str, Any]) -> JSONResponse:
        """OpenAI-compatible completions. Tools run inside Mimir; client tools ignored."""
        service: BrainService = request.app.state.service
        status, payload = run_openai_chat(service, body)
        return JSONResponse(status_code=status, content=payload)

    @application.get("/v1/models")
    def openai_models(request: Request) -> dict[str, Any]:
        s: Settings = request.app.state.settings
        return models_list(s.ollama.model)

    return application


app = create_app()
