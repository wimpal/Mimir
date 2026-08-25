# clients/chat — placeholder

Thin chat client for Mimir. Not built yet: ROADMAP Phase 6 picks the form
(minimal web chat vs Telegram/Matrix bot).

## Contract (locked by AGENTS.md)

- Talks **only** to the FastAPI brain over HTTP (`POST /v1/chat`, Phase 2).
- Configuration = the brain base URL. Nothing else.
- No business logic here: no direct Ollama/Jellyfin/Open-Meteo calls,
  no prompt assembly, no history ownership — the brain owns all of it.
- Must surface brain error states (offline / tool failure) instead of hiding them.
