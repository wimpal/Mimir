# Phase 8e — Morning brief

**Status: done** (phrase-triggered weather + today’s schedule via normal chat).

## Behaviour

- Trigger: morning greetings such as “good morning” / “goodmorning” /
  “morning” / Dutch “goedemorgen” (standalone or leading the turn; spacing
  does not matter) — **not** a `/morning` slash command, **not** a proactive
  push
- Reply language matches the user (English vs Dutch, etc.)
- Reply opens with a short greeting in that language (“Good morning” /
  “Goedemorgen”, optionally “… sir” / “… meneer”), then **weather + today’s
  schedule only** (no news, no movie digression)
- Calendar feed `context` / per-event `calendar_context` used when paraphrasing
  (e.g. work “filmen” = shoot, not watching a movie)
- Path: normal chat → model calls `get_weather` and `get_calendar` (prefer both
  in one step) → short reply grounded in tool output
- Same path later when spoken in Phase 10 (no client special-case)
- TUI shows one dim tool summary line under the reply (`get_weather · get_calendar`)

See domain term **Morning brief** in [`CONTEXT.md`](../CONTEXT.md).

## Tools reused

| Tool | Phase |
|---|---|
| `get_weather` | 3 — [`phase3-weather.md`](./phase3-weather.md) |
| `get_calendar` | 8d — [`phase8d-calendar.md`](./phase8d-calendar.md) |

No new tools, endpoints, or TUI commands.

## Prompt

[`config/system_prompt.md`](../config/system_prompt.md): dual-tool morning
greeting discipline, language match, Style examples (EN/NL).

## Exit checks

Pinned suite cases `morning_1`, `morning_2`, `morning_4` in `scripts/tool_call_suite.py`
(both tools required; reply grounded in weather and calendar payloads; `morning_4`
checks Dutch greeting with no English mid-reply).

```powershell
uv run python scripts/tool_call_suite.py
```

Manual: type `Good morning` / `Goedemorgen` in the TUI; confirm a short brief
that greets in the right language and reflects weather + today’s events (or
clear failure / stale notes when tools say so).
