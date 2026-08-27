# Textual TUI replaces web Chat client

Phase 6 shipped a vanilla web UI served by the brain. After daily use, a browser tab felt wrong for a local assistant, and coding-agent-style terminal UIs fit the workflow better. We decided: v1 Chat client is a **full-screen Textual TUI** (`uv run mimir` / `dist/mimir.exe`), against brain URL `MIMIR_BRAIN_URL` / `--url` (default `http://127.0.0.1:8000`). On launch the TUI health-checks the brain and, if down, starts `uv run uvicorn` from the repo (`MIMIR_REPO_ROOT` or discover from cwd/exe path); it does not stop the brain on exit. Each launch starts a **new** Conversation; resume is explicit via `/history` (Phase 8b), not auto-restore on launch. Allowlisted Preferences are edited via `/settings` (Phase 8c) against `GET/PUT /v1/preferences` — not `config.yaml` / `.env`. The web static client and brain `StaticFiles` mount are removed. Telegram/Matrix remain out of scope. Discord is a brain send tool later (ADR 0006), not a Chat client.

**Considered options:** keep web + add TUI; streaming REPL without Textual; strict client-only (never spawn brain); auto-restore last Conversation on launch (rejected for v1 — use `/history`).

**Supersedes:** ADR 0003
