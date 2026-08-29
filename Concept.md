# Offline LLM Personal Assistant — v1 Architecture & Tech Stack

Assistant name: **Mimir** (Modular Intelligent Multi-Interface Resource)

**Doc roles:** this file is intent, locked decisions, and rationale.
Sequencing, exit criteria, and hardware locks live in [`ROADMAP.md`](./ROADMAP.md).
Agent working rules: [`AGENTS.md`](./AGENTS.md).

## Goals (v1)

- Answer general questions
- Check local weather
- Recommend movies from a Jellyfin catalogue
- Activated via chat app (v1), voice (v2, Siri/Echo-style)
- Fully offline, self-hosted; no cloud safety layer between the user and the model

### v1 definition of done

A single-user LAN chat client can: ask general questions, get weather, get Jellyfin
recommendations from *this* library, keep history across restart, and degrade
clearly when Ollama/tools are down — without the client talking to Ollama or
Jellyfin directly. Voice is **not** required for v1.

### Non-goals (until ROADMAP says otherwise)

LangChain-class frameworks, Open WebUI as the product UI, vector DB for Jellyfin,
shopping/smart-home/proactive notify, email inbox sync, public-internet exposure,
multi-user/voice-ID. Calendar read is sequenced in ROADMAP Phase 8d (ICS Calendar
feed); Discord is a brain **tool** (Phase 8f), not a Chat client front door.

---

## Inference Layer: Ollama

**Choice: Ollama**, not raw llama.cpp or vLLM.

Reasoning:

- Wraps llama.cpp with GPU offload, model management, and a REST API (native +
  OpenAI-compatible).
- Native tool-calling support — required for weather and Jellyfin.
- vLLM is multi-user throughput tooling — unnecessary ops for a single-user assistant.
- llama.cpp directly remains the fallback for fine-grained control later.

**Model (locked for this hardware):** stock **Qwen3 8B** (`qwen3:8b`, Q4_K_M) on
the current AMD box; optional **Qwen3 14B** on the same 16 GB GPU (middle tier —
try before buying 24 GB hardware); **Qwen3 30B-A3B** only on a later compute box
with **24 GB** VRAM. See ROADMAP hardware + VRAM tables.

**Why Qwen3:** chosen for local tool-calling reliability via Ollama’s tools API.
Do not treat comparative “beats Llama / Gemma on benchmarks” claims as fact
without a dated source — re-verify if swapping models. Phase 1’s scripted suite
is the project’s ground truth for *this* stack.

**“Uncensored” decision (locked):** means **self-hosted + no cloud moderation
proxy**, not an abliterated / “uncensored” fine-tune. Abliterated finetunes often
degrade instruction-following and structured tool calls — the capability we
optimized for. Stay on stock `qwen3:8b` unless a measured suite run justifies a
swap. Personality is **Jarvis-led** (calm competence, dry wit, brief answers);
refusal style and tone live in
[`config/system_prompt.md`](./config/system_prompt.md).

**Thinking mode (locked):** Qwen3 is hybrid. **`think: false` for all tool loops
and for voice.** Chat-only deep reasoning with thinking may be revisited later;
it costs latency/tokens and can interfere with tool-call formatting. Config:
`ollama.think` (default `false`).

**Context window (locked default):** set `ollama.num_ctx` explicitly (default
**8192**). Ollama’s implicit default is often smaller and **silently truncates**
system prompt + history + tool schemas + tool results — which looks like “forgot”
or “dropped a tool call”. Raise only after checking VRAM (`ollama ps`).

---

## Orchestration: Custom, not LangChain

For a handful of tools (chat, weather, Jellyfin), a heavy agent framework adds
abstraction we don’t need.

Build a small **FastAPI** “brain” that:

1. Receives the user message
2. Sends it to Ollama with tool schemas
3. If the model returns a tool call, executes it and feeds the result back
4. Returns the final response to whatever client called it

Keep the loop readable in a sitting (`brain/agent.py` + `brain/ollama.py` +
tools). Understanding every step beats opaque framework magic when debugging
bad tool calls.

---

## Tools

### Weather

- **Open-Meteo** (no API key) as the HTTP transport; for Netherlands home
  coords pin **KNMI HARMONIE AROME** (`knmi_harmonie_arome_netherlands`, 2 km).
- Lat/long + `Europe/Amsterdam` timezone in config. Soft network dependency —
  degrade clearly when offline; Phase 7 serves a TTL **Forecast cache** (marked
  `stale`) when Open-Meteo is unreachable.
- Buienradar-style 5-minute rain nowcast is Phase 11 backlog if needed.

### Jellyfin

- **Jellyfin REST API** → SQLite Catalogue (movies from allowlisted libraries;
  titles, genres, overview, director, cast, ratings, watch state for a configured
  Jellyfin user). Sync is owned by the **brain**, never by Ollama. Paginate;
  auth via API key from env (`X-Emby-Token`). On-demand HTTP + daily periodic Sync;
  atomic generation publish so a failed Sync keeps the last-good Catalogue.
- v1: `recommend_movies` filters a small Catalogue subset (seed-title overlap per
  ADR 0002); LLM picks. Embeddings / `sqlite-vec` only after catalogue stuffing
  fails (~1000+ titles or bad relevance). Series and playback control are out of
  scope for v1.

---

## Memory / State (Data)

SQLite under a configurable `data_dir`: history, preferences, Jellyfin cache,
optional tool logs.

| Topic | v1 policy |
|---|---|
| Migrations | Hand-rolled versioned SQL (`schema_version`); Catalogue Box sets at version 4 — no Alembic unless pain forces it |
| Backup | Copy/stop-and-copy `data_dir` (SQLite + logs + cache); see [`docs/ops-backup.md`](./docs/ops-backup.md) |
| Retention | Keep full history for single-user v1; no auto-prune unless disk hurts |
| Context injection | Last N Message pairs (`memory.history_pairs`, default 20) under `num_ctx`. Token-budget window and summarization/compaction are backlog until long threads actually break |
| Chat memory path | Mimir `/v1/chat` owns SQLite Conversations; OpenAI-compat stays client `messages` only until Assist needs shared threads (ADR 0001) |
| Preferences | Allowlisted keys via tools + TUI `/settings` + system-prompt inject; HTTP `GET/PUT /v1/preferences`; Jellyfin watch/likes are media state, not Preference rows |

---

## Chat Frontend (v1)

Full-screen Textual TUI Chat client (`uv run mimir` / `dist/mimir.exe`) — thin
HTTP client only (ADR 0004). Health-checks the brain and starts it if needed
(`uv run uvicorn` from the repo); does not stop it on exit. Each launch opens a
new Conversation; `/history` resumes a past one; `/settings` edits allowlisted Preferences. Telegram/Matrix bots are not a v1 path.
Discord is a send tool with a Channel allowlist (ADR 0006), not a chat front door.
The Phase 6 web UI was superseded.

**Network / auth:** Default bind is loopback with no Auth token. Opening
`runtime.host` past loopback requires `auth.mode: token` and `MIMIR_CLIENT_TOKEN`
(ADR 0005) — refuse startup otherwise. LAN clients may call chat with Bearer;
Sync and debug traces stay Host-only. No public-internet exposure; remote VPN
docs are backlog.

Open WebUI is fine for temporary debugging, not the long-term product UI: voice
must call the same brain without rewriting chat-specific logic.

---

## Architecture Diagram

```
[Chat client] ──HTTP──▶ [FastAPI "brain" service] ──▶ [Ollama + Qwen3]
                              │
                              ├── tool calls ──▶ [Open-Meteo API]
                              ├── tool calls ──▶ [Jellyfin API]
                              ├── tool calls ──▶ [Calendar feed ICS URL]  (Phase 8d)
                              ├── tool calls ──▶ [Discord API]           (Phase 8f)
                              ├── sync job   ──▶ [Jellyfin API]  (brain-owned)
                              └──▶ [SQLite: history, prefs, cached catalogue]
```

Ollama never talks to Jellyfin or Open-Meteo. The brain executes tools and runs sync.

---

## Path to Voice (v2)

Use **Home Assistant Assist** + **Wyoming** (wake → STT → conversation agent → TTS),
not a custom voice stack.

Components (see ROADMAP Phase 10 for sequencing):

- Living-room satellite: HA Voice Preview Edition (or Atom Echo / phone / Pi+ReSpeaker)
- STT: faster-whisper or whisper.cpp; TTS: Piper
- Conversation agent → **Mimir brain** (not HA’s native Ollama integration)

**Load-bearing assumption — verify before Phase 10 (docs spike in Phase 2):**
HA must be able to call **our** OpenAI-compatible (or custom) conversation endpoint
so tool calling stays in the brain. Historically, the built-in OpenAI Conversation
integration may not accept an arbitrary base URL without a custom integration;
HA’s **native Ollama** conversation agent would bypass the brain and drop our
tools. Do not discover this at voice bring-up.

### Voice architecture

```
[Voice PE / satellite] ──Wyoming──▶ [Home Assistant]
                                         │
                              Assist: STT → conversation agent → TTS
                                         │
                                         ▼
                              [FastAPI brain] ──▶ Ollama / weather / Jellyfin
```

---

## Hardware

**Locked now:** Windows dev box, **AMD RX 9070 XT 16 GB**, Qwen3 8B (14B optional).
**Later:** separate Linux compute box — **14B** on a 16 GB GPU tier, or **30B-A3B**
on a 24 GB tier.

Living-room vs compute stay decoupled: pretty satellite in the room, GPU in a
closet. Detailed SKUs, VRAM tables, AMD/ROCm notes, and Windows→Linux portability
are owned by [`ROADMAP.md`](./ROADMAP.md) — do not duplicate shopping lists here.

---

## Build order

Owned by [`ROADMAP.md`](./ROADMAP.md). Short form:

1. Prove Ollama tool-calling (dummy tools + standing suite)
2. FastAPI brain + hardening
3. Weather → memory → Jellyfin
4. Thin chat UI → package → voice last
