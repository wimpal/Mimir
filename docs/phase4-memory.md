# Phase 4 — Memory & preferences

**Status: done** — SQLite Conversations/Messages + allowlisted Preferences on `/v1/chat`.

See also: [`CONTEXT.md`](../CONTEXT.md), [`docs/adr/0001-chat-memory-vs-openai-compat.md`](./adr/0001-chat-memory-vs-openai-compat.md).

## Contract

| Path | Behavior |
|---|---|
| `POST /v1/chat` + `message` | Persist turn; mint `conversation_id` if omitted; create-on-write if unknown |
| `conversation_id` without `message` | **400** |
| `messages` only, no id | Stateless (no SQLite) — scripts / OpenAI-shaped clients |
| `POST /v1/chat/completions` | Client `messages` only — **no** SQLite memory (ADR 0001) |

Persisted **Messages** = user text + final assistant reply (including user-facing error strings). Tool-loop intermediates stay in JSONL `turn_log`.

**History window:** last `memory.history_pairs` (default **20**) user+assistant pairs from SQLite; full history retained.

**Preferences:** allowlist `favorite_genres`, `tone`. Tools `get_preference` / `set_preference`; HTTP `GET/PUT /v1/preferences` (TUI `/settings`, Phase 8c — see [`phase8c-settings.md`](./phase8c-settings.md)); inject a “Known preferences” block into the system prompt; refresh mid-turn after a successful set.

## Schema

`schema_version` → **1** via hand-rolled migration from 0:

- `conversations` — id, timestamps
- `messages` — conversation_id FK, role, content, created_at
- `preferences` — key/value

## Wiring

- `brain/db.py` — migrations + persistence API
- `brain/prefs.py` — allowlist, normalize, format inject
- `brain/tools/preferences.py` — get/set tools
- `brain/service.py` — persist vs stateless branches; `after_tool` prefs refresh
- `memory.history_pairs` in config (+ `MIMIR_HISTORY_PAIRS`)

## Exit checks

1. Restart keeps history (same `conversation_id` → prior Messages in Ollama context)
2. Prefs influence a follow-up (system inject contains stored genres/tone)
3. Empty DB migrates to `schema_version=1`

Automated coverage: `tests/test_db.py`, `tests/test_memory.py`, `tests/test_prefs.py`.

Pinned suite cases `pref_1`…`pref_3` in `scripts/tool_call_suite.py`. Re-run after model / prompt / schema / `num_ctx` / `think` changes:

```powershell
uv run python scripts/tool_call_suite.py
```

## Curl sketch

```powershell
# Mint conversation
curl -s http://127.0.0.1:8000/v1/chat -H "Content-Type: application/json" `
  -d "{\"message\":\"hello\"}"

# Continue (paste conversation_id from previous reply)
curl -s http://127.0.0.1:8000/v1/chat -H "Content-Type: application/json" `
  -d "{\"message\":\"what did I just say?\",\"conversation_id\":\"<id>\"}"
```
