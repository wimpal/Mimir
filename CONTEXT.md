# Mimir

Offline personal assistant: a brain that chats, calls tools, and remembers for a single user.

## Language

**Conversation**:
A single-user thread of Messages identified by an id the brain persists. Omitting the id mints a new one when persisting; a supplied unknown id creates that Conversation on write. Persist path on `/v1/chat` always requires `message`. `messages`-only without an id stays stateless (no SQLite). With an id, `messages` without `message` is rejected. OpenAI-compat stays client-history-only in Phase 4.
_Avoid_: Session, chat, thread (as API vocabulary)

**Message**:
A persisted user utterance or final assistant reply in a Conversation. Tool-call and tool-result intermediates are not Messages.
_Avoid_: Turn (reserved for observability traces), history entry

**Preference**:
A durable key/value fact about the user, drawn from a small allowlist. The brain injects known Preferences into model context and exposes get/set via tools.
_Avoid_: Setting, config, memory (as a synonym for prefs)

**History window**:
The last N Message pairs (user + final assistant) from a Conversation injected into the model. Full history stays in SQLite; only the window is sent.
_Avoid_: Context, memory window, summarization (backlog — not Phase 4)

**Movie**:
A film from an allowlisted Jellyfin movies library, cached by the brain with metadata and the configured Jellyfin user's watch state. Series and other media types are out of scope for v1.
_Avoid_: Title (ambiguous), item, media, show

**Catalogue**:
The brain's SQLite cache of Movies for the configured Jellyfin user. Sync refreshes it; recommendation tools read it, not live Jellyfin.
_Avoid_: Library (Jellyfin-side), index, collection

**Sync**:
The brain-owned refresh of the Catalogue from Jellyfin (on-demand and periodic). Idempotent upserts; records last-run status.
_Avoid_: Import, crawl, scrape

**Catalogue subset**:
The short filtered list of Movies a recommendation tool returns from the Catalogue (tens of rows, summarized) so the model can pick and explain — not a scored recommender ranking and not the full library.
_Avoid_: Recommendations list, search results, top-N

**Seed title**:
A Movie already in the Catalogue used as the reference for “something like X” filters (overlapping genres and similar metadata). If the seed is missing, the tool says so and falls back to explicit genre/mood filters.
_Avoid_: Query title, similar-to, reference film

**Watched**:
A Movie the configured Jellyfin user has marked played/completed. Partial progress is not Watched; in-progress Movies stay eligible for `unwatched_only` and may be surfaced when they match the request.
_Avoid_: Seen, started

**Chat client**:
The thin Textual TUI (`uv run mimir` / `dist/mimir.exe`) that talks only to the brain over HTTP. It may start the brain if `/health` fails, but holds no business logic and never calls Ollama, Jellyfin, or weather APIs directly. Each launch opens a new Conversation.
_Avoid_: Open WebUI (as the product UI), web UI (superseded), bot, frontend with tools/prompts

**Auth token**:
The shared secret a client must send to the brain when auth is enabled. Single-user; not a login system.
_Avoid_: Password, API key (reserved for Jellyfin), session, user account

**Forecast cache**:
The brain's last successful weather payload, kept under a TTL and returned when Open-Meteo is unreachable. Distinct from the Catalogue (Jellyfin).
_Avoid_: Weather history, offline weather, circuit breaker

**Turn trace**:
A JSONL observability record of one agent loop (prompt id, tools, latencies, success/fail). Not a Message; not full prompt or reply text by default.
_Avoid_: Log line, debug dump, conversation history

**Host-only**:
Reachable only on the machine running the brain (loopback bind), not from other LAN devices. Used for operational endpoints such as Sync and debug traces.
_Avoid_: Localhost-only as a synonym for “LAN”, admin network
