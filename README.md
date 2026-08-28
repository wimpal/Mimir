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
Copy-Item .env.example .env
# edit config/config.yaml (lat/long!); set MIMIR_CLIENT_TOKEN in .env and restart brain

# 3. Prove config loads
uv run python -m brain.config

# 4. Standing tool-call suite (>=80%)
uv run python scripts/tool_call_suite.py

# 5. Run the brain (host/port must match config runtime.host / runtime.port)
uv run uvicorn brain.main:app --host 127.0.0.1 --port 8000 --reload

# 6. Chat TUI (auto-starts brain if needed; sends MIMIR_CLIENT_TOKEN when set)
uv run mimir
# optional: uv run mimir --url http://127.0.0.1:8000
```

Health stays open. With `auth.mode: token` (default via `MIMIR_AUTH_MODE=token` in `.env`),
pass a Bearer header on `/v1/*`:

```powershell
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/v1/chat `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $env:MIMIR_CLIENT_TOKEN" `
  -d "{\"message\":\"What time is it on the server?\"}"
```

OpenAI-compatible surface (for Home Assistant later): `POST /v1/chat/completions`,
`GET /v1/models`. See [`docs/ha-conversation-agent.md`](./docs/ha-conversation-agent.md).
Streaming on native chat: [`docs/api-streaming.md`](./docs/api-streaming.md).
Phase 7 harden notes: [`docs/phase7-harden.md`](./docs/phase7-harden.md).
Backup: [`docs/ops-backup.md`](./docs/ops-backup.md).

Turn traces: `data/logs/turns.jsonl` (under `runtime.data_dir`); Host-only
`GET /debug/recent-traces`.

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
`MIMIR_CLIENT_TOKEN`) come from `.env`/environment only. See `.env.example`.
TUI-only: `MIMIR_BRAIN_URL`, `MIMIR_TURN_TIMEOUT_S`, `MIMIR_STATE_DIR`.
Non-loopback bind requires Auth token — [`docs/adr/0005-non-loopback-requires-auth-token.md`](./docs/adr/0005-non-loopback-requires-auth-token.md).
