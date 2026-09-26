---
name: tool-call-health
description: >-
  Run Mimir's full tool-call suite, triage failures, and present a fix plan.
  Use when the user asks for tool-call health, suite triage, tool-calling
  regression diagnosis, or a plan after model / prompt / schema / num_ctx changes.
disable-model-invocation: true
---

# Tool-call health

Diagnose tool-calling reliability. **Plan only** — do not implement fixes unless the user explicitly asks after reviewing the plan.

## Prerequisites

- Working directory: Mimir repo root
- Live Ollama reachable at the Active profile in config
- `config/system_prompt.md` and local config present (`uv run` can load settings)

If Ollama or config is missing, stop and report that — do not invent results.

## Workflow

```
Progress:
- [ ] 1. Run suite
- [ ] 2. Triage failures
- [ ] 3. Decide what needs fixing
- [ ] 4. Present fix plan
```

### 1. Run suite

Run the **full** suite (all cases — never `--case` unless the user scoped it):

```text
uv run python scripts/tool_call_suite.py --json-out data/logs/suite_autopsy.json
```

Capture stdout/stderr and exit code. Completion: summary block printed and autopsy JSON written (or a hard infra failure explained).

Suite wall-clock is often many minutes — wait for it; do not abort early.

### 2. Triage failures

Read `data/logs/suite_autopsy.json` plus the printed summary.

Record:

| Field | Source |
|---|---|
| Banner | `model`, `active_profile`, `num_ctx`, `think` |
| Gate | `pass_pct` vs 80% viability (`EXIT OK` / `EXIT FAIL`) |
| Metrics | `right_tool`, `valid_args`, `result_used` |
| Failures | `failure_ids`, `failures_by_class`, per-case `reason` / `tool_sequence` / `content` |

Group failures by `failure_class` (or `reason` if class empty). Prefer classes that share one root cause over one-off flukes.

Known classes: `no_tool_when_required`, `unexpected_tool`, `malformed_args`, `tool_not_used_in_answer`, `format_xml_leakage`, `empty_response`, `ollama_error`, `max_iterations`, plus case-specific `reply_*` / `missing_*` / `english_*`.

For each class, open the failing case definition in `scripts/tool_call_suite.py` and the checker expectations — distinguish **model/prompt/schema** issues from **suite-checker or fixture** bugs.

### 3. Decide what needs fixing

Viability bar: overall pass ≥ **80%**. Quality bar: drive the three metrics up; aim for zero systematic classes.

Nothing to fix when: exit 0, no failures, metrics clean. Say so and stop — empty plan.

Otherwise classify each failure cluster:

| Likely layer | When |
|---|---|
| System prompt | Wrong tool / no tool / result not grounded / XML leakage |
| Tool schema or descriptions | Valid tool, bad args; systematic arg mistakes |
| `num_ctx` / `think` | Truncation, multi-tool collapse, think=true regressions |
| Agent / tool loop | `max_iterations`, empty responses, loop bugs |
| Suite checker / fixture | Checker too strict or fixture drift vs real tool output |
| Model swap | Viability still failing **after** one round of prompt/schema/`num_ctx` fixes |

Follow project order: prompt → schema → `num_ctx`/`think` → loop code → model swap. Do not jump to model swap first.

Mark clusters **fix now** vs **monitor** (single flaky case under viability, no shared class).

### 4. Present fix plan

Use this template:

```markdown
# Tool-call health — <date>

## Verdict
- Gate: PASS|FAIL (<passed>/<total>, <pct>%) — viability ≥80%
- Profile: <active_profile> / <model> · num_ctx=<n> · think=<bool>
- Metrics: right_tool=<…> valid_args=<…> result_used=<…>

## Failures
| Class | Cases | Metric hit | Suspected layer |
|---|---|---|---|
| … | id, id | right_tool\|valid_args\|result_used | prompt\|schema\|… |

## Plan
1. **<title>** — <why> · touch: `<paths>` · done when: <re-run criterion>
2. …

## Out of scope / monitor
- …

## Next command after fixes
uv run python scripts/tool_call_suite.py --json-out data/logs/suite_autopsy.json
```

Rules for the plan:

- Ordered, smallest change first; one cluster per step when possible
- Name concrete files (`config/system_prompt.md`, tool modules, suite checkers)
- Each step has a **done when** that re-runs the suite (full, or `--case` for a tight mid-step check then full at the end)
- No implementation in this skill run
- If viability fails and prompt/schema/`num_ctx` already look exhausted, the plan’s last step is a **named** model-profile trial (`docs/model-profiles.md`), not an open-ended model hunt

## Done

Skill is complete when the user has the verdict + plan (or a clean bill of health). Implementation is a separate turn.
