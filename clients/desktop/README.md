# Mimir desktop (Tauri 2)

Windows **daily-driver** chat client. Thin front door to the brain — same HTTP/SSE
API as the Textual TUI and Android app. Native Enter / Shift+Enter; TUI-like olive
chrome and slash commands.

The Textual TUI (`clients/tui/`) remains for SSH / headless use.

## Requirements

- [Node.js](https://nodejs.org/) + npm
- [Rust](https://rustup.rs/) (stable)
- Running Mimir brain on `http://127.0.0.1:8000` (or set URL via `/connect`)
- Bearer token = `MIMIR_CLIENT_TOKEN` (same as TUI/mobile)
- Optional auto-start: `uv` on PATH and a checkout of this repo (`MIMIR_REPO_ROOT` if needed)
- Mic: browser permission for microphone (Windows will prompt)

## Dev

```powershell
cd clients/desktop
npm install
npm run tauri dev
```

Or from the repo root (brain + GUI):

```powershell
powershell -File scripts/restart_mimir.ps1
```

First launch opens **`/connect`** — paste brain URL + token. Token is stored in
**Windows Credential Manager**. Each launch starts a **fresh** conversation
(TUI policy); use `/history` to resume.

### Pinable `.exe` (taskbar)

```powershell
# From repo root — builds release + copies to a stable path:
powershell -File scripts/build_mimir_desktop_exe.ps1
```

Pin **`dist\mimir-desktop.exe`** (Yggdrasil tree icon, same as the TUI). The
restart script prefers that path, then `src-tauri\target\release\…`, else
`npm run tauri dev`.

To refresh icons from the TUI asset:

```powershell
cd clients/desktop
npm run tauri -- icon ..\tui\assets\mimir-icon.png
```

## Keys & commands

| Input | Action |
|---|---|
| Enter | Send |
| Shift+Enter | Newline |
| Esc | Cancel recording → close modal → detach stream |
| mic | Click to record; click again to STT → send |
| `/new` | New conversation |
| `/history` | Resume a past conversation |
| `/settings` | Brain preferences (`GET/PUT /v1/preferences`) |
| `/connect` | Brain URL + bearer token |
| `/copy` | Copy last assistant reply |
| `/help` | Command list |
| `/quit` | Close window |

## How it differs from the TUI

| | Desktop GUI | Textual TUI |
|---|---|---|
| Keys | Native webview | Terminal-owned |
| Brain I/O | Rust `reqwest` (no CORS) | Python `httpx` |
| Look | TUI olive/green chrome + splash | Same language |
| Confirm writes | Confirm / Cancel buttons | Type `ja` / `yes` |
| History | Starts fresh; `/history` to resume | Starts fresh; `/history` to resume |
| Mic | Webview capture → `/v1/stt` | ffmpeg dshow |

## Brain calls (via Rust)

- `GET /health`
- `POST /v1/chat` (`stream: true`) SSE
- `GET /v1/conversations` + messages
- `GET/PUT /v1/preferences`
- `POST /v1/stt`

No MCP, Ollama, Homebase, or BudgetTracker calls from this app.

## Acceptance checklist (manual)

- [ ] Enter sends; Shift+Enter inserts newline
- [ ] Slash commands work without top-bar buttons
- [ ] Mic record → STT → chat turn; Esc cancels recording
- [ ] `/settings` edits a preference
- [ ] Paste a full Dutch recipe — all lines reach the brain
- [ ] Write confirm → Confirm → succeeds
