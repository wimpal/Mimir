# Phase 8a — Recently watched

**Status:** implemented

## What landed

| Piece | Role |
|---|---|
| `movies.last_played_at` | Schema v3 nullable UTC ISO from Jellyfin `UserData.LastPlayedDate` |
| `list_recently_watched` | Catalogue tool: titles in a look-back window (default `jellyfin.recent_watched_days` = 14) |
| `recommend_movies` bias | Soft rank boost for genres seen in Recent watches |
| Fail clear | If Catalogue has `played=1` rows but no `last_played_at` anywhere → “play dates missing; run Sync” |

## After upgrade

Run a Jellyfin Sync once so active Catalogue rows get `last_played_at`. Pre-8a catalogues answer “what did I watch?” with the fail-clear message until Sync succeeds.

Box-set–aware recommendations (next in MCU-style sets) are documented in [`phase8a-box-sets.md`](./phase8a-box-sets.md) (schema v4; requires another Sync).

## Config

```yaml
jellyfin:
  recent_watched_days: 14
```

## Exit checklist

- [x] “What did I watch last week?” uses `list_recently_watched` with grounded titles
- [x] Recs soft-bias toward recent genres when dates exist
- [x] Play dates missing → clear error (not empty success)
