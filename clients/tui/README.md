# clients/tui — Mimir Chat client

Full-screen Textual TUI (ADR 0004): Claude/Amp-style blend chrome with a
green Yggdrasil splash on empty Conversations.

## Run (dev)

```powershell
# brain already running on :8000 (or let the TUI start it)
uv run mimir
uv run mimir --url http://127.0.0.1:8000
python -m clients.tui
```

Quick “new window” shortcut (still uses uv): double-click
[`scripts/launch_mimir.bat`](../../scripts/launch_mimir.bat).

Full restart (stop brain + TUI, rebuild `dist\mimir.exe`, start both):

```powershell
powershell -File scripts/restart_mimir.ps1
# brain only: powershell -File scripts/restart_mimir.ps1 -BrainOnly
# skip PyInstaller (faster dev): powershell -File scripts/restart_mimir.ps1 -SkipExeBuild
```

Or double-click [`scripts/restart_mimir.bat`](../../scripts/restart_mimir.bat).
Default full restart rebuilds `dist\mimir.exe` then launches it (pinned taskbar shortcut stays current).

## Windows .exe (double-click → terminal + TUI)

```powershell
powershell -File scripts/build_mimir_exe.ps1
```

Uses `clients/tui/assets/mimir.ico` (world-tree, taskbar/Explorer). The window
caption is **Mimir** (system title text — console apps cannot color it).
The in-app header does not repeat the name.

Output: `dist/mimir.exe`. On startup the TUI checks `/health`; if the brain
is down it starts `uv run uvicorn …` from the repo (keep the exe under
`dist/`, or set `MIMIR_REPO_ROOT`). Brain logs: `data/logs/brain_launch.log`.

**T-016 auto-start:** Windows Task Scheduler is the **primary** way the brain
starts after login (`scripts/install_login_tasks.ps1`). The TUI launcher is a
**backup** when `/health` fails — it does not replace scheduled startup.

## UI

- Compact status line (host / conversation) under the window caption
- World-tree splash when the Conversation is empty; `/new` restores it
- Each launch starts a **new** Conversation; `/history` resumes a past one; `/settings` edits Preferences
- Highlighted user strips, one dim tool summary line under the reply, Amp-style input border
- Esc interrupts the current turn while working
- Copy: drag to select then Ctrl+C, or `/copy` / Ctrl+Shift+C for the last reply
  (Ctrl+C no longer quits — use Ctrl+Q, Ctrl+D, or `/quit`)

## Commands

| Input | Action |
|---|---|
| text + Enter | Send chat turn (SSE) |
| `/new` | New Conversation |
| `/history` | Browse and resume a past Conversation |
| `/settings` | View and edit allowlisted Preferences |
| `/copy` | Copy the last assistant reply |
| `/quit` | Exit |
| `/help` | Help |
| Esc | Interrupt in-flight turn (or dismiss `/history` / `/settings`) |

State: `~/.mimir/chat_state.json` (or `MIMIR_STATE_DIR`).
