# Box-set–aware recommendations

**Status:** implemented (extends Phase 8a Recent watches)

## Problem

Recent watches correctly listed MCU titles, but `recommend_movies` only soft-biased by **genre**, so suggestions stayed in-genre and mostly **outside** the Marvel Box set.

## What landed

| Piece | Role |
|---|---|
| Sync | After movie libraries, list Jellyfin `BoxSet`s and members; attach to Catalogue movies (`box_set_ids_json`, schema **v4**) |
| Soft-fail | Box set fetch errors log and continue; movies still publish |
| Recommend head | Dominant Box set among Recent watches → up to 3 next unwatched by `ProductionYear` |
| Recommend tail | Existing genre/seed ranking, **excluding** that Box set’s other members |
| Payload | `box_set_next: true` on head titles; `filters.box_set_*` for observability |

## After upgrade

Restart the brain (schema → 4), then **Sync again** so Box set links populate. Without a Sync, recommendations behave as Phase 8a genre-only.
