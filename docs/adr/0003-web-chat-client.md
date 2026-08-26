# Minimal web Chat client; not Telegram/Matrix

**Status:** superseded by [ADR 0004](./0004-tui-chat-client.md).

Phase 6 needs a product front door. A Telegram or Matrix bot would pull phone UX forward but adds a cloud/messaging dependency and a second client shape beside the eventual HA voice path. We decided: v1 is a **vanilla** Chat client served by the brain (same-origin), single Conversation with `localStorage` id, `/v1/chat` SSE, and `GET /v1/conversations/{id}/messages` returning the full Conversation for refresh (History window remains model-injection only). No Conversation list, no client auth, no tool-event UI in Phase 6. Telegram/Matrix as a chat front door are out of the roadmap.

**Considered options:** Telegram bot; Matrix bot; framework SPA (React/Svelte) as the Phase 6 default.

