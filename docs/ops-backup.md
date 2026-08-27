# Backup and restore (`data_dir`)

Mimir stores SQLite, turn logs, and the Forecast cache under `runtime.data_dir`
(default `./data`). Retention is keep-all for single-user v1 unless disk hurts —
no auto-prune.

## What to copy

| Path | Contents |
|---|---|
| `{data_dir}/mimir.db` | Conversations, Messages, Preferences, Catalogue |
| `{data_dir}/logs/` | Turn traces (`turns.jsonl`), brain launch log |
| `{data_dir}/cache/` | Forecast cache (`weather.json`) — optional |

## Snapshot

1. Prefer a brief pause: stop the brain (or avoid chat/Sync for a moment).
2. Copy the files above to your backup location (same relative layout helps).
3. Restart the brain if you stopped it.

On Linux compose, back up the mounted volume that maps to `MIMIR_DATA_DIR`.

## Restore

1. Stop the brain.
2. Replace `{data_dir}/mimir.db` (and optionally `logs/`, `cache/`) from the backup.
3. Start the brain; confirm `GET /health` shows `db.ok` and expected schema version.

There is no backup script in Phase 7 — a one-shot helper can land with Phase 9 packaging if needed.
