# Phase 3 — Weather tool

**Status: done** (Open-Meteo + KNMI HARMONIE for NL home coords).

## Provider

- Transport: [Open-Meteo Forecast API](https://open-meteo.com/en/docs) (`/v1/forecast`)
- Model: `knmi_harmonie_arome_netherlands` (2 km KNMI HARMONIE AROME)
- No API key; soft network dependency — fail fast within `timeouts.tool_s`
- Config: `location.latitude` / `longitude` / `timezone` (default `Europe/Amsterdam`)

Buienradar-style 5-minute rain nowcast remains Phase 11 backlog.

## Tool

`get_weather` — no arguments; home location from server config.

Compact JSON for the LLM includes:

- `current` — temp °C, conditions (WMO label), precip, humidity, wind
- `today` / `tomorrow` — max/min, precip sum, conditions
- `next_hours_precip` — short hourly precip slice
- `source`: `open-meteo/knmi`

## Wiring

- `brain/tools/weather.py` — fetch + normalize
- `build_registry(settings)` — dummies + weather
- `BrainService` passes the registry into `run_turn`

## Exit checks

Pinned suite cases (`weather_1`…`weather_5`) in `scripts/tool_call_suite.py`:

1. Weather today → tool + grounded answer
2. Umbrella → tool + precip/conditions used
3. Tomorrow’s forecast → tomorrow fields used
4. Offline override → clear failure (no hang)
5. Echo control → does not invent weather via `get_weather`

Re-run after model / prompt / schema / `num_ctx` / `think` changes:

```powershell
uv run python scripts/tool_call_suite.py
```

Measured on 2026-08-25 (qwen3:8b, `num_ctx=8192`, `think=false`): **16–17/17** overall (≥80% OK); **5/5** weather pinned after `weather_5` control check.

Suite summary now reports `right_tool` / `valid_args` / `result_used` separately. Weather grounding requires temperature, conditions, or precip tokens from the tool JSON (not bare `°` / `celsius` alone).