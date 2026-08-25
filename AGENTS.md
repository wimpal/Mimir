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

## Stack (locked)

| Layer | Choice |
|---|---|
| Inference | **Ollama** + **Qwen3 8B** Q4_K_M (dev). 30B-A3B only on a later compute box |
| Orchestration | **Custom FastAPI** tool loop — not LangChain / LlamaIndex / CrewAI |
| Memory | **SQLite** (history, prefs, Jellyfin cache) |
| Weather | **Open-Meteo** (no API key); lat/long in config |
| Media | **Jellyfin REST** → SQLite cache; LLM reasons over a **filtered subset** |
| Chat UI | Thin HTTP client only — no business logic, no direct Ollama/Jellyfin calls |
| Deploy target | Linux compute box later; keep code **OS-agnostic** now (Windows + AMD 9070 XT 16 GB) |

## Architecture invariants

- The **brain** owns tools, prompts, history, and timeouts. Clients are front doors only.
- Prefer an **OpenAI-compatible** chat endpoint (or thin adapter) so Home Assistant can call the same brain in v2.
- Tool loop: user message → Ollama (+ tool schemas) → execute tool → feed result → final reply. Cap iterations. Time out every external call.
- Fail loud and short: if Ollama/Jellyfin/weather is down, return a clear message — never hang.
- **Single-user** unless Concept/Roadmap explicitly flips to multi-profile (schema choice is early and sticky).
- Jellyfin v1 = sync + filter + LLM pick. Add embeddings / vector search only after catalogue stuffing fails.
- Weather needs network; degrade gracefully when offline. Everything else aims local-first.

## Repo shape (target)

```
brain/           # FastAPI service, tools, agent loop, SQLite
clients/chat/    # Thin chat UI or bot
config/          # Examples, system prompt — no secrets committed
docker-compose.yml
Concept.md
ROADMAP.md
AGENTS.md
```

Configurable data dir for SQLite/logs (env or config). Use `pathlib`; no hardcoded `C:\...` or machine-specific paths.

## Working rules

- **Small slices:** dummy tool → weather → memory → Jellyfin → chat UI → harden → compose → voice last.
- **Prove tool-calling** against Ollama before wiring real tools. If the model drops/malforms calls, fix or swap the model — do not bury it under a framework.
- Keep the brain **frontend-agnostic**. Chat-specific UX stays in `clients/`.
- Log prompt id, tool name, latency, success/fail (file or SQLite) once the loop exists.
- Secrets (Jellyfin key, auth tokens) only via env / local config gitignored; ship `.env.example`.
- Prefer `docker-compose.yml` as the Linux deploy unit even if Ollama runs natively on Windows during GPU bring-up.
- After changing Ollama/model setup on this PC, confirm GPU offload with `ollama ps` (AMD path).

## Out of scope until Roadmap says otherwise

Voice / Wyoming / Home Assistant Assist, LangChain-class frameworks, Open WebUI as the product UI, vector DB for Jellyfin, calendar/shopping/smart-home/proactive notify, exposing the brain to the public internet (LAN + optional Tailscale later).

## Done means

A change is done when the **current roadmap phase exit criteria** are met (or the specific task’s acceptance check is), not when scaffolding exists. Prefer a working curl/API path before polish UI.
