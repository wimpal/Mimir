# Mimir — Offline Personal Assistant

Self-hosted, offline-first chat assistant: a FastAPI **brain** running
**Qwen3 8B via Ollama** with tools (weather, Jellyfin), plus a Textual **TUI**
Chat client. Voice (Home Assistant) is v2 and plugs into the same brain.

Read [`Concept.md`](./Concept.md) for intent and [`ROADMAP.md`](./ROADMAP.md)
for phases. Chat UI: Phase 6 done (TUI — ADR 0004).

## Layout

```
brain/           # FastAPI brain: chat, OpenAI adapter, agent loop, tools
clients/tui/     # Textual Chat client — uv run mimir
config/          # config.example.yaml (copy to config.yaml), system_prompt.md
scripts/         # try_prompt.py, tool_call_suite.py
docs/            # Phase notes (tool-calling, HA spike, streaming contract)
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

# 3. Prove config loads
uv run python -m brain.config

# 4. Standing tool-call suite (>=80%)
uv run python scripts/tool_call_suite.py

# 5. Run the brain
uv run uvicorn brain.main:app --host 127.0.0.1 --port 8000 --reload

# 6. Chat TUI (auto-starts brain if needed)
uv run mimir
# optional: uv run mimir --url http://127.0.0.1:8000

# Windows: double-clickable console exe (build once)
# powershell -File scripts/build_mimir_exe.ps1
# then run dist\mimir.exe
```

Health + curl still work:

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/chat -H "Content-Type: application/json" -d "{\"message\":\"What time is it on the server?\"}"
```

OpenAI-compatible surface (for Home Assistant later): `POST /v1/chat/completions`,
`GET /v1/models`. See [`docs/ha-conversation-agent.md`](./docs/ha-conversation-agent.md).
Streaming on native chat: [`docs/api-streaming.md`](./docs/api-streaming.md).

Turn traces: `data/logs/turns.jsonl` (under `runtime.data_dir`).

Optional: iterate on the system prompt without the tool loop via
`uv run python scripts/try_prompt.py "…"`.

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
TUI-only: `MIMIR_BRAIN_URL`, `MIMIR_TURN_TIMEOUT_S`, `MIMIR_STATE_DIR`.
