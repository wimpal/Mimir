# Model profiles + Active model (T-074)

Named **Model profiles** let you trial other local Ollama tags without
hand-editing the daily-driver default each time. The **Active model** is the
profile in use right now; it stays selected across brain restarts until you
explicitly switch (ADR-016).

## Config

In `config/config.yaml` (see `config/config.example.yaml`):

```yaml
ollama:
  url: http://127.0.0.1:11434
  model: granite4.2:8b     # inheritance defaults when a profile omits a field
  num_ctx: 16384
  think: false
  keep_alive: 45m
  profiles:
    default:
      model: granite4.2:8b
      num_ctx: 16384
    qwen3_8b:
      model: qwen3:8b
      num_ctx: 8192
```

- Profile `default` is required when `profiles` is present.
- Sticky Active name is stored in `{data_dir}/active_model.json` — not in YAML.
- Missing state file → Active = `default`.

## Switch

### CLI

```text
uv run python -m brain.model_profiles list
uv run python -m brain.model_profiles show
uv run python -m brain.model_profiles use trial_14b --restart
uv run python -m brain.model_profiles use default --restart
```

`--restart` runs `scripts/restart_mimir.ps1 -BrainOnly` on Windows. Elsewhere,
restart the brain yourself after `use`.

### Desktop GUI (T-089)

`/settings` → **Active model** dropdown. On loopback Windows the GUI saves sticky
Active then runs `restart_mimir.ps1 -BrainOnly` and waits until loaded matches.
Remote brain URL: sticky is saved; restart the brain on the host yourself.

HTTP (Bearer when token mode): `GET /v1/model-profiles`,
`PUT /v1/model-profiles/active` — see `project-control-heim/contracts/mimir.client.md`.

Pull the tag first: `ollama pull qwen3:14b`.

## Suite gate

```text
uv run python scripts/tool_call_suite.py
```

Banner includes `model=` and `active_profile=`. Keep the ≥80% bar before vibe
testing a trial (T-075 bake-off).

Batch several profiles (skips tags not present in Ollama when `--pulled-only`):

```text
uv run python scripts/suite_profiles.py --pulled-only
```

Per-profile logs land in `data/logs/suite_<profile>_<stamp>.log`; scoreboard in
`data/logs/suite_batch_<stamp>.txt`. Restores Active `default` at the end unless
`--no-restore-default`. On Windows, prefer this over a hand-rolled PowerShell
loop (avoids Tee-Object hangs; sets UTF-8 for suite previews).

## Env escape hatch

`MIMIR_OLLAMA_MODEL` / `NUM_CTX` / `THINK` / `KEEP_ALIVE` still override the
*effective* fields after Active resolve (containers / one-off ops). They are
**not** the sticky store — `show` reports `env_mask=…` when they apply.

## Check what’s loaded

- Startup log: `brain ready model=… active_profile=…`
- `GET /health` → `ollama.model` is the effective Active tag
- `uv run python -m brain.model_profiles show`

## Suite autopsy (T-090)

```text
uv run python scripts/tool_call_suite.py --json-out data/logs/suite_autopsy.json
uv run python scripts/suite_profiles.py granite42_8b
```

Per-profile logs: `data/logs/suite_<profile>_<stamp>.log` and
`data/logs/suite_<profile>_<stamp>_autopsy.json`. Batch restores the prior Active
profile (not always `default`).
