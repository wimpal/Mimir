# Phase 7 — Reliability, security, observability

**Status:** implemented (see ROADMAP Phase 7 exit criteria).

## What landed

| Area | Behavior |
|---|---|
| Forecast cache | `{data_dir}/cache/weather.json`, TTL `weather.cache_ttl_s` (default 3600). Fresh payloads include `stale: false` + `fetched_at`. On Open-Meteo failure, serve cache within TTL with `stale: true`. |
| Auth | `auth.mode: none` \| `token`. Non-loopback `runtime.host` refuses startup without token (ADR 0005). Bearer on `/v1/*` and `/debug/*` when token mode. |
| `/health` | Always open (no Auth token). |
| Host-only | `POST /v1/jellyfin/sync` and `GET /debug/recent-traces` require loopback client. |
| Debug | `GET /debug/recent-traces?limit=50` — Turn trace summaries only (no message bodies). |
| Personality | Jarvis-led system prompt — [`phase7-personality.md`](./phase7-personality.md). |
| Backup | [`ops-backup.md`](./ops-backup.md). |
| Remote access | Deferred to Phase 11 backlog (local network only this phase). |

## Exit checklist

- [x] Stop Ollama → `/v1/chat` returns a clear brain-side offline reply (covered by unit tests + existing `MSG_OLLAMA_DOWN` path).
- [x] Jellyfin down → `recommend_movies` uses last-good Catalogue or fails clear (Phase 5 behavior retained).
- [x] Open-Meteo down with warm Forecast cache → tool returns `stale: true` (unit tests).
- [x] `runtime.host: 0.0.0.0` without token → brain refuses to start (unit tests / ADR 0005).
- [x] Token mode → chat requires Bearer; `/health` stays open (unit tests).
- [x] Sync or `/debug/recent-traces` from a non-loopback client → 403 (unit tests).
- [x] `uv run python scripts/tool_call_suite.py` ≥80% after prompt change (25/25).

## Manual bind note

`runtime.host` / `runtime.port` are the bind source of truth (`brain_launcher`,
`restart_mimir.ps1`, and Task Scheduler login path read config). Manual uvicorn
must match:

```powershell
# Dev (loopback only)
uv run uvicorn brain.main:app --host 127.0.0.1 --port 8000

# M5 household LAN (requires auth.mode token + MIMIR_CLIENT_TOKEN)
uv run uvicorn brain.main:app --host 0.0.0.0 --port 8000
```

Chat clients connect via `MIMIR_BRAIN_URL` (may be a LAN IP while the process binds `0.0.0.0`).
