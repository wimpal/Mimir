# Streaming contract (`/v1/chat` SSE)

Native `POST /v1/chat` supports Server-Sent Events when `stream: true`.
OpenAI-compatible `POST /v1/chat/completions` with `stream: true` remains
**HTTP 501** until Assist/voice needs it.

Auth (when Phase 7 enables it) applies the same way for streaming and
non-streaming.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/chat` | Native Mimir chat — JSON or SSE |
| `POST` | `/v1/chat/completions` | OpenAI-compatible; streaming still 501 |

## Behavior

- `stream: false` (default) — JSON `ChatResponseBody` as before.
- `stream: true` — `Content-Type: text/event-stream`; one JSON object per `data:` line.

Do not invent a second streaming URL.

## SSE event types

| `type` | Meaning |
|---|---|
| `meta` | Early turn metadata (`conversation_id` when minted/known) |
| `token` | Assistant text chunk (`text`) — Phase 6 emits after the tool loop finishes (not live Ollama deltas) |
| `tool_start` | Tool about to run (`name`, optional `arguments`) |
| `tool_end` | Tool finished (`name`, `ok`, optional `result_preview`) |
| `done` | Final turn metadata (`stopped_reason`, `tools_used`, `turn_id`, `conversation_id`) |
| `error` | Fail-loud short message (`message`); stream ends. May include `conversation_id` |

Clients should treat unknown event types as ignorable. The Textual Chat client
(`uv run mimir`) shows dim tool lines and streams tokens into the transcript.
Phase 6 tokens are emitted **after** the tool loop finishes (not live Ollama
deltas) — the TUI status line shows “working…” during the quiet window.

## Persistence

User + final assistant Messages (including user-facing error fallbacks) are
written to SQLite **before** `done`, same as non-streaming. Partial tokens are
never persisted.

## OpenAI adapter streaming

`/v1/chat/completions` with `stream: true` returns **501**. Tool execution
remains **server-side** inside Mimir either way.
