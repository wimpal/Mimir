# Mimir Implementation Roadmap

**North star (v1):** Fully offline, self-hosted chat assistant (Qwen3 via Ollama) with weather + Jellyfin recommendations, FastAPI “brain” as the only integration surface so voice (v2) can plug in later without rewriting the core.

Source of truth for product intent: `[Concept.md](./Concept.md)`.

---

## Hardware (locked for this phase of work)


| Role                    | Hardware                                            | Model choice                                                                              |
| ----------------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **Dev machine (now)**   | AMD Radeon RX **9070 XT 16 GB**                     | **Qwen3 8B** Q4_K_M — fits easily (~6–7 GB); leave headroom for long context / tool loops |
| **Compute box (later)** | Separate hidden box (e.g. 24 GB NVIDIA or Mac mini) | Optional upgrade to **Qwen3 30B-A3B**; brain/API unchanged                                |


**Why not 30B-A3B on the 9070 XT:** Q4_K_M needs ~18–21 GB — it won’t fit fully in 16 GB without a lower quant or CPU offload (slower, worse for iteration). Use 8B until the dedicated box exists.

**AMD note:** Ollama on Windows + AMD may use ROCm/HIP or **Vulkan** depending on
Ollama build and RDNA4 support. Verify GPU offload with `ollama ps` after the
first run — if layers stay on CPU, fix the AMD runtime (or fall back per Risk 7)
before judging model quality.

### Windows → Linux compute box

Yes — the eventual compute box will almost certainly be **Linux** (or HA OS / Debian-based). The stack is already portable; the switch is easy if we avoid a few Windows-only traps while developing here.


| Layer                  | Portability                                                                                            |
| ---------------------- | ------------------------------------------------------------------------------------------------------ |
| FastAPI brain + SQLite | Trivial — pure Python; use pathlib, no `C:\...` hardcoding                                             |
| Config                 | Env vars / YAML relative to the app or `/data` volume — not machine-specific paths                     |
| Chat client            | HTTP only — no change                                                                                  |
| Ollama                 | Same REST API on both OSes; **GPU setup differs** (this PC: AMD; compute box: likely NVIDIA CUDA)      |
| Docker Compose         | Preferred deploy path on Linux; use it on Windows too when practical so compose is the source of truth |
| Voice / Home Assistant | Linux-native; never required on the Windows dev box                                                    |


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


| Decision         | Status                                                                                                                                 |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Inference        | **Locked:** Ollama + **Qwen3 8B** (`qwen3:8b`, Q4_K_M) on 9070 XT (30B-A3B deferred to compute box)                                    |
| Orchestration    | **Locked:** custom FastAPI loop (no LangChain)                                                                                         |
| Storage          | **Locked:** SQLite: history, prefs, Jellyfin cache                                                                                     |
| Clients          | **Locked:** thin chat UI first; brain stays OpenAI-compatible / HTTP-agnostic                                                          |
| Profiles         | **Locked:** single-user for v1 — no users table/FKs; revisit only if multi-user becomes a day-one requirement                          |
| Personality      | **Locked:** Jarvis-led household operator (calm competence, dry wit, brief) — see `[config/system_prompt.md](./config/system_prompt.md)` |
| Language/tooling | **Locked:** Python 3.12+, uv, ruff; config = `config/config.yaml` (non-secrets) + `.env` secrets, `MIMIR_`* env overrides YAML         |
| “Uncensored”     | **Locked:** self-hosted / no cloud moderation — **stock** `qwen3:8b`, not an abliterated finetune (tool-calling tradeoff; see Concept) |
| Thinking mode    | **Locked:** `ollama.think: false` for tool loops and voice                                                                             |
| Context          | **Locked default:** `ollama.num_ctx: 8192` (set explicitly; never rely on Ollama’s silent default)                                     |


**Deliverables**

- Repo skeleton: `brain/`, `clients/tui/`, `config/`, `docker-compose.yml` (stubs OK)
- `config.yaml` / `.env.example`: Ollama URL, lat/long, Jellyfin URL + API key placeholders, auth mode
- System prompt v0 checked into repo
- Explicit call: single-profile for v1 unless multi-user is required day one

**Exit criteria:** Config loads; docs state single- vs multi-user; 8B runs on GPU on this PC (`ollama ps` shows GPU).

---



## Phase 1 — Ollama + tool-calling proof (1–2 days)

Validate the model before building product logic.

**Status: done** — see `[docs/phase1-tool-calling.md](./docs/phase1-tool-calling.md)`
(12/12 with `num_ctx=8192`; GO for Phase 2).

**Tasks**

1. Install Ollama; pull Qwen3 (`qwen3:8b`).
2. Smoke-test chat via Ollama HTTP API; confirm `num_ctx` is applied (not silent default).
3. Register dummy tools (`get_server_time` / `echo`) and confirm tool-call JSON + grounded final answers.
4. Measure failure modes with a **standing** suite (not a one-off gate).

**Deliverables landed**

- `brain/ollama.py` — thin httpx client (`/api/chat`, `think` + `num_ctx` from config)
- `brain/tools/` — `get_server_time`, `echo`, schemas, dispatch
- `brain/agent.py` — minimal tool loop (iteration cap)
- `scripts/tool_call_suite.py` — permanent regression suite
- `docs/phase1-tool-calling.md` — measured metrics + failure modes

**Exit criteria (viability gate vs quality bar)**


| Bar                            | Meaning                                                                                      |
| ------------------------------ | -------------------------------------------------------------------------------------------- |
| **Viability (Phase 1 GO)**     | ≥80% overall on the fixed ≥10-case suite with `num_ctx` set — “is this model usable at all?” |
| **Quality (aim for Phase 2+)** | Track three metrics separately; improve before wiring many real tools                        |


**Three metrics** (suite must report each, not only a blended pass rate):

1. **Right tool** — required tool called; unexpected tools not called
2. **Valid arguments** — schema-ok args (or intentional empty for no-arg tools)
3. **Result used** — final answer grounded in tool output (no bare “done” / hallucinated values)

**Standing rule:** re-run `uv run python scripts/tool_call_suite.py` whenever you
change **model tag**, **system prompt**, **tool schemas**, or `num_ctx` **/** `think`.
Treat regressions like test failures.

If viability fails after one round of schema/prompt/`num_ctx` fixes, **swap model
before** wiring weather/Jellyfin. Named fallbacks to try (verify with the same suite;
no assumed ranking): `qwen2.5:14b`, `llama3.1:8b`, `mistral-nemo`.

---



## Phase 2 — FastAPI brain core (2–4 days)

The reusable “brain” every client will call.

**Status: done** — see `[docs/ha-conversation-agent.md](./docs/ha-conversation-agent.md)`
and `[docs/api-streaming.md](./docs/api-streaming.md)`.

**API shape**

- `POST /v1/chat` — message(s) in → assistant reply out
- `GET /health` — Ollama reachable + DB ok
- OpenAI-compatible `POST /v1/chat/completions` (or thin adapter) for HA in v2

**Streaming contract (decide here, implement now or stub):** design `/v1/chat` (and
the OpenAI adapter) so SSE/chunked streaming can be added without changing
clients’ URL or auth. Phase 6/9 will need it for latency; do not invent a second
API later. Non-streaming is fine for the first Phase 2 cut if the contract is documented.

**Docs spike (before investing in the OpenAI adapter):** verify how Home Assistant
registers an external conversation agent with a **custom base URL**. Confirm we
will **not** use HA’s native Ollama integration (it would bypass Mimir tools).
Write findings in `docs/ha-conversation-agent.md` (pass/fail + integration path).

**Agent loop**

1. Load system prompt + recent history from SQLite (Phase 4+)
2. Call Ollama with tool schemas, `think` + `num_ctx` from config
3. If tool call → execute → append tool result → call again (cap iterations, e.g. 3–5)
4. **Observability now:** append a JSONL turn trace (`prompt_id`, tools, latencies, success/fail) via `turn_log`
5. **Conversation/message persistence:** Phase 4 — see `[docs/phase4-memory.md](./docs/phase4-memory.md)`

**Hardening in this phase (don’t defer)**

- Timeouts on Ollama and every tool (+ overall turn budget)
- Graceful “brain’s offline / tool unavailable” messages (no hang)
- Structured logging: prompt id, tool name, latency, success/fail (file or SQLite)

**Deliverables landed**

- `brain/main.py` — FastAPI: `/health`, `/v1/chat`, `/v1/chat/completions`, `/v1/models`
- `brain/service.py` — prompt + `run_turn` + graceful offline replies
- `brain/db.py` — `mimir.db` bootstrap (`schema_version`; history tables landed in Phase 4)
- `brain/turn_log.py` — JSONL turn observability under `data_dir/logs/turns.jsonl`
- `docs/api-streaming.md` — SSE contract reserved; `stream=true` → 501
- `docs/ha-conversation-agent.md` — HACS OpenAI-compat path; no native Ollama

**Exit criteria:** Dummy tool works end-to-end through FastAPI; timeouts and offline
paths verified; logs show tool traces; HA spike doc exists; tool suite still green.

---



## Phase 3 — Weather tool (1–2 days)

First real external tool; keeps the loop simple.

**Status: done** — see `[docs/phase3-weather.md](./docs/phase3-weather.md)`.

**Tasks**

1. Tool schema: `get_weather` — default lat/long from config; optional place name later.
2. Client for [Open-Meteo](https://open-meteo.com/) with **KNMI HARMONIE** (`knmi_harmonie_arome_netherlands`) for NL.
3. Normalize response for the LLM (temp, conditions, precip, short forecast) — small JSON, not raw API dump.
4. Pin **5 fixed prompts** with expected behavior (e.g. “weather today” → calls tool; offline → clear failure). Re-run tool suite after schema add.

**Deliverables landed**

- `brain/tools/weather.py` — Open-Meteo client, WMO labels, compact JSON
- `build_registry(settings)` — weather bound to config + `timeouts.tool_s`
- `location.timezone` (default `Europe/Amsterdam`) in config
- 5 pinned weather cases in `scripts/tool_call_suite.py`
- `docs/phase3-weather.md`

**Offline note:** Open-Meteo needs network. For “fully offline” days, either cache last forecast or reply that weather is unavailable. Document that weather is the one soft dependency. Phase 3 fails clear (no cache); **Forecast cache** lands in Phase 7.

**Exit criteria:** The 5 pinned prompts behave as expected; unreachable API fails fast with a clear reply.

---



## Phase 4 — Memory & preferences (1–2 days)

SQLite before Jellyfin so history and prefs have a home.

**Status: done** — see `[docs/phase4-memory.md](./docs/phase4-memory.md)`, `[CONTEXT.md](./CONTEXT.md)`, `[docs/adr/0001-chat-memory-vs-openai-compat.md](./docs/adr/0001-chat-memory-vs-openai-compat.md)`.

**Schema (landed,** `schema_version=1`**)**

- `schema_version` — hand-rolled migrations
- `conversations` / `messages` — user + final assistant only; timestamps
- `preferences` — allowlisted key/value (`favorite_genres`, `tone`)
- Tool observability remains JSONL `turn_log` (no `tool_logs` table)
- If multi-profile: `users` + FK everywhere (not in v1)

**Behavior (landed)**

- Persist every `/v1/chat` turn that includes `message` (mint or create-on-write id)
- Inject last N Message pairs (`memory.history_pairs`, default 20) under `num_ctx`
- Preference get/set via tools + system-prompt inject (refresh mid-turn after set)
- OpenAI-compat stays client-history-only (ADR 0001)
- Compaction/summarization: backlog until long threads break (see Concept Data)

**Deliverables landed**

- `brain/db.py` — migration 0→1 + conversation/message/preference API
- `brain/prefs.py` + `brain/tools/preferences.py`
- `brain/service.py` — persist vs stateless paths; `after_tool` prefs refresh
- `memory.history_pairs` in config; pinned `pref_*` suite cases
- `docs/phase4-memory.md`

**Exit criteria:** Restart keeps history; prefs influence a follow-up reply; migration applies cleanly on empty DB.

---



## Phase 5 — Jellyfin sync + recommendation tool (3–6 days)

Core media feature; keep v1 intentionally dumb.

**Status: done** — see `[docs/phase5-jellyfin.md](./docs/phase5-jellyfin.md)`,
`[CONTEXT.md](./CONTEXT.md)`, `[docs/adr/0002-jellyfin-seed-title-filters.md](./docs/adr/0002-jellyfin-seed-title-filters.md)`.

### 5a. Sync job (landed)

- Auth: Jellyfin API key from env (`JELLYFIN_API_KEY` → `X-Emby-Token`)
- Configured `user_id` + allowlisted movie `library_ids`; movies only
- **Paginate** Items; idempotent upserts into a staging sync generation; publish atomically
- On-demand `POST /v1/jellyfin/sync` + daily periodic background Sync
- Sync status on Sync response and `/health` (`jellyfin_sync`)



### 5b. Recommendation tool (landed)

`recommend_movies(seed_title?, genre?, mood?, unwatched_only?, min_rating?)`:

1. Filters SQLite Catalogue (unwatched = not Played; mood→genres map; optional rating floor)
2. Seed-title overlap filters (ADR 0002); subset cap ~20
3. LLM picks and explains — not a full recommender engine
4. Serves last-good Catalogue if Jellyfin is down; empty Catalogue fails clear



### 5c. Defer until it hurts

Embeddings + `sqlite-vec` / Chroma only when catalogue stuffing / filter quality breaks (~1000+ titles or bad relevance).

**Exit criteria:** Sync fills SQLite (paginated); **5 pinned suite cases**
(including empty-catalogue + must-not-call) stay green; Jellyfin down → Sync
timeout/clear fail while recommend uses last-good; tool suite still green after
schema add.

---



## Phase 6 — Chat frontend (2–4 days)

**Status: done** — Textual TUI Chat client (ADR 0004 supersedes web UI / ADR 0003).
See `[docs/phase6-chat.md](./docs/phase6-chat.md)`, `[docs/api-streaming.md](./docs/api-streaming.md)`.

Thin **Textual TUI** Chat client only — no business logic in the UI.
Telegram/Matrix bots are out of scope (not a deferred option).
The Phase 6 web static UI was replaced by the TUI.

**Locked**

- Full-screen Textual TUI (`uv run mimir` / `dist/mimir.exe`)
- On launch: health-check brain; if down, start `uv run uvicorn` from the repo (does not stop brain on exit)
- Each launch starts a **new** Conversation; `/history` resumes a past one (Phase 8b); `/settings` edits Preferences (Phase 8c); `/new` clears the current Conversation
- Brain base URL via `--url` / `MIMIR_BRAIN_URL` (default `http://127.0.0.1:8000`)
- Consume SSE on `POST /v1/chat`; OpenAI-compat streaming stays deferred
- `GET /v1/conversations/{id}/messages` available (full Conversation); not auto-restored on launch
- Tool activity as bordered cards; fail-loud errors; Esc interrupts turn
- No auth until Phase 7 (Auth token when bind is non-loopback)

**Must-haves**

- Chat → weather → movie recs from the TUI
- Consume `/v1/chat` streaming
- Error states mirroring brain offline / tool failure
- Auto-start brain when unreachable (needs `uv` + repo)

**Must-nots**

- No direct Ollama/Jellyfin calls from the client
- No Open WebUI as the long-term path (fine for temporary debugging)
- No Telegram/Matrix chat front door
- No browser Chat UI as the product front door

**Exit criteria:** Full user journey works from TUI: chat → weather → movie recs; brain auto-starts if down; new Conversation each launch.

---



## Phase 7 — Reliability, security, observability (2–3 days)

**Status: done** — see `[docs/phase7-harden.md](./docs/phase7-harden.md)`, `[docs/adr/0005-non-loopback-requires-auth-token.md](./docs/adr/0005-non-loopback-requires-auth-token.md)`, `[docs/ops-backup.md](./docs/ops-backup.md)`, `[docs/phase7-personality.md](./docs/phase7-personality.md)`.

| Area          | Work |
| ------------- | ---- |
| Reliability   | Timeouts already landed; **Forecast cache** (~60 min TTL, `stale` + `fetched_at`) when Open-Meteo fails; `/health` for clients |
| Security      | Default bind loopback + `auth.mode: none`. Non-loopback `runtime.host` **refuses startup** unless `auth.mode: token` + Auth token (ADR 0005). Bearer on `/v1/*` when token mode. `/health` always open. Sync + `/debug/*` are **Host-only** |
| Remote access | **Deferred** to Phase 11 backlog (Tailscale/WireGuard). This phase = local network only |
| Observability | `GET /debug/recent-traces` — last N Turn trace summaries, Host-only |
| Personality   | Jarvis-led rewrite of system prompt — **re-run tool suite** after edits; see `docs/phase7-personality.md` |
| Data          | Document SQLite backup (copy `data_dir`); retention still “keep all” unless disk hurts |


**Exit criteria:** Kill Ollama/Jellyfin → assistant responds usefully; non-loopback bind without Auth token cannot start.

---



## Phase 8a — Recently watched (Jellyfin) (1–2 days)

**Status: done** — see `[docs/phase8a-recent-watches.md](./docs/phase8a-recent-watches.md)`.

Catalogue already knows Watched/`played`, but not **when**. Sync (or equivalent) must retain recent play timing so Mimir can answer “what did I watch lately” (~last 1–2 weeks) and bias `recommend_movies` with that Recent watches signal.

**Tasks**

- Persist last-played (or equivalent) from Jellyfin UserData into the Catalogue
- Tool or filter path: list Recently watched in a configurable window (default ~14 days)
- Recommendation path: take Recent watches into account (not only `unwatched_only`)
- Fail clear when Sync/Catalogue lacks play dates

**Exit criteria:** “What did I watch last week?” returns grounded titles; recs change sensibly when Recent watches exist.

---



## Phase 8b — `/history` resume (1–2 days)

**Status: done** — see [`docs/phase8b-history.md`](./docs/phase8b-history.md).

Chat client: browse past Conversations and resume one.

**Tasks**

- Brain API: list Conversations (id + summary metadata enough to choose)
- TUI `/history`: list → arrow keys → Enter resumes that Conversation
- Launch behavior can stay “new Conversation”; resume is explicit via `/history`

**Exit criteria:** Pick an old Conversation from `/history` and continue chatting with its History window.

---



## Phase 8c — `/settings` Preferences UI (1 day)

**Status: done** — see [`docs/phase8c-settings.md`](./docs/phase8c-settings.md).

TUI screen to view/edit allowlisted Preferences (same store as `get_preference` / `set_preference`). Not `config.yaml` / `.env` / Auth token.

**Exit criteria:** Change a Preference in `/settings`; later turns see it (inject/tools) without editing YAML.

---



## Phase 8d — Calendar feed (ICS URL) (1–2 days)

**Status: done** — see [`docs/phase8d-calendar.md`](./docs/phase8d-calendar.md).

Read-only **Calendar feed(s)** via configured ICS subscribe URL(s) — **provider-agnostic** (Proton share link first; Google/Fastmail/etc. by swapping the URL). No provider SDK. Local CalDAV bridge deferred until ICS publish lag is unacceptable.

**Tasks**

- Config: one or more named feeds (`calendar.feeds` id + display name); ICS URL + optional basic auth in `.env` (`CALENDAR_ICS_URL_<ID>`, or legacy `CALENDAR_ICS_URL`)
- Tool `get_calendar`: full calendar day in `location.timezone` (midnight→midnight); timeout + fail clear; tag events with `calendar` / `calendar_name`
- Optional short TTL cache (weather-like: serve in-TTL stale on fetch failure); document provider publish lag (e.g. Proton share up to ~8h)

**Exit criteria:** “What’s on my calendar today?” returns events from the feed(s); wrong/missing URL fails loud (or returns in-TTL stale cache marked `stale=true` when a prior fetch succeeded).

See [`docs/adr/0007-calendar-via-ics-url.md`](./docs/adr/0007-calendar-via-ics-url.md).

---



## Phase 8e — Morning brief (½–1 day)

**Status: done** — see [`docs/phase8e-morning-brief.md`](./docs/phase8e-morning-brief.md).

Phrase-triggered (“good morning”): normal chat + prompt/tool discipline calls weather + Calendar feed. No `/morning`, no proactive push. Contents: **weather + today’s schedule** only (no news).

**Exit criteria:** Typed “good morning” yields a short brief grounded in tool output; works the same later when spoken in Phase 10.

---



## Phase 8f — Discord send tool (1–2 days)

Brain tool posts a message to Discord. **Not** a Chat client front door (ADR 0006).

**Locked**

- Bot token in env; **Channel allowlist** = snowflake IDs (+ optional labels) in config
- Tool: send message to an allowlisted channel only; expand (read/DM) later
- Timeout + fail clear when Discord is down

**Exit criteria:** User asks Mimir to post something; message appears only on an allowlisted channel; inventing a channel id outside the allowlist fails.

---



## Phase 9 — Deployment packaging (1–2 days)

*(Was Phase 8.)*

**Tasks**

- `docker-compose.yml`: Ollama (or document host GPU Ollama), brain; Chat client is the TUI (`uv run mimir`), not a static server
- Volume mounts for SQLite + model cache; backup note for the volume
- README: hardware paths (this 9070 XT box / later NVIDIA or Mac mini), first-boot steps
- One-command health check script (include tool-suite optional flag)

**Exit criteria:** Fresh machine can bring up the stack from compose + config and pass a smoke chat.

---



## Phase 10 — Voice v2 (1–2+ weeks after v1 is stable)

*(Was Phase 9.)* Do not start until chat + tools are trustworthy **and** the Phase 2 HA spike passed.

**Stack (from Concept)**

- Living room: HA Voice PE (or Atom Echo / phone / Pi+ReSpeaker) — verify phone-as-satellite support before buying
- Compute: Home Assistant + Assist pipeline
  - STT: faster-whisper / whisper.cpp
  - TTS: Piper
  - Conversation agent → FastAPI brain (**not** HA native Ollama)
- Wyoming protocol between satellite and HA
- `think: false`; streaming preferred for wake-to-speech latency

**Tasks**

1. Register brain as HA conversation agent (path from spike doc)
2. Assist pipeline: wake → STT → brain → TTS
3. Voice-specific prompt tweaks (shorter answers) + re-run tool suite
4. Latency budget (wake-to-speech); tune model size if needed
5. Optional: same auth as chat client

**Exit criteria:** Spoken weather + movie request works on Voice PE without touching the chat UI; brain code paths unchanged aside from the HA adapter.

---



## Phase 11 — Future features (backlog, post-v2)

*(Was Phase 10.)* Order by leverage once HA is in the loop:

1. **Smart home control** — mostly free via HA after conversation agent works
2. **Shopping lists** — HA or similar; new tools on the same brain loop
3. **Email read** (provider-agnostic if practical) — backlog; not scheduled
4. **Calendar deepen** — CalDAV/local bridge if ICS lag hurts; write access only if needed
5. **Proactive notifications** — worker watches Jellyfin “new episode”; notify via HA (or Discord send)
6. **Play music** — Jellyfin playback / HA media player tool
7. **Vector search for catalogue** — only if Phase 5 quality plateaus
8. **History compaction / summarization** — when last-N under `num_ctx` is not enough
9. **Voice ID / multi-user** — only if multi-profile was deferred and becomes painful
10. **Buienradar / Buienalarm rain nowcast** — 5-minute precip if Open-Meteo hourly is not enough
11. **Remote access docs** — Tailscale/WireGuard only; no port-forward recipe as default (deferred from Phase 7)
12. **Discord expand** — read channels / DM after send-only proves useful

**Personality:** not a phase — small Jarvis-led prompt iterations anytime; re-run the tool suite after edits.

---



## Suggested timeline (solo, part-time)


| Phase | Focus                                          | Rough duration |
| ----- | ---------------------------------------------- | -------------- |
| 0–1   | Decisions + Ollama proof                       | ~1 week        |
| 2–3   | Brain + weather                                | ~1 week        |
| 4–5   | Memory + Jellyfin                              | ~1–2 weeks     |
| 6–7   | Chat + harden                                  | ~1–2 weeks     |
| 8a–8f | Recent watches, TUI polish, calendar, Discord  | ~1–2 weeks     |
| 9     | Deployment packaging                           | ~1–2 days      |
| —     | **v1 usable** (see Concept definition of done) |                |
| 10    | Voice pipeline                                 | ~2+ weeks      |
| 11    | Backlog                                        | ongoing        |


---



## Risk register

1. **Tool-call reliability** — mitigate with standing suite + logging; swap to a named fallback model if viability fails.
2. **Context stuffing Jellyfin** — filtered subsets first; vectors later.
3. **“Fully offline” vs weather** — document network dependency; cache or degrade.
4. **Schema migration for multi-user** — single-user locked in Phase 0; versioned SQL from Phase 4.
5. **Voice latency** — 8B may be required for spoken UX even if 30B is fine for chat; streaming + `think: false`.
6. **Over-building** — no LangChain, no vector DB, no Open WebUI as core — Concept is explicit.
7. **AMD / RDNA4 GPU stack on Windows** — ROCm/HIP or Vulkan may misbehave; if `ollama ps` shows CPU, try Vulkan builds / driver updates, then **CPU-offload and accept slower iteration** rather than blocking the brain work. Do not judge model quality on a CPU-only path.
8. **Silent context truncation** — always set `num_ctx`; misattributing truncation to “bad model” causes unnecessary swaps.
9. **HA bypasses the brain** — native Ollama conversation agent would drop tools; verify OpenAI-compat / custom agent path in Phase 2 before Phase 10.

---



## First concrete sprint

1. Repo + config + system prompt
2. Ollama + Qwen3 + dummy tool script *(done — Phase 1)*
3. FastAPI loop: chat + dummy tool + timeouts/logging (+ HA spike doc) *(done — Phase 2)*
4. Wire Open-Meteo + KNMI *(done — Phase 3)*
5. Memory + preferences *(done — Phase 4)*
6. Manual curl/chat proof before any UI
7. Jellyfin (Phase 5) *(done — see docs/phase5-jellyfin.md)*
8. Chat frontend (Phase 6) *(done — see docs/phase6-chat.md)*

That keeps the brain frontend-agnostic for voice later.

---



## Model VRAM guide (Qwen3)

Figures for Ollama’s default **Q4_K_M** quant (weights + KV cache + runtime at short/medium context):


| Model             | Params                      | Disk (Q4_K_M) | VRAM to run                                          | Minimum practical GPU                                                 |
| ----------------- | --------------------------- | ------------- | ---------------------------------------------------- | --------------------------------------------------------------------- |
| **Qwen3 8B**      | 8B dense                    | ~5 GB         | **~6–7 GB** (more with long context / thinking mode) | 8 GB works; **12 GB** comfortable                                     |
| **Qwen3 30B-A3B** | 30.5B MoE / **3.3B active** | ~18–19 GB     | **~18–21 GB**                                        | **24 GB** for full GPU (RTX 3090/4090); 16 GB needs Q3 or CPU offload |


Notes:

- MoE “A3B” means only ~3B params run per token (fast), but **all ~30B weights still load into VRAM** — you cannot treat it like a 3B model.
- Longer context (`num_ctx`) and multi-turn tool loops grow the KV cache; thinking mode uses more tokens too.
- CPU/RAM offload works but is much slower; keep the model fully on GPU/unified memory for voice latency.
- Start on **8B**; upgrade to **30B-A3B** later without changing the rest of the stack.

