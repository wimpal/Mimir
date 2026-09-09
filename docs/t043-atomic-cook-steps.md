# T-043 — Atomic cook steps on recipe import

**Status:** done — Picnic live smoke 2026-09-09 (8 short steps saved)

## Goal

`homebase.recipes.add` still takes `steps: string[]`. Extraction must emit **one
cook action per element** so cook-through / voice can pace “next” without
splitting prose later. Ingredient `group` (T-049) is unchanged.

## What ships

1. **Prompt** (`config/system_prompt.md`): one verb/action per step; never merge
   `then` / `dan` / `;` / multi-sentence Directions; stir-fry → ≥6 short steps.
2. **Deterministic splitter** (`brain.recipe_import.split_atomic_steps`): after
   number-strip, expand blobs on `;`, `.!?` + whitespace **followed by a capital**,
   and connectors `then` / `and then` / `dan` / `en dan` / `vervolgens`. Called
   only from `normalize_recipe_payload` on `steps[]` — never touches
   ingredients/`group`. Capital requirement keeps `ca. 1 minuut` intact.

## Canonical blob fixture

See `tests/test_recipe_import.py` (`_CHOW_MEIN_STEP_BLOB` and
`test_split_atomic_steps_keeps_abbrev_and_splits_picnic_blob`):

```text
Heat a wok over high heat; add the oil then stir fry the chicken until browned.
Add the vegetables and toss. Pour in the sauce and then cook until glossy! Serve hot.
```

Expected after normalize: **≥6** short steps. Bare `and` / `en` must **not**
split (“salt and pepper”, “peper en zout”).

## Live smoke

- **2026-09-09 Picnic** ketjap-zalm URL → confirm **8** short steps; saved on *yes*.
- Faithful original Picnic paragraphs through the splitter → ~15 atomic steps
  (unit fixture). Model may still invent ingredients on sparse Picnic HTML —
  fidelity is a separate concern.
- Paste with `Voor de dressing` must still show `group` headings (T-049).
