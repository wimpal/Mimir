# Mimir — Offline Personal Assistant

Self-hosted, offline-first chat assistant: a FastAPI **brain** running
**Qwen3 8B via Ollama** with tools (weather, Jellyfin), plus a Textual **TUI**
Chat client. Voice (Home Assistant) is v2 and plugs into the same brain.

Read [`Concept.md`](./Concept.md) for intent and [`ROADMAP.md`](./ROADMAP.md)
for phases. Chat UI: Phase 6 done (TUI — ADR 0004).

## Layout

```
brain/           # FastAPI brain: chat, OpenAI adapter, agent loop, tools
clients/tui/     # Textual Chat client — uv run mimir
config/          # config.example.yaml (copy to config.yaml), system_prompt.md
scripts/         # try_prompt.py, tool_call_suite.py
docs/            # Phase notes (tool-calling, HA spike, streaming contract)
tests/           # pytest
```

## Quickstart

Prerequisites: [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com).

```powershell
# 1. Dependencies
uv sync

# 2. Local config (gitignored) + secrets template
Copy-Item config/config.example.yaml config/config.yaml
Copy-Item .env.example .env
# edit config/config.yaml (lat/long!); set MIMIR_CLIENT_TOKEN in .env and restart brain

# 3. Prove config loads
uv run python -m brain.config

# 4. Standing tool-call suite (>=80%)
uv run python scripts/tool_call_suite.py

# 5. Run the brain (host/port must match config runtime.host / runtime.port)
uv run uvicorn brain.main:app --host 127.0.0.1 --port 8000 --reload

# 6. Chat TUI (auto-starts brain if needed; sends MIMIR_CLIENT_TOKEN when set)
uv run mimir
# optional: uv run mimir --url http://127.0.0.1:8000
```

Health stays open. With `auth.mode: token` (default via `MIMIR_AUTH_MODE=token` in `.env`),
pass a Bearer header on `/v1/*`:

```powershell
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/v1/chat `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $env:MIMIR_CLIENT_TOKEN" `
  -d "{\"message\":\"What time is it on the server?\"}"
```

OpenAI-compatible surface (for Home Assistant later): `POST /v1/chat/completions`,
`GET /v1/models`. See [`docs/ha-conversation-agent.md`](./docs/ha-conversation-agent.md).
Streaming on native chat: [`docs/api-streaming.md`](./docs/api-streaming.md).
Phase 7 harden notes: [`docs/phase7-harden.md`](./docs/phase7-harden.md).
Backup: [`docs/ops-backup.md`](./docs/ops-backup.md).

Turn traces: `data/logs/turns.jsonl` (under `runtime.data_dir`); Host-only
`GET /debug/recent-traces`.

Optional: iterate on the system prompt without the tool loop via
`uv run python scripts/try_prompt.py "…"`.

## Model / GPU (dev box: AMD RX 9070 XT)

```powershell
ollama pull qwen3:8b
ollama run qwen3:8b "say hi"   # warm it up
ollama ps                       # PROCESSOR column must show GPU (ROCm/HIP or Vulkan path)
```

If layers stay on CPU: update AMD drivers, retry, and only then judge model quality.
`qwen3:14b` is the optional middle tier on this 16 GB GPU — try before buying 24 GB
hardware. `qwen3:30b-a3b` is deferred to a 24 GB compute box.

## Config precedence

`MIMIR_*` env vars > `config/config.yaml`; secrets (`JELLYFIN_API_KEY`,
`MIMIR_CLIENT_TOKEN`) come from `.env`/environment only. See `.env.example`.
TUI-only: `MIMIR_BRAIN_URL`, `MIMIR_TURN_TIMEOUT_S`, `MIMIR_STATE_DIR`.
Non-loopback bind requires Auth token — [`docs/adr/0005-non-loopback-requires-auth-token.md`](./docs/adr/0005-non-loopback-requires-auth-token.md).

## Auto-start at login (Windows, T-016)

M4 goal: after PC reboot and login, the brain listens on `:8000` within ~2 minutes
without opening a terminal. **M6** moves production to a Linux compute box; this
Task Scheduler setup is a bridge until then.

### Prerequisites

- [uv](https://docs.astral.sh/uv/) on the user PATH (or in `%USERPROFILE%\.local\bin`)
- `.env` in the repo root with `MIMIR_CLIENT_TOKEN` and `MIMIR_AUTH_MODE=token`
- [Ollama](https://ollama.com) installed

### Install (one time)

```powershell
# From repo root — registers \Heim\ tasks for the current user
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_login_tasks.ps1

# If Ollama is already a Windows service:
powershell -File scripts/install_login_tasks.ps1 -SkipOllama
```

This creates:

| Task | Trigger | Action |
|------|---------|--------|
| `Heim Ollama` | At log on | `scripts/start_ollama_at_login.ps1` (hidden) |
| `Heim Mimir brain` | At log on, delay 30s | `scripts/start_brain_at_login.ps1` (hidden) |

Login tasks run with **no visible console**. Check these logs if something fails:

| Log | Contents |
|-----|----------|
| `data/logs/ollama_login.log` | Ollama login task steps |
| `data/logs/ollama_serve.log` | `ollama serve` stdout/stderr |
| `data/logs/brain_login.log` | Brain login task steps |
| `data/logs/brain_login_cli.log` | `ensure_brain_cli` stdout |
| `data/logs/brain_launch.log` | uvicorn brain process output |

Manual test without rebooting:

```powershell
Start-ScheduledTask -TaskPath '\Heim\' -TaskName 'Heim Mimir brain'
```

Uninstall: `powershell -File scripts/install_login_tasks.ps1 -Uninstall`

### After reboot + login (before opening TUI)

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:11434/api/tags
```

Then `uv run mimir` or `dist\mimir.exe` — chat should work with no manual brain start.

### Manual restart vs login start

| Script | Use when |
|--------|----------|
| `scripts/start_brain_at_login.ps1` | Task Scheduler / idempotent ensure-running |
| `scripts/restart_mimir.ps1` | Pick up config/prompt changes — **kills** the old brain first; bind host comes from `config/config.yaml` (`-Url` is health-check only) |
| TUI `ensure_brain_running` | Backup if Task Scheduler did not run |

### Failure symptoms

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `/health` 200 but chat fails on model | Ollama not ready | Increase login delay; verify Ollama task |
| `/health` unreachable | `uv` not on Task Scheduler PATH | Re-run installer after PATH fix; check `data/logs/brain_launch.log` |
| 401 on chat | `.env` missing token or wrong cwd | Task action Working directory = repo root |
| Two brains / port conflict | TUI + scheduler race | Scheduler runs first; TUI skips if `/health` OK |

## LAN access (M5 mobile)

The brain defaults to loopback (`127.0.0.1`). For a phone or laptop on home Wi‑Fi,
bind all interfaces and enforce bearer auth (T-014 / ADR 0005).

### Prerequisites

- `.env`: `MIMIR_AUTH_MODE=token` and non-empty `MIMIR_CLIENT_TOKEN`
- `config/config.yaml`:

```yaml
auth:
  mode: token
runtime:
  host: 0.0.0.0
  port: 8000
```

- Home Wi‑Fi network profile set to **Private** in Windows (Settings → Network)
- One-time firewall (Private profile, local subnet only) — **Administrator PowerShell**:

```powershell
# Start menu -> Windows PowerShell -> Run as administrator, then:
cd D:\Dev\Projects\Mimir
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_brain_firewall.ps1
```

Discover the PC LAN address: `ipconfig` → IPv4 on the home adapter (e.g.
`192.168.0.x`). Mobile clients use `http://<PC-LAN-IP>:8000` with the same bearer
token as the TUI. **Do not port-forward 8000** to the internet (ADR-004 / M7).

Verify resolved config after edits:

```powershell
uv run python -m brain.config
powershell -File scripts/restart_mimir.ps1 -BrainOnly
```

`-Url` on restart/login scripts is the **client health-check URL** (usually
`http://127.0.0.1:8000`); uvicorn `--host` comes from `runtime.host` in config.

### Acceptance (from another device on home Wi‑Fi)

```powershell
# On the phone/laptop (replace <PC-LAN-IP>):
curl http://<PC-LAN-IP>:8000/health
curl -X POST http://<PC-LAN-IP>:8000/v1/chat -H "Content-Type: application/json" -d "{\"message\":\"hi\"}"
# → 401 without Authorization

# On the PC (use curl.exe in PowerShell):
curl.exe -X POST http://127.0.0.1:8000/v1/chat `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $env:MIMIR_CLIENT_TOKEN" `
  -d "{\"message\":\"hi\"}"
```

The TUI (`uv run mimir` / `dist\mimir.exe`) keeps using loopback (`http://127.0.0.1:8000`)
or `MIMIR_BRAIN_URL` — both work when the brain binds `0.0.0.0`. **Verified 2026-08-29:**
desktop TUI chats normally with `runtime.host: 0.0.0.0` + token auth unchanged.

### Verified (operator household, 2026-08-29)

| Check | Result |
|-------|--------|
| Firewall rule | Installed (`Heim Mimir brain`, Private + LocalSubnet) |
| Phone `GET /health` on LAN | 200, full JSON |
| TUI on PC (`dist\mimir.exe` / `uv run mimir`) | Chat works on loopback |
| Phone `POST /v1/chat` → 401 / bearer chat | Verified 2026-08-29 (Termux) |
| Reboot → LAN without manual steps | Verified 2026-08-29 |
