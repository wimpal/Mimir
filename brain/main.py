"""Mimir brain — FastAPI service (Phase 0 stub).

Phase 2 turns this into the real brain: chat endpoints, agent loop, tools,
Ollama reachability checks in /health. For now it proves config loads and
the service boots.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from brain import __version__
from brain.config import Settings, load_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fails loud at startup if config is missing/invalid.
    app.state.settings = load_config()
    app.state.settings.runtime.ensure_data_dir()
    yield


app = FastAPI(title="Mimir Brain", version=__version__, lifespan=lifespan)


@app.get("/health")
def health(request: Request) -> dict:
    # Deliberately does NOT ping Ollama yet — reachability checks are Phase 2 semantics.
    s: Settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "mimir-brain",
        "version": __version__,
        "config_loaded": True,
        "single_user": True,
        "ollama": {"url": s.ollama.url, "model": s.ollama.model},
    }
