# T-050 — web.fetch keeps Recipe JSON-LD for URL import

**Status:** done — E2E accepted 2026-09-09 (Picnic ketjap-zalm chat import)

## Goal

URL recipe import must see real `recipeInstructions` / `HowToStep` (and
ingredients) from fetched pages. If `simplify_body` drops them, the model
invents Bereiding and garnish.

## What ships

In `brain/tools/web_fetch.py`:

1. Collect classic `<script type="application/ld+json">` Recipe payloads.
2. Also recover Recipe JSON embedded in Next.js flight scripts
   (`self.__next_f.push([1,"{...}"])`) — Picnic’s actual shape.
3. Emit plain-text **Ingredients** + **Directions** in source order, prepended
   ahead of stripped page chrome.
4. Truncation prefers structured Directions over chrome; SSRF unchanged;
   `MAX_BODY_BYTES` = 2 MiB (Picnic HTML ~757 KiB).

Tests: `tests/test_web_fetch.py` (HowToStep order, `@graph` / HowToSection,
Next.js escape, truncation).

Contract: additive note on Save-recipe URL row in
`project-control-heim/contracts/mimir.client.md` (no Homebase schema change).

## Live acceptance

Chat: *importeer dit recept* +
`https://picnic.app/nl/recepten/6941193067f37538a871bc33/ketjap-zalm-met-broccoli-en-rijst`

- Confirm: **8 ingredients / 15 steps**, rice-first (Breng een pan… → bak zalm).
- Ingredients: rijst, broccoli, bosui, zalmfilet, sojasaus, ketjap manis,
  zonnebloemolie, knoflook — no sinaasappel / paprika / zeewier.
- *yes* → saved in Homebase with the same lists.

Atomic splitting (T-043) still applies on the recovered blobs.

## Out of scope

Headless browser render; Homebase schema changes.
