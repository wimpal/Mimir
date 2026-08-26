# Textual TUI replaces web Chat client

Phase 6 shipped a vanilla web UI served by the brain. After daily use, a browser tab felt wrong for a local assistant, and coding-agent-style terminal UIs fit the workflow better. We decided: v1 Chat client is a **full-screen Textual TUI** (`uv run mimir` / `dist/mimir.exe`), against brain URL `MIMIR_BRAIN_URL` / `--url` (default `http://127.0.0.1:8000`). On launch the TUI health-checks the brain and, if down, starts `uv run uvicorn` from the repo (`MIMIR_REPO_ROOT` or discover from cwd/exe path); it does not stop the brain on exit. Each launch starts a **new** Conversation (auto-resume is backlog). The web static client and brain `StaticFiles` mount are removed. Telegram/Matrix remain out of scope.

**Considered options:** keep web + add TUI; streaming REPL without Textual; strict client-only (never spawn brain); auto-restore last Conversation on launch.

**Supersedes:** ADR 0003
