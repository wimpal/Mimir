# Phase 1 — Tool-calling proof results

Measured against live **Ollama + `qwen3:8b`** on the Windows dev box (AMD 9070 XT).

## Standing regression (not a one-off)

Re-run whenever **model**, **system prompt**, **tool schemas**, or **`num_ctx` / `think`** change:

```powershell
uv sync
uv run python scripts/tool_call_suite.py
```

Unit tests (no Ollama):

```powershell
uv run pytest tests/test_ollama_client.py tests/test_tools.py tests/test_agent_loop.py -q
```

Exit code `0` = **viability** bar (≥80% overall on ≥10 fixed cases). That answers “is this model usable?” — not “is quality good enough forever.” Aim to improve the three metrics below before wiring many real tools.

Run from the repo root (system prompt path is required). Config must set `ollama.num_ctx` explicitly (default 8192) so failures aren’t silent truncation.

## Metrics to track

| Metric | Meaning |
|---|---|
| Right tool | Required tool called; unexpected tools not called |
| Valid arguments | Schema-ok args |
| Result used | Final answer grounded in tool output |

The suite prints separate rates for each metric on every run (not only a blended pass rate), e.g.:

```text
right_tool=17/17 (100%) valid_args=17/17 (100%) result_used=17/17 (100%)
```

Reason codes still explain individual failures (`no_tool_when_required`, `unexpected_tool`, `malformed_args`, `tool_not_used_in_answer`, …).

## GPU offload (Phase 0 exit)

After loading the model, confirm layers are on GPU (AMD path):

```powershell
ollama run qwen3:8b "ping"
ollama ps
```

| Field | Expected |
|---|---|
| `PROCESSOR` | shows GPU (e.g. `100% GPU` or mixed with GPU) — **not** `100% CPU` |
| Recorded (2026-08-25) | `qwen3:8b` · `PROCESSOR=100% GPU` · `CONTEXT=8192` · size ~6.3 GB |

If CPU-only, follow ROADMAP Risk 7 before judging quality.

## Measured run (2026-08-25, with `num_ctx=8192`)

| Metric | Value |
|---|---|
| Model | `qwen3:8b` |
| Cases | 12 |
| Pass rate | **12/12 (100%)** — viability GO |
| Latency p50 | ~475 ms wall |
| Latency p95 | ~961 ms wall |
| `think` | `false` |
| `num_ctx` | **8192** |

First call in a process can be multi-second (model warm-up); this run’s first case was ~5.8 s, then sub-second to ~1 s.

Earlier runs (before / during prompt tightening) saw ~83–92% with occasional hallucinated times or echo-without-grounding. Treat those as known failure modes to watch for, even when a given run is clean.

## Failure modes observed (across runs)

| Code | Notes |
|---|---|
| `no_tool_when_required` | Time asked → sometimes **hallucinated** timestamp with **zero** tool calls; or multi-step phrasing that only called `echo`. |
| `tool_not_used_in_answer` | Tool ran but reply didn’t quote the result (mitigated by prompt). |

Other codes: `unexpected_tool`, `malformed_args`, `max_iterations`, `ollama_error`, `empty_response`.

### Qualitative notes

- Time questions usually select `get_server_time` and ground the answer in the ISO payload.
- Pure knowledge questions correctly skip tools.
- Vague prompts ask for clarification — no crash.
- Multi-step chaining is more reliable with an explicit “get time, then echo” prompt than with short phrasing alone.

## Go / no-go for Phase 2

**GO.** Viability met with `num_ctx` set. Do **not** swap models before Phase 2.

Named fallbacks if a future suite run fails viability after prompt/`num_ctx` fixes (verify each with this suite): `qwen2.5:14b`, `llama3.1:8b`, `mistral-nemo`.

Watch in Phase 2+: hallucinated values when a tool should have been called; answers that acknowledge tools without quoting results.

## Code landed

| Piece | Path |
|---|---|
| Ollama client | `brain/ollama.py` (`num_ctx` + `think`) |
| Dummy tools | `brain/tools/` |
| Tool loop | `brain/agent.py` |
| Eval suite | `scripts/tool_call_suite.py` |
