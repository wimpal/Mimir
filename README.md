# Mimir — Offline Personal Assistant

Self-hosted, offline-first chat assistant: a FastAPI **brain** running
**Qwen3 8B via Ollama** with tools (weather, Jellyfin), plus thin clients.
Voice (Home Assistant) is v2 and plugs into the same brain.

Read [`Concept.md`](./Concept.md) for intent and [`ROADMAP.md`](./ROADMAP.md)
for phases. Current phase: **0 — Foundations** (this scaffold).

## Layout

```
brain/           # FastAPI service: config, (later) agent loop + tools + SQLite
clients/chat/    # Thin chat client — placeholder until Phase 6
config/          # config.example.yaml (copy to config.yaml), system_prompt.md
tests/           # pytest
```

## Quickstart

Prerequisites: [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com).

```powershell
# 1. Dependencies
uv sync

# 2. Local config (gitignored) + secrets template
Copy-Item config/config.example.yaml config/config.yaml
# edit config/config.yaml (lat/long!) — optional: Copy-Item .env.example .env

# 3. Prove config loads (exit criterion)
uv run python -m brain.config

# 4. Run the brain
uv run uvicorn brain.main:app --host 127.0.0.1 --port 8000 --reload

# 5. Health check
curl http://127.0.0.1:8000/health
```

## Model / GPU (dev box: AMD RX 9070 XT)

```powershell
ollama pull qwen3:8b
ollama run qwen3:8b "say hi"   # warm it up
ollama ps                       # PROCESSOR column must show GPU (ROCm/HIP or Vulkan path)
```

If layers stay on CPU: update AMD drivers, retry, and only then judge model quality.
`qwen3:30b-a3b` is deferred to the future compute box (won't fit fully in 16 GB).

## Config precedence

`MIMIR_*` env vars > `config/config.yaml`; secrets (`JELLYFIN_API_KEY`,
`MIMIR_AUTH_TOKEN`) come from `.env`/environment only. See `.env.example`.
