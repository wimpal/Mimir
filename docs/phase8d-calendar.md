# Phase 8d — Calendar feed (ICS URL)

**Status: done** (provider-agnostic ICS subscribe URL(s); ADR 0007).

## Provider

- Transport: HTTP GET of configured **Calendar feed(s)** (ICS subscribe URL)
- No provider SDK — Proton share link first; Google/Fastmail/etc. by swapping the URL
- **Multiple named feeds:** `calendar.feeds` in YAML (`id` + display `name` +
  optional `context` note for the LLM); secrets
  `CALENDAR_ICS_URL_<ID>` (ID uppercased, hyphens → underscores)
- **Legacy single feed:** `CALENDAR_ICS_URL` only when `feeds` is empty (not a fallback
  beside named feeds)
- Optional per-feed basic auth: `CALENDAR_ICS_USERNAME_<ID>` / `CALENDAR_ICS_PASSWORD_<ID>`
- Soft network dependency — fail fast within `timeouts.tool_s`
- Per-feed TTL cache (`calendar.cache_ttl_s`, default 300s), **weather-like**: always
  fetch; on failure serve last success only if still within TTL (`stale=true`); expired
  or missing cache → loud error
- **Publish lag:** some publishers (e.g. Proton share links) can lag up to ~8h; tool JSON
  includes `lag_note`

Names are **config**, not Preferences (`/settings`).

## Tool

`get_calendar` — no arguments; always the **full calendar day** in
`location.timezone` (midnight→midnight) across all configured feeds.

Compact JSON for the LLM includes:

- `timezone` / `window` — query bounds
- `events` — `{summary, start, end, all_day, location?, calendar, calendar_name, calendar_context?}`
- `feeds` — per-feed ok/stale/fetched_at (or error); optional `context` from config
- `errors` — optional list when a feed fails but others succeed
- `fetched_at` / `stale` / `lag_note`

## Wiring

- `brain/tools/calendar.py` — fetch + parse + merge
- `brain/calendar_cache.py` — `{data_dir}/cache/calendar_<id>.json`
- `build_registry(settings)` — always registers `get_calendar`

## Config

```yaml
calendar:
  cache_ttl_s: 300
  feeds:
    - id: family
      name: Fam Palland
      context: Shared household / family schedule
    - id: personal
      name: Personal
```

```env
CALENDAR_ICS_URL_FAMILY=
CALENDAR_ICS_URL_PERSONAL=
# or legacy single (only when feeds: []):
# CALENDAR_ICS_URL=
```

## Exit checks

Pinned suite cases (`calendar_1`…`calendar_3`) in `scripts/tool_call_suite.py`.

```powershell
uv run python scripts/tool_call_suite.py
```

Phase 8e (morning brief) reuses this tool.
