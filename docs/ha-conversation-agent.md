# Home Assistant conversation agent spike (Phase 2)

**Status:** pass (docs) — HA can call Mimir via an OpenAI-compatible custom
integration pointed at the brain. **Do not** use HA’s native Ollama agent.

**Date:** 2026-08-25  
**Scope:** docs research only (no live HA on the Windows dev box).

---

## Verdict

| Question | Result |
|---|---|
| Can HA register a conversation agent with a **custom base URL**? | **Pass** — via a **custom / HACS** OpenAI-compatible integration |
| Does core **OpenAI Conversation** accept an arbitrary base URL? | **Fail** — official docs: works only with the official OpenAI API endpoint |
| Can we use HA’s **native Ollama** conversation agent for Mimir? | **Fail / forbidden** — would talk to Ollama directly and **bypass Mimir tools** |

**Recommended path for Phase 10:** install a HACS OpenAI-compatible conversation
integration that supports a custom API base URL, point it at
`http://<mimir-host>:8000/v1`, and select that agent in Assist. Prefer
integrations that can run **completion-only** (or allow disabling HA-side tools)
so Mimir keeps owning weather / Jellyfin / future tools.

---

## Why native Ollama is out

HA’s built-in Ollama conversation agent sends prompts straight to Ollama.
Mimir’s tools, timeouts, history, and logging never run. That violates the
architecture lock in Concept / AGENTS: clients call the **brain**, never Ollama
for product traffic.

---

## Core OpenAI Conversation

Official integration docs state it does **not** support OpenAI-compatible
third-party backends or custom base URLs — only the official OpenAI endpoint.
Therefore it is **not** a viable Mimir path without HA core changes.

Source: [OpenAI Conversation](https://www.home-assistant.io/integrations/openai_conversation/)
(checked 2026-08-25).

---

## Working path: custom base URL integrations

Community / HACS options that accept a custom base URL (examples; verify
current maintenance before Phase 10):

| Integration | Notes |
|---|---|
| [Extended OpenAI Conversation](https://github.com/jekalmin/extended_openai_conversation) | Explicit “Base Url” for LocalAI / Azure / compatible servers |
| [Local OpenAI LLM](https://github.com/skye-harris/hass_local_openai_llm/) | Fork with server URL; streaming; optional API key |
| [OpenLLM Conversation](https://github.com/adamjs83/hass-openllm-conversation) | Generic OpenAI-compatible base URL |
| [Custom OpenAI API Conversation](https://github.com/hekmon/ha-openaicust) | Fork with custom base URL |

**Setup sketch (any of the above):**

1. Install via HACS (or manual `custom_components/`).
2. Add integration → set **Base URL** to `http://<brain-lan-ip>:8000/v1`
   (must end with `/v1` for typical OpenAI clients).
3. API key: optional until Phase 7; use a placeholder if the integration requires one.
4. Model: `qwen3:8b` (or whatever `ollama.model` is in Mimir config) —
   advertised by `GET /v1/models`.
5. Voice Assistants → Conversation agent → select this integration.
6. Confirm Assist replies come from Mimir (e.g. “what time is it on the server?”
   should hit `get_server_time` and show up in `data/logs/turns.jsonl`).

---

## Endpoints Mimir exposes for this path

| Method | Path | Role |
|---|---|---|
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat (non-streaming in Phase 2) |
| `GET` | `/v1/models` | Model discovery for HA config flows |
| `POST` | `/v1/chat` | Native Mimir clients (chat UI); not required by HA |
| `GET` | `/health` | Ops / readiness |

Streaming is reserved on the same URLs — see [`api-streaming.md`](./api-streaming.md).

---

## Tool ownership (critical)

Mimir runs the **tool loop inside the brain**. The OpenAI adapter:

- Injects Mimir tool schemas toward Ollama.
- **Ignores** client-supplied `tools` in the OpenAI request body.
- Returns **final assistant text** only.

If a chosen HA integration insists on **client-side** tool calling (exposing HA
LLM APIs / Assist tools into the completion request and expecting the model to
call them back to HA):

1. Prefer disabling HA tool / function calling for the Mimir agent, **or**
2. Use a completion-only config, **or**
3. Fall back later to a custom HA conversation agent that POSTs to `/v1/chat`
   (still on the table if HACS options fight tool ownership).

Do **not** “fix” conflicts by pointing HA at Ollama.

---

## Phase 10 checklist (when voice starts)

- [ ] Pick one maintained HACS OpenAI-compatible integration
- [ ] Point base URL at Mimir `/v1`; confirm `/v1/models` discovery
- [ ] Disable HA-side tools for that agent if they conflict
- [ ] Smoke: spoken weather + movie request after Phases 3–5 exist
- [ ] Re-run `scripts/tool_call_suite.py` after any voice-oriented prompt tweaks

---

## Pass / fail summary

**Pass** for “HA can call our OpenAI-compatible endpoint with a custom base URL”
via HACS/custom integrations + Mimir’s `/v1/chat/completions` and `/v1/models`.

**Fail** for core OpenAI Conversation (no custom base URL) and for native Ollama
(bypasses the brain). Those are documented non-paths.
