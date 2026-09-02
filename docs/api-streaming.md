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
| `token` | Live Ollama content delta on the **final** assistant generation (after any tool loop) |
| `sentence` | Speakable sentence completed (`index` 0-based, `text`). Voice clients should start TTS per sentence. |
| `tool_start` | Tool about to run (`name`, optional `arguments`) |
| `tool_end` | Tool finished (`name`, `ok`, optional `result_preview`) |
| `done` | Final turn metadata (`stopped_reason`, `tools_used`, `turn_id`, `conversation_id`) |
| `error` | Fail-loud short message (`message`); stream ends. May include `conversation_id` |

Event order: `meta` → `tool_start`/`tool_end`* → `token`* → `sentence`* → `done` | `error`.

Clients should treat unknown event types as ignorable. The Textual Chat client
(`uv run mimir`) shows dim tool lines and streams tokens into the transcript.
During tool execution the stream is quiet (no tokens or sentences).

### Tool-heavy turns

1. **First Ollama pass** — blocking `chat()` with tool schemas (tool calls are not
   streamed).
2. **`tool_start` / `tool_end`** — one pair per tool execution; stream is quiet.
3. **Final pass** — tools omitted from the Ollama request; live `token` and `sentence`
   events may follow.

Brain-side fixups (e.g. compound NL weather + shopping list) may adjust the final
reply text; clients should still render streamed `token` events and match `done`.

**Operator verified (2026-09-02):** chat prompt *"Vertel me in twee zinnen wat het weer
is en wat op de boodschappenlijst staat."* — `get_weather` + `homebase.shopping_list.list`,
Dutch reply, list grounded to kaas/melk.

### Voice clients (T-029)

On push-to-talk, call `POST /v1/tts` for each `sentence` event while the chat
SSE continues. Play audio in `index` order; parallel TTS requests are fine.
Cancel playback on new PTT or `error`. Typed chat may ignore `sentence` events.

## Persistence

User + final assistant Messages (including user-facing error fallbacks) are
written to SQLite **before** `done`, same as non-streaming. Partial tokens are
never persisted.

## OpenAI adapter streaming

`/v1/chat/completions` with `stream: true` returns **501**. Tool execution
remains **server-side** inside Mimir either way.
