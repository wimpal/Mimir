# Offline LLM Personal Assistant — v1 Architecture & Tech Stack

Assistant name: Mimir

## Goals (v1)

- Answer general questions
- Check local weather
- Recommend movies from a Jellyfin catalogue
- Activated via chat app (v1), voice (v2, Siri/Echo-style)
- Fully offline, self-hosted, uncensored

---

## Inference Layer: Ollama

**Choice: Ollama**, not raw llama.cpp or vLLM.

Reasoning:

- Wraps llama.cpp, so you get its performance and quantization support, but with automatic GPU offloading, model management, and an OpenAI-compatible REST API.
- Native tool-calling support — required for the weather and Jellyfin integrations.
- vLLM is built for concurrent multi-user throughput — overkill and unnecessary ops overhead for a single-user assistant.
- llama.cpp directly remains the fallback if you later want fine-grained control (custom sampling, ARM/edge builds).

**Model: Qwen3** (8B for tighter hardware, 30B-A3B MoE if you have 16–24GB VRAM to spare). Uncensored version?

Currently has the best tool-calling reliability among local models available via Ollama — fewer dropped or malformed tool calls than Llama 3.3 or Gemma 4 in recent benchmarks. This matters once the agent is chaining tool calls rather than just chatting. Run it quantized (Q4_K_M as a starting point).

---

## Orchestration: Custom, not LangChain

For 3 tools (chat, weather, Jellyfin), a heavy agent framework adds abstraction you don't need.

Build a small **FastAPI** service that:

1. Receives the user message
2. Sends it to Ollama with your tool schemas (weather lookup, Jellyfin query)
3. If the model returns a tool call, executes it and feeds the result back to get a final response
4. Returns the response to the chat frontend

This is roughly 150 lines of Python, and you'll understand every part of it — valuable when debugging why it hallucinated a tool call.

---

## Tools

### Weather

- **Open-Meteo** — free, no API key required, sufficient for personal use.
- Store your lat/long in config.

### Jellyfin

- Use the **Jellyfin REST API** to pull library metadata (genres, cast, watch history, ratings).
- For v1: pull the catalogue + watch history into SQLite, and let the LLM reason over a filtered/summarized subset (e.g. "unwatched movies in genres you rate highly") rather than building a full recommendation engine.
- If the library grows large (1000+ titles), add embeddings + a small vector store (SQLite + `sqlite-vec`, or Chroma) so the whole catalogue isn't stuffed into context every time.

---

## Memory / State

SQLite for conversation history and preferences. Simple, durable, zero ops overhead.

---

## Chat Frontend (v1)

Build a minimal custom chat UI (or a Telegram/Matrix bot) rather than adopting Open WebUI.

Open WebUI is excellent but chat-specific. Since the roadmap includes voice, the FastAPI "brain" service should be frontend-agnostic from day one, with chat as just the first client calling it.

---

## Architecture Diagram

```
[Chat client] ──HTTP──▶ [FastAPI "brain" service] ──▶ [Ollama + Qwen3]
                              │                              │
                              ├──▶ [Open-Meteo API]          │ (tool calls)
                              ├──▶ [Jellyfin API] ◀───sync───┘
                              └──▶ [SQLite: history, prefs, cached catalogue]

```

---

## Path to Voice (v2)

Don't build this from scratch — use **Home Assistant's Assist pipeline** plus the **Wyoming protocol** ecosystem (built by the Home Assistant/Rhasspy team specifically for local wake-word → STT → assistant → TTS pipelines).

Components:

- **Home Assistant Voice Preview Edition** (or another Wyoming satellite) for wake word + mic/speaker, see Hardware section below
- **faster-whisper** or **whisper.cpp** for speech-to-text
- **Piper** for text-to-speech (fast, decent quality, fully local)
- **Home Assistant** itself, acting as the orchestrator between the satellite, STT/TTS, and the brain service

The FastAPI brain service gets registered with Home Assistant as a **conversation agent** (HA supports OpenAI-compatible endpoints for this) — same tool-calling logic as v1, new front door. This is the reason to build the chat frontend as a thin client rather than baking chat-specific logic into the brain service: the brain doesn't change between v1 and v2, only what's calling it.

### Updated architecture with voice

```
[Voice PE, living room] ──Wyoming──▶ [Home Assistant, hidden compute box]
                                              │
                                    ┌─────────┴─────────┐
                                    │  Assist pipeline:  │
                                    │  STT (whisper)      │
                                    │  → conversation agent (FastAPI brain)
                                    │  → TTS (Piper)      │
                                    └─────────┬─────────┘
                                              ▼
                                   [Ollama + Qwen3, weather API, Jellyfin]

```

---

## Hardware Recommendations

Key idea: **decouple the living-room device from the compute.** Only the microphone/speaker endpoint needs to look nice — the GPU or Mac mini doing the actual inference can live in a closet, basement, or network cabinet, connected over the local network.

### Compute box (hidden)

Two solid paths:

**GPU box**

- Used **RTX 3090 (24GB)** — best VRAM-per-dollar on the used market, comfortably runs Qwen3 30B-A3B quantized with room to spare.
- Pair with any decent CPU + 32GB RAM.

**Mac mini/Studio (M-series, 24–32GB+ unified memory)**

- Quieter, lower power, good if the box needs to sit in a living space rather than a server closet.
- Apple Silicon inference (especially via Ollama's MLX path) has gotten notably fast in 2026.

**Budget option**

- 8B model on a 12GB card (e.g. RTX 3060 12GB) is a perfectly good starting point. Upgrade the model later without touching the rest of the stack.

### Living-room device (visible): Home Assistant Voice Preview Edition

This is essentially built for exactly this use case — a privacy-respecting, Echo/Nest-style alternative:

- ~$59, puck-shaped, semi-transparent polycarbonate housing — designed to look like consumer smart-speaker hardware, not a dev board in a project box
- Built-in mic array, speaker, LED ring, rotary volume dial
- Runs fully local by default via the **Wyoming protocol** — no cloud account required
- Actively maintained by Nabu Casa (the Home Assistant company), with regular OTA firmware updates

It just streams audio to a server; wake word can run on-device or be offloaded, and everything else (STT, LLM, TTS) happens on the hidden compute box.

**Cheaper/DIY alternatives** if covering multiple rooms:

- **M5Stack Atom Echo** (~$13) — works well, but looks like a dev board; fine for an office, not for a living room
- **Old Android phone as satellite** — supported since March 2026; on-device wake word, phone as mic/speaker, free if a spare phone is available (put it in a nice dock)
- **DIY Raspberry Pi + ReSpeaker HAT** in a 3D-printed or off-the-shelf enclosure — most flexible, most effort, best if a specific look is wanted

---

## Roadmap: Beyond the Core Build

### Reliability & fallback

- Define graceful failure behavior for when Ollama/GPU is down or overloaded (e.g. "brain's offline" response rather than a hang or silent timeout)
- Add timeouts on tool calls — if Jellyfin or Open-Meteo is slow/unreachable, the assistant shouldn't hang waiting indefinitely

### Security

- Put the FastAPI brain service behind basic auth or restrict it to the LAN/VLAN, especially once Home Assistant and voice satellites are also talking to it
- If ever exposed externally (e.g. checking the assistant from outside home), use Tailscale/WireGuard rather than port-forwarding

### Multi-user awareness

- HA's Assist pipeline doesn't do voice-ID by default — "who's speaking" isn't distinguished unless explicitly added
- Decide early whether recommendations/preferences are single-profile or need per-person separation, since this affects the Jellyfin/SQLite schema now rather than as a painful migration later

### Observability

- Log prompts, tool calls, and latencies (even just to a file or SQLite table) — useful for debugging "why did it call the wrong tool" once not watching the terminal directly

### Deployment

- A `docker-compose.yml` tying together Ollama, the FastAPI service, and (later) Home Assistant makes the whole stack reproducible and easy to redeploy if hardware changes

### System prompt / personality

- Decide tone and boundaries up front (how terse, how it handles "I don't know," whether it asks clarifying questions) — small thing, but it's what makes it feel like a personal assistant rather than a generic chatbot

### Future features

- Calendar integration
- Shopping lists
- Smart home control (comes free via Home Assistant once integrated)
- Proactive notifications (e.g. "new episode of X is out on Jellyfin")
- play music

---

## Build Order Suggestion

1. Ollama + Qwen3 running locally, verify tool-calling works with a dummy tool
2. FastAPI brain service with weather tool wired in
3. Jellyfin sync (SQLite cache of catalogue + watch history) + recommendation tool
4. Chat frontend as thin client
5. Wyoming voice pipeline (wake word → STT → brain service → TTS)

**Note:** Start the recommendation logic simple (LLM reasoning over curated context) and only add embeddings/vector search once the catalogue-stuffing approach breaks down — it's tempting to over-engineer that part first.