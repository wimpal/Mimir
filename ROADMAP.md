# Mimir Implementation Roadmap

**North star (v1):** Fully offline, self-hosted chat assistant (Qwen3 via Ollama) with weather + Jellyfin recommendations, FastAPI “brain” as the only integration surface so voice (v2) can plug in later without rewriting the core.

Source of truth for product intent: [`Concept.md`](./Concept.md).

---

## Hardware (locked for this phase of work)

| Role | Hardware | Model choice |
|---|---|---|
| **Dev machine (now)** | AMD Radeon RX **9070 XT 16 GB** | **Qwen3 8B** Q4_K_M — fits easily (~6–7 GB); leave headroom for long context / tool loops |
| **Compute box (later)** | Separate hidden box (e.g. 24 GB NVIDIA or Mac mini) | Optional upgrade to **Qwen3 30B-A3B**; brain/API unchanged |

**Why not 30B-A3B on the 9070 XT:** Q4_K_M needs ~18–21 GB — it won’t fit fully in 16 GB without a lower quant or CPU offload (slower, worse for iteration). Use 8B until the dedicated box exists.

**AMD note:** Ollama on Windows + AMD uses the ROCm/HIP path (or Vulkan depending on Ollama build). Verify GPU offload with `ollama ps` after the first run — if layers stay on CPU, fix the AMD runtime before judging model quality.

### Windows → Linux compute box

Yes — the eventual compute box will almost certainly be **Linux** (or HA OS / Debian-based). The stack is already portable; the switch is easy if we avoid a few Windows-only traps while developing here.

| Layer | Portability |
|---|---|
| FastAPI brain + SQLite | Trivial — pure Python; use pathlib, no `C:\...` hardcoding |
| Config | Env vars / YAML relative to the app or `/data` volume — not machine-specific paths |
| Chat client | HTTP only — no change |
| Ollama | Same REST API on both OSes; **GPU setup differs** (this PC: AMD; compute box: likely NVIDIA CUDA) |
| Docker Compose | Preferred deploy path on Linux; use it on Windows too when practical so compose is the source of truth |
| Voice / Home Assistant | Linux-native; never required on the Windows dev box |

**Do now (cheap):**
- Write the brain as OS-agnostic Python
- Put runtime data (SQLite, logs) under a configurable data dir
- Target `docker-compose.yml` as the Linux deploy unit even if you run Ollama natively on Windows during early GPU debugging

**Do later (at move time):**
- Install Ollama (or compose service) on Linux + pull the same model tag
- Point clients / HA at the new host IP
- Re-verify GPU offload (`ollama ps`) — different vendor stack, same API

**Bottom line:** No need to dual-boot or develop in WSL for v1. Develop on Windows; keep the brain container-friendly. Moving is mostly “install Ollama + compose up + copy config/SQLite,” not a rewrite.

---

## Phase 0 — Foundations & decisions (½–1 day)

Lock choices before code so schema and APIs don’t thrash later.

| Decision | Status |
|---|---|
| Inference | Ollama + **Qwen3 8B** on 9070 XT (30B-A3B deferred to compute box) |
| Orchestration | Custom FastAPI loop (no LangChain) |
| Storage | SQLite: history, prefs, Jellyfin cache |
| Clients | Thin chat UI first; brain stays OpenAI-compatible / HTTP-agnostic |
| Profiles | **Decide now:** single-user vs multi-profile (affects prefs + Jellyfin schema) |
| Personality | Draft system prompt (tone, “I don’t know,” clarifying questions) |

**Deliverables**
- Repo skeleton: `brain/`, `clients/chat/`, `config/`, `docker-compose.yml` (stubs OK)
- `config.yaml` / `.env.example`: Ollama URL, lat/long, Jellyfin URL + API key placeholders, auth mode
- System prompt v0 checked into repo
- Explicit call: single-profile for v1 unless multi-user is required day one

**Exit criteria:** Config loads; docs state single- vs multi-user; 8B runs on GPU on this PC (`ollama ps` shows GPU).

---

## Phase 1 — Ollama + tool-calling proof (1–2 days)

Validate the model before building product logic.

**Tasks**
1. Install Ollama; pull Qwen3 (start `qwen3:8b` or equivalent quantized tag).
2. Smoke-test chat via Ollama HTTP API.
3. Register a **dummy tool** (e.g. `get_server_time` / `echo`) and confirm the model emits correct tool-call JSON and can use the tool result in a final answer.
4. Measure latency and failure modes (malformed JSON, skipped tools, loops).

**Exit criteria:** ≥~80% success on a small scripted tool-call suite; known failure modes documented. If Qwen3 misbehaves badly, swap model *before* wiring real tools.

---

## Phase 2 — FastAPI brain core (2–4 days)

The reusable “brain” every client will call.

**API shape (suggested)**
- `POST /v1/chat` — message(s) in → assistant reply out (streaming optional later)
- `GET /health` — Ollama reachable + DB ok
- OpenAI-compatible `POST /v1/chat/completions` (or thin adapter) so Home Assistant can use it in v2

**Agent loop**
1. Load system prompt + recent history from SQLite
2. Call Ollama with tool schemas
3. If tool call → execute → append tool result → call again (cap iterations, e.g. 3–5)
4. Persist turn; return final text

**Hardening in this phase (don’t defer)**
- Timeouts on Ollama and every tool
- Graceful “brain’s offline / tool unavailable” messages (no hang)
- Structured logging: prompt id, tool name, latency, success/fail (file or SQLite)

**Exit criteria:** Dummy tool works end-to-end through FastAPI; timeouts and offline paths verified; logs show tool traces.

---

## Phase 3 — Weather tool (1–2 days)

First real external tool; keeps the loop simple.

**Tasks**
1. Tool schema: `get_weather(location?)` — default lat/long from config; optional place name later.
2. Client for [Open-Meteo](https://open-meteo.com/) (no API key).
3. Normalize response for the LLM (temp, conditions, precip, short forecast) — small JSON, not raw API dump.
4. Prompt examples: “weather today,” “will it rain this evening.”

**Offline note:** Open-Meteo needs network. For “fully offline” days, either cache last forecast or reply that weather is unavailable. Document that weather is the one soft dependency.

**Exit criteria:** Chat questions return sensible weather; unreachable API fails fast with a clear reply.

---

## Phase 4 — Memory & preferences (1–2 days)

SQLite before Jellyfin so history and prefs have a home.

**Schema (v1 sketch)**
- `conversations` / `messages` — roles, content, timestamps, optional `conversation_id`
- `preferences` — key/value (favorite genres, tone, default location)
- `tool_logs` — optional observability
- If multi-profile: `users` + FK everywhere

**Behavior**
- Persist every chat turn
- Inject last N turns (or token budget) into context
- Simple preference get/set (tool or admin endpoint) — enough for “I like sci-fi”

**Exit criteria:** Restart keeps history; prefs influence a follow-up reply.

---

## Phase 5 — Jellyfin sync + recommendation tool (3–6 days)

Core media feature; keep v1 intentionally dumb.

### 5a. Sync job
- Auth to Jellyfin REST API
- Periodic (or on-demand) sync into SQLite:
  - titles, year, genres, cast/crew (as needed), ratings
  - watch history / played state / progress
- Idempotent upserts; sync status + last-run time

### 5b. Recommendation tool (no vector DB yet)
Tool e.g. `recommend_movies(mood?, genre?, unwatched_only?)` that:
1. Filters SQLite (unwatched, genre prefs, rating floor)
2. Returns a **small curated subset** (tens of rows, summarized)
3. Lets the LLM pick and explain — not a full recommender engine

### 5c. Defer until it hurts
Embeddings + `sqlite-vec` / Chroma only when catalogue stuffing / filter quality breaks (~1000+ titles or bad relevance).

**Exit criteria:** Sync fills SQLite; “something like Blade Runner I haven’t seen” returns plausible titles from *your* library; Jellyfin down → timeout + clear message.

---

## Phase 6 — Chat frontend (2–4 days)

Thin client only — no business logic in the UI.

**Options (pick one for v1)**
- Minimal web chat (React/Svelte/vanilla) against `POST /v1/chat`
- Telegram or Matrix bot calling the same API

**Must-haves**
- Conversation list or single-thread UX
- Streaming if the brain supports it (nice-to-have)
- Error states mirroring brain offline / tool failure
- Config: brain base URL only

**Must-nots**
- No direct Ollama/Jellyfin calls from the client
- No Open WebUI as the long-term path (fine for temporary debugging)

**Exit criteria:** Full user journey works from UI: chat → weather → movie recs → history survives refresh.

---

## Phase 7 — Reliability, security, observability (2–3 days)

Concept’s “beyond core” items before voice.

| Area | Work |
|---|---|
| Reliability | Global timeouts; circuit-breaker or short cache for flaky tools; `/health` for clients |
| Security | LAN-only bind and/or basic auth / API token; no public bind by default |
| Remote access | Document Tailscale/WireGuard only — no port-forward recipe as default |
| Observability | Prompt/tool/latency logs queryable; maybe a simple `GET /debug/recent-traces` on LAN |
| Personality | Iterate system prompt from real chats |

**Exit criteria:** Kill Ollama/Jellyfin → assistant responds usefully; unauthenticated WAN exposure is not the default.

---

## Phase 8 — Deployment packaging (1–2 days)

**Tasks**
- `docker-compose.yml`: Ollama (or document host GPU Ollama), brain, optional chat static server
- Volume mounts for SQLite + model cache
- README: hardware paths (3060 12GB / 3090 / Mac mini), first-boot steps
- One-command health check script

**Exit criteria:** Fresh machine can bring up the stack from compose + config and pass a smoke chat.

---

## Phase 9 — Voice v2 (1–2+ weeks after v1 is stable)

Do not start until chat + tools are trustworthy.

**Stack (from Concept)**
- Living room: HA Voice PE (or Atom Echo / phone / Pi+ReSpeaker)
- Compute: Home Assistant + Assist pipeline
  - STT: faster-whisper / whisper.cpp
  - TTS: Piper
  - Conversation agent → FastAPI brain (OpenAI-compatible)
- Wyoming protocol between satellite and HA

**Tasks**
1. Register brain as HA conversation agent
2. Assist pipeline: wake → STT → brain → TTS
3. Voice-specific prompt tweaks (shorter answers)
4. Latency budget (wake-to-speech); tune model size if needed
5. Optional: same auth as chat client

**Exit criteria:** Spoken weather + movie request works on Voice PE without touching the chat UI; brain code paths unchanged aside from the HA adapter.

---

## Phase 10 — Future features (backlog, post-v2)

Order by leverage once HA is in the loop:

1. **Smart home control** — mostly free via HA after conversation agent works
2. **Shopping lists / calendar** — HA or CalDAV; new tools on the same brain loop
3. **Proactive notifications** — worker watches Jellyfin “new episode”; notify via HA/Telegram
4. **Play music** — Jellyfin playback / HA media player tool
5. **Vector search for catalogue** — only if Phase 5 quality plateaus
6. **Voice ID / multi-user** — only if multi-profile was deferred and becomes painful

---

## Suggested timeline (solo, part-time)

| Phase | Focus | Rough duration |
|---|---|---|
| 0–1 | Decisions + Ollama proof | ~1 week |
| 2–3 | Brain + weather | ~1 week |
| 4–5 | Memory + Jellyfin | ~1–2 weeks |
| 6–8 | Chat + harden + Docker | ~1–2 weeks |
| — | **v1 usable** | |
| 9 | Voice pipeline | ~2+ weeks |
| 10 | Backlog | ongoing |

---

## Risk register

1. **Tool-call reliability** — mitigate with Phase 1 suite + logging; model swap is cheaper than agent frameworks.
2. **Context stuffing Jellyfin** — filtered subsets first; vectors later.
3. **“Fully offline” vs weather** — document network dependency; cache or degrade.
4. **Schema migration for multi-user** — decide in Phase 0.
5. **Voice latency** — 8B may be required for spoken UX even if 30B is fine for chat.
6. **Over-building** — no LangChain, no vector DB, no Open WebUI as core — Concept is explicit.

---

## First concrete sprint

1. Repo + config + system prompt
2. Ollama + Qwen3 + dummy tool script
3. FastAPI loop: chat + dummy tool + SQLite history + timeouts/logging
4. Wire Open-Meteo
5. Manual curl/chat proof before any UI or Jellyfin

That matches the Concept build order and keeps the brain frontend-agnostic for voice later.

---

## Model VRAM guide (Qwen3)

Figures for Ollama’s default **Q4_K_M** quant (weights + KV cache + runtime at short/medium context):

| Model | Params | Disk (Q4_K_M) | VRAM to run | Minimum practical GPU |
|---|---|---|---|---|
| **Qwen3 8B** | 8B dense | ~5 GB | **~6–7 GB** (more with long context / thinking mode) | 8 GB works; **12 GB** comfortable |
| **Qwen3 30B-A3B** | 30.5B MoE / **3.3B active** | ~18–19 GB | **~18–21 GB** | **24 GB** for full GPU (RTX 3090/4090); 16 GB needs Q3 or CPU offload |

Notes:
- MoE “A3B” means only ~3B params run per token (fast), but **all ~30B weights still load into VRAM** — you cannot treat it like a 3B model.
- Longer context and multi-turn tool loops grow the KV cache; thinking mode uses more tokens too.
- CPU/RAM offload works but is much slower; keep the model fully on GPU/unified memory for voice latency.
- Start on **8B**; upgrade to **30B-A3B** later without changing the rest of the stack.
