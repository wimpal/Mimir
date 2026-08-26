# Mimir — Agent Guide

Offline, self-hosted personal assistant. v1 is a **thin chat client** talking to a **FastAPI brain** that runs **Ollama + Qwen3** with tools (weather, Jellyfin). Voice (Home Assistant / Wyoming) is v2 and must not reshape the brain.

## Read first

| Before doing this | Read this |
|---|---|
| Changing goals, stack, tools, or architecture | [`Concept.md`](./Concept.md) |
| Choosing what to build next, phases, exit criteria, hardware | [`ROADMAP.md`](./ROADMAP.md) |
| Starting any implementation session | Current roadmap phase + its exit criteria |

Do not invent scope outside Concept/Roadmap. Advance one phase at a time; meet that phase’s exit criteria before starting the next.

## Docs stay true

`Concept.md`, `ROADMAP.md`, and `AGENTS.md` are the project’s source of truth. When a decision, stack choice, phase, hardware assumption, or scope **differs** from what those files say, **update the affected file(s) in the same change** so they always match reality. Prefer editing the authoritative doc over leaving stale prose and “we decided X in chat” drift.

**One source per fact:** Concept = intent/decisions/rationale; ROADMAP = sequencing/exit criteria/hardware locks. Do not duplicate roadmaps or shopping lists in Concept.

## Stack (locked)

| Layer | Choice |
|---|---|
| Inference | **Ollama** + **stock Qwen3 8B** Q4_K_M (dev). 30B-A3B only on a later compute box. Not an abliterated “uncensored” finetune |
| Options | `ollama.num_ctx: 8192`, `ollama.think: false` (tool loops + voice) |
| Orchestration | **Custom FastAPI** tool loop — not LangChain / LlamaIndex / CrewAI |
| Memory | **SQLite** (history, prefs, Jellyfin cache); hand-rolled migrations (`schema_version`, Phase 4+) |
| Weather | **Open-Meteo** (no API key) → **KNMI HARMONIE** for NL; lat/long + timezone in config |
| Media | **Jellyfin REST** → SQLite cache (paginated sync); LLM reasons over a **filtered subset** |
| Chat UI | Thin Textual TUI (`uv run mimir` / `dist/mimir.exe`); may auto-start brain; no direct Ollama/Jellyfin calls |
| Deploy target | Linux compute box later; keep code **OS-agnostic** now (Windows + AMD 9070 XT 16 GB) |
| Language/tooling | **Python 3.12+** · **uv** · **ruff**; profiles: **single-user v1** (locked Phase 0) |

## Architecture invariants

- The **brain** owns tools, prompts, history, timeouts, and Jellyfin sync. Clients are front doors only. Ollama never calls external APIs.
- Prefer an **OpenAI-compatible** chat endpoint (or thin adapter) so Home Assistant can call the same brain in v2 — **after** the Phase 2 HA spike confirms the path. Never point HA at native Ollama for Mimir (bypasses tools).
- Tool loop: user message → Ollama (+ tool schemas) → execute tool → feed result → final reply. Cap iterations. Time out every external call.
- Fail loud and short: if Ollama/Jellyfin/weather is down, return a clear message — never hang.
- **Single-user** unless Concept/Roadmap explicitly flips to multi-profile (schema choice is early and sticky).
- Jellyfin v1 = sync + filter + LLM pick. Add embeddings / vector search only after catalogue stuffing fails.
- Weather needs network; degrade gracefully when offline. Everything else aims local-first.
- Always pass **`num_ctx`** from config — do not rely on Ollama’s silent default.

## Repo shape (target)

```
brain/           # FastAPI service, tools, agent loop, SQLite
clients/tui/     # Textual Chat client (`uv run mimir`)
config/          # Examples + system prompt — real config.yaml gitignored
scripts/         # try_prompt.py, tool_call_suite.py (standing regression)
docs/            # Phase notes (tool-calling, HA spike, …)
tests/           # pytest suite
.env.example     # Secret placeholders — copy to .env (gitignored)
pyproject.toml   # Brain service deps, uv-managed
docker-compose.yml
Concept.md
ROADMAP.md
AGENTS.md
```

Configurable data dir for SQLite/logs (env or config). Use `pathlib`; no hardcoded `C:\...` or machine-specific paths.

## Working rules

- **Small slices:** dummy tool → weather → memory → Jellyfin → chat UI → harden → compose → voice last.
- **Standing tool suite:** re-run `uv run python scripts/tool_call_suite.py` on model, system-prompt, tool-schema, or `num_ctx`/`think` changes. Viability bar ≥80%; track right-tool / valid-args / result-used separately when extending cases.
- If the model drops/malforms calls after prompt/`num_ctx` fixes, **swap model** (named fallbacks in ROADMAP) — do not bury it under a framework.
- Keep the brain **frontend-agnostic**. Chat-specific UX stays in `clients/`.
- Log prompt id, tool name, latency, success/fail (file or SQLite) once the loop exists.
- Secrets (Jellyfin key, auth tokens) only via env / local config gitignored; ship `.env.example`.
- Prefer `docker-compose.yml` as the Linux deploy unit even if you run Ollama natively on Windows during GPU bring-up.
- After changing Ollama/model setup on this PC, confirm GPU offload with `ollama ps` (AMD path). If CPU-only, follow ROADMAP Risk 7 before judging quality.

## Out of scope until Roadmap says otherwise

Voice / Wyoming / Home Assistant Assist (until Phase 9 + HA spike), LangChain-class frameworks, Open WebUI as the product UI, vector DB for Jellyfin, calendar/shopping/smart-home/proactive notify, exposing the brain to the public internet (LAN + optional Tailscale later).

## Done means

A change is done when the **current roadmap phase exit criteria** are met (or the specific task’s acceptance check is), not when scaffolding exists. Prefer a working curl/API path before polish UI. v1 product done = Concept “definition of done”.
