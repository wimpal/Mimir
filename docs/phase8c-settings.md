# Phase 8c — `/settings` Preferences UI

**Status: done** — Preferences HTTP API + TUI `/settings` editor.

See also: [`CONTEXT.md`](../CONTEXT.md),
[`docs/adr/0004-tui-chat-client.md`](./adr/0004-tui-chat-client.md),
[`docs/phase4-memory.md`](./phase4-memory.md),
[`docs/phase6-chat.md`](./phase6-chat.md).

## Contract

| Path | Behavior |
|---|---|
| `GET /v1/preferences` | Every allowlisted key in stable order (`favorite_genres`, `tone`). Unset → `value: null`. Response: `{ preferences: [ {key, value} ] }` |
| `PUT /v1/preferences/{key}` | Body `{ "value": "<string>" }`. Same normalize rules as `set_preference` tool. Success → `{ key, value }` with **stored** string form (genres = JSON array string). Unknown key or invalid value → **400**. Bad/missing body → **422** |

Auth: same Bearer rules as other `/v1/*` when `auth.mode: token`.

Not in scope: `config.yaml`, `.env`, Auth token, clear/DELETE.

## Chat client (TUI)

- `/settings` (idle only): GET prefs → ModalScreen list → Enter edits one key → PUT → system note in transcript; Esc cancels.
- Modal owns its Input/Esc (does not use the main chat Input while open).
- Control calls use a short timeout (~10s), not the chat turn timeout.
- Later `/v1/chat` turns reload Preferences into the system prompt (same inject path as tools).

## Exit checks

1. `/settings` → set `tone` → next chat turn’s system prompt includes `tone: …`
2. Esc in picker / edit leaves Preferences unchanged
3. Invalid value → 400; bad body → 422
4. Automated:

```powershell
uv run pytest tests/test_prefs.py tests/test_api.py tests/test_auth.py tests/test_tui_client.py tests/test_memory.py -q
```
