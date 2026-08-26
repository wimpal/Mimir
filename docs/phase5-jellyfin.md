# Phase 5 — Jellyfin Catalogue Sync + recommend_movies

**Status:** implemented  
**Auth mode:** Jellyfin **API key** via env `JELLYFIN_API_KEY`, sent as `X-Emby-Token` (Emby-compatible header).

## What landed

| Piece | Role |
|---|---|
| `brain/jellyfin_client.py` | Paginated `Users/{userId}/Items` (movies only, `EnableUserData=true`) |
| `brain/jellyfin_sync.py` | Single-flight SyncManager; atomic generation publish |
| `brain/db.py` | `schema_version=2`: `movies` + `jellyfin_sync_state` |
| `brain/tools/recommend.py` | `recommend_movies` — Catalogue subset for the LLM |
| `POST /v1/jellyfin/sync` | On-demand Sync (HTTP only; not a chat tool) |
| `GET /health` → `jellyfin_sync` | Informational last-run / stale / count |

## Config

```yaml
jellyfin:
  url: http://…
  user_id: "<Jellyfin user GUID>"
  library_ids: ["<movie library GUID>", …]
  sync_interval_hours: 24   # 0 = periodic off
  page_size: 100
timeouts:
  jellyfin_sync_s: 300
```

Secrets: `JELLYFIN_API_KEY`. Env overrides: `MIMIR_JELLYFIN_URL`, `MIMIR_JELLYFIN_USER_ID`, `MIMIR_JELLYFIN_LIBRARY_IDS` (comma-separated), etc.

Sync runs only when url + api_key + user_id + non-empty `library_ids` are set. `recommend_movies` registers whenever SQLite is available (serves last-good Catalogue offline).

## Sync behavior

1. Allocate staging `sync_generation`.
2. Paginate each allowlisted library; upsert into that generation.
3. On success: set `active_generation`, delete older generations.
4. On failure: abandon staging generation; leave previous Catalogue intact.

Periodic Sync: background task after startup (if configured and catalogue empty/stale), then every `sync_interval_hours`. On-demand: `POST /v1/jellyfin/sync` (`503` not configured, `409` busy, `200` with result).

## recommend_movies

Args: `seed_title?`, `genre?`, `mood?` (cozy/scary/funny/thoughtful/action → genres), `unwatched_only` (default true), `min_rating?`.

- Seed resolved against full Catalogue first (watched seeds still work), then candidates filtered.
- Watched = Jellyfin Played/completed only; in-progress stays eligible.
- Cap 20 rows; overviews truncated. `favorite_genres` Preference is **not** forced in SQL.
- Empty Catalogue → `error: catalogue empty; sync required`.
- Stale flag when last success older than interval or last attempt failed.

## Failure modes

| Situation | Behavior |
|---|---|
| Jellyfin down during Sync | Clear error; last-good Catalogue kept |
| Jellyfin down during chat | Recommend from Catalogue (may be `stale`) |
| Never synced | Recommend returns catalogue-empty error |

## Exit checks

- Sync fills SQLite (paginated).
- 5 pinned suite cases (`jellyfin_*`) + tool suite still green.
- Unit/API tests cover client params, generation publish, recommend seed order, sync HTTP codes.
