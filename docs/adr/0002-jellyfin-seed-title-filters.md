# Seed-title filters, not embeddings, for “like X”

Phase 5 needs “something like Blade Runner I haven’t seen” without a vector index. We decided: resolve a Seed title in the Catalogue (exact case-insensitive, else unique contains-match, else return ambiguous candidates), then filter by overlapping genres and similar cheap metadata into a Catalogue subset for the LLM to pick. Missing seed → say so and fall back to genre/mood filters. Embeddings / `sqlite-vec` stay deferred until filter quality breaks.

**Considered options:** LLM-only “like X” over a genre subset (no seed lookup); require the seed to exist with no fallback; vectors from day one.
