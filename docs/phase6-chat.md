# Phase 6 — Chat client + `/v1/chat` SSE

**Status: done** — Textual TUI Chat client (ADR 0004); native SSE on `/v1/chat`;
Messages GET for future resume; web static UI removed.

See also: [`CONTEXT.md`](../CONTEXT.md),
[`docs/adr/0004-tui-chat-client.md`](./adr/0004-tui-chat-client.md),
[`docs/api-streaming.md`](./api-streaming.md).

## Contract

| Path | Behavior |
|---|---|
| `POST /v1/chat` `stream:false` | JSON reply |
| `POST /v1/chat` `stream:true` | SSE: `meta` → `tool_*` → `token`+ → `done` / `error` |
| `GET /v1/conversations` | List Conversations (preview + timestamps); empty rows omitted |
| `GET /v1/conversations/{id}/messages` | Full Conversation Messages; unknown id → `[]` (no create) |
| `POST /v1/chat/completions` `stream:true` | Still **501** |

## Chat client (TUI)

- Entry: `uv run mimir`, `python -m clients.tui`, or `dist/mimir.exe`
- Base URL: `--url` / `MIMIR_BRAIN_URL` (default `http://127.0.0.1:8000`)
- On launch: `GET /health`; if brain down, start `uv run uvicorn` from the repo
  (`MIMIR_REPO_ROOT` or discover); brain stays up after TUI exit
- Each launch starts a **new** Conversation; `/history` resumes a past one (Phase 8b); `/settings` edits Preferences (Phase 8c); `/new` clears the current Conversation
- Slash commands: `/new`, `/history`, `/settings`, `/quit`, `/help`; Esc interrupts in-flight turn
- Blend chrome: world-tree splash, bordered input, tool cards, green accent
- Tokens may arrive as a post-tool-loop burst (brain contract)

## Exit checks

1. TUI chat → weather / movie recs (with live Ollama + tools)
2. Launch with brain down → auto-start succeeds (needs `uv` + repo)
3. Fresh Conversation + splash each launch; `/new` returns to splash
4. Automated: SSE + Messages GET on brain; TUI client unit tests

```powershell
uv run pytest tests/test_api.py tests/test_tui_client.py tests/test_memory.py -q
```

```powershell
uv run mimir
# or: dist\mimir.exe
```
