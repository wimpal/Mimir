# Phase 8b — `/history` resume

**Status: done** — list Conversations API + TUI `/history` picker.

See also: [`CONTEXT.md`](../CONTEXT.md),
[`docs/adr/0004-tui-chat-client.md`](./adr/0004-tui-chat-client.md),
[`docs/phase6-chat.md`](./phase6-chat.md).

## Contract

| Path | Behavior |
|---|---|
| `GET /v1/conversations?limit=` | Conversations with Messages, newest `updated_at` first. Default limit **50**, clamped **1..200**. Empty Conversations omitted. Preview = first user Message (else first Message), truncated ~80 chars. Response: `{ conversations, limit, count }` |
| `GET /v1/conversations/{id}/messages` | Full transcript (unchanged); unknown id → `[]` |
| `POST /v1/chat` + `conversation_id` | History window from SQLite (unchanged) |

Auth: same Bearer rules as other `/v1/*` when `auth.mode: token`.

## Chat client (TUI)

- Launch still starts a **new** Conversation (no auto-resume).
- `/history` (idle only): fetch list → ModalScreen OptionList → Enter resumes; Esc cancels.
- Resume: set `conversation_id`, paint Messages via existing restore path, then chat as usual.
- Caps: picker shows at most 200 Conversations (no pagination this phase).

## Exit checks

1. Chat in Conversation A → `/new` → chat in B → `/history` → pick A → follow-up uses A’s History window
2. Empty DB → `/history` → “No past Conversations.”
3. Esc in picker leaves the current Conversation unchanged
4. Automated:

```powershell
uv run pytest tests/test_db.py tests/test_api.py tests/test_tui_client.py tests/test_memory.py tests/test_auth.py -q
```
