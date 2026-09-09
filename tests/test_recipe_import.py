"""Tests for T-021 recipe import staging / confirm."""

from __future__ import annotations

import json

from brain.agent import StoppedReason, run_turn
from brain.mcp.errors import is_write_tool
from brain.mcp.write_guard import check_write_allowed, user_message_requests_write
from brain.ollama import ChatMessage, ChatResponse, ToolCall, ToolCallFunction
from brain.recipe_import import (
    PendingRecipeStore,
    normalize_recipe_payload,
    normalize_step,
    split_atomic_steps,
    user_message_requests_recipe_save,
)
from brain.tools import Tool


def test_is_write_tool_covers_recipes_add() -> None:
    assert is_write_tool("homebase.recipes.add")


def test_recipe_save_phrases_en_nl() -> None:
    assert user_message_requests_recipe_save("Save this recipe: https://example.com/r")
    assert user_message_requests_recipe_save("Bewaar dit recept: https://example.com/r")
    assert user_message_requests_recipe_save("Voeg dit recept toe:\n1. Mix")
    assert user_message_requests_recipe_save("recept opslaan")
    assert user_message_requests_recipe_save(
        "importeer dit recept: https://www.lekkerensimpel.com/fajita-wrap-bowl/"
    )
    assert user_message_requests_write("Save this recipe please")
    assert user_message_requests_write("Bewaar dit recept")
    assert user_message_requests_write(
        "importeer dit recept: https://example.com/r"
    )


def test_meal_plan_does_not_request_recipe_write() -> None:
    assert not user_message_requests_recipe_save("What can I cook tonight?")
    assert not user_message_requests_recipe_save("recept met kip")
    assert not user_message_requests_write("What can I cook tonight?")
    assert not user_message_requests_write("recept met kip")
    assert not user_message_requests_write("wat kunnen we koken")
    err = check_write_allowed(
        "homebase.recipes.add",
        "What can I cook tonight?",
    )
    assert err is not None
    assert "write blocked" in err


def test_recipe_save_negation_does_not_request_write() -> None:
    assert not user_message_requests_recipe_save("don't add this recipe")
    assert not user_message_requests_write("don't add this recipe")
    assert not user_message_requests_write("niet dit recept opslaan")
    assert check_write_allowed(
        "homebase.recipes.add",
        "don't save this recipe",
    ) is not None


def test_confirm_reply_lists_all_steps() -> None:
    from brain.recipe_import import build_recipe_confirm_reply

    payload = {
        "title": "Fajita wrap bowl",
        "servings": 2,
        "ingredients": [{"name": "wrap", "quantity": "2"}],
        "steps": [
            "Vet de schaal in.",
            "Bak de wraps.",
            "Snijd groenten.",
            "Bak de kip.",
            "Vul de bakjes.",
        ],
    }
    text = build_recipe_confirm_reply(
        payload, user_message="importeer dit recept: https://example.com"
    )
    assert "5 stappen" in text
    assert "1. Vet de schaal in." in text
    assert "5. Vul de bakjes." in text
    assert "nog niet opgeslagen" in text


def test_normalize_strips_step_numbers() -> None:
    assert normalize_step("1. Mix flour") == "Mix flour"
    assert normalize_step("2) Heat pan") == "Heat pan"
    payload, err = normalize_recipe_payload(
        {
            "title": "Pannenkoeken",
            "ingredients": [{"name": "bloem", "quantity": "250 g"}],
            "steps": ["1. Mix flour, milk and eggs.", "2. Cook."],
        }
    )
    assert err is None
    assert payload is not None
    assert payload["steps"] == ["Mix flour, milk and eggs.", "Cook."]
    assert not any(s.startswith("1.") for s in payload["steps"])


_CHOW_MEIN_STEP_BLOB = (
    "Heat a wok over high heat; add the oil then stir fry the chicken until browned. "
    "Add the vegetables and toss. Pour in the sauce and then cook until glossy! Serve hot."
)


def test_split_atomic_steps_expands_blob() -> None:
    """T-043: multi-sentence / ; / then blobs become many short steps."""
    parts = split_atomic_steps([_CHOW_MEIN_STEP_BLOB])
    assert len(parts) >= 6
    assert all(len(p) < len(_CHOW_MEIN_STEP_BLOB) for p in parts)
    # Bare "and" must not split (salt and pepper / milk and eggs).
    assert split_atomic_steps(["Season with salt and pepper."]) == [
        "Season with salt and pepper."
    ]
    assert split_atomic_steps(["Mix flour, milk and eggs."]) == [
        "Mix flour, milk and eggs."
    ]


def test_split_atomic_steps_dutch_connectors() -> None:
    parts = split_atomic_steps(
        [
            "Verhit de wok. Voeg olie toe en dan bak de kip. "
            "Voeg groenten toe dan roer. Serveer vervolgens warm."
        ]
    )
    assert len(parts) >= 5
    assert any("Verhit" in p for p in parts)
    assert any("Serveer" in p or "warm" in p.lower() for p in parts)


def test_split_atomic_steps_keeps_abbrev_and_splits_picnic_blob() -> None:
    """T-043 smoke: Picnic-style paragraphs; do not split on ca. 1."""
    picnic = [
        "Breng een pan met water aan de kook. "
        "Kook 300 g rijst volgens de aanwijzingen op de verpakking gaar.",
        "Breng een pan met ruim gezouten water aan de kook. "
        "Snijd 2 broccoli's in roosjes. Snijd 1 bosje bosui in dunne ringetjes. "
        "Houd de witte en groene ringetjes apart. Snijd 2 teentjes knoflook fijn. "
        "Kook de broccoli voor 4 tot 5 minuten.",
        "Verhit in de tussentijd 1 el zonnebloemolie in een koekenpan. "
        "Bak 4 stukken zalm voor 2 tot 3 minuten per kant. "
        "Bak de knoflook en witte gedeelte van de bosui mee. "
        "Blus af met 2 el sojasaus en 4 el ketjap manis. "
        "Laat ca. 1 minuut indikken.",
        "Serveer de rijst met de broccoli en ketjap-zalm. "
        "Garneer met de overgebleven groene bosui ringetjes.",
    ]
    parts = split_atomic_steps(picnic)
    assert len(parts) >= 6
    assert any("Laat ca. 1 minuut indikken" in p for p in parts)
    assert not any(p == "Laat ca." for p in parts)
    # Bare en must not split garnish / salt-and-pepper style phrases.
    assert any("broccoli en ketjap-zalm" in p for p in parts)


def test_normalize_splits_atomic_steps_and_keeps_atomic_list() -> None:
    payload, err = normalize_recipe_payload(
        {
            "title": "Easy Chicken Chow Mein",
            "ingredients": [{"name": "noodles", "quantity": "200 g"}],
            "steps": [_CHOW_MEIN_STEP_BLOB],
        }
    )
    assert err is None
    assert payload is not None
    assert len(payload["steps"]) >= 6

    atomic, err2 = normalize_recipe_payload(
        {
            "title": "Pannenkoeken",
            "ingredients": [{"name": "bloem", "quantity": "250 g"}],
            "steps": [
                "Mix flour, milk and eggs.",
                "Heat a lightly oiled pan.",
                "Pour a ladle of batter.",
            ],
        }
    )
    assert err2 is None
    assert atomic is not None
    assert atomic["steps"] == [
        "Mix flour, milk and eggs.",
        "Heat a lightly oiled pan.",
        "Pour a ladle of batter.",
    ]


def test_normalize_too_many_steps_after_split() -> None:
    # One blob that expands past MAX_STEPS via many sentence fragments.
    from brain.recipe_import import MAX_STEPS

    blob = " ".join(f"Do action number {i}." for i in range(MAX_STEPS + 5))
    payload, err = normalize_recipe_payload(
        {
            "title": "Too many",
            "ingredients": [{"name": "x", "quantity": "1"}],
            "steps": [blob],
        }
    )
    assert payload is None
    assert err is not None
    assert "too many steps" in err


def test_confirm_reply_reflects_expanded_step_count() -> None:
    from brain.recipe_import import build_recipe_confirm_reply

    payload, err = normalize_recipe_payload(
        {
            "title": "Chow Mein",
            "ingredients": [{"name": "noodles", "quantity": "200 g"}],
            "steps": [_CHOW_MEIN_STEP_BLOB],
        }
    )
    assert err is None and payload is not None
    reply = build_recipe_confirm_reply(payload, user_message="Save this recipe")
    n = len(payload["steps"])
    assert f"{n} steps" in reply
    # Preview should include at least the first expanded fragment.
    assert payload["steps"][0] in reply


def test_normalize_preserves_ingredient_groups_when_splitting_steps() -> None:
    """T-049 regression: group/clean names survive T-043 step expansion."""
    payload, err = normalize_recipe_payload(
        {
            "title": "Kip kerrie pastasalade",
            "ingredients": [
                {"name": "pasta", "quantity": "250 g"},
                {"name": "yoghurt", "quantity": "100 gr", "group": "dressing"},
                {"name": "kerriepoeder", "quantity": "1 tl", "group": "dressing"},
                {"name": "peper en zout", "quantity": "to taste", "group": "dressing"},
            ],
            "steps": [_CHOW_MEIN_STEP_BLOB],
        }
    )
    assert err is None
    assert payload is not None
    assert len(payload["steps"]) >= 6
    by_name = {i["name"]: i for i in payload["ingredients"]}
    assert by_name["pasta"].get("group") is None or "group" not in by_name["pasta"]
    assert by_name["yoghurt"]["group"] == "dressing"
    assert by_name["kerriepoeder"]["group"] == "dressing"
    assert by_name["peper en zout"]["group"] == "dressing"
    assert by_name["peper en zout"]["name"] == "peper en zout"
    assert "(voor dressing)" not in by_name["peper en zout"]["name"]


def test_normalize_fills_missing_ingredient_quantity() -> None:
    payload, err = normalize_recipe_payload(
        {
            "title": "Easy Chicken Chow Mein",
            "ingredients": [
                {"name": "lean chicken breasts", "quantity": ""},
                {"name": "200 g chow mein noodles", "quantity": ""},
                {"name": "soy sauce", "quantity": "5 tablespoons"},
                "salt & pepper",
                {"name": "", "quantity": ""},
            ],
            "steps": ["Stir fry.", "Serve."],
        }
    )
    assert err is None
    assert payload is not None
    ings = {i["name"]: i["quantity"] for i in payload["ingredients"]}
    assert ings["lean chicken breasts"] == "to taste"
    assert ings["chow mein noodles"] == "200 g"
    assert ings["soy sauce"] == "5 tablespoons"
    assert ings["salt & pepper"] == "to taste"
    assert len(payload["ingredients"]) == 4


def test_normalize_strips_amount_already_in_quantity() -> None:
    """Model often puts the amount in both fields — UI would double it."""
    from brain.recipe_import import normalize_ingredient

    assert normalize_ingredient(
        {"name": "300 gr kipgehakt", "quantity": "300 gr"}
    ) == {"name": "kipgehakt", "quantity": "300 gr"}
    assert normalize_ingredient(
        {"name": "4-6 wraps (Santa Maria)", "quantity": "4-6"}
    ) == {"name": "wraps (Santa Maria)", "quantity": "4-6"}
    assert normalize_ingredient(
        {"name": "1 tl gedroogde oregano", "quantity": "1 tl"}
    ) == {"name": "gedroogde oregano", "quantity": "1 tl"}
    assert normalize_ingredient(
        {"name": "snufje peper en zout", "quantity": "snufje"}
    ) == {"name": "peper en zout", "quantity": "snufje"}
    assert normalize_ingredient(
        {"name": "1 tomaat", "quantity": "1"}
    ) == {"name": "tomaat", "quantity": "1"}
    # Empty quantity + Dutch "gr" unit
    assert normalize_ingredient(
        {"name": "300 gr kipgehakt", "quantity": ""}
    ) == {"name": "kipgehakt", "quantity": "300 gr"}


def test_normalize_t047_doubled_stems_and_qty_fluff() -> None:
    """T-047: collapse doubled stems; map invented Dutch qty fluff → to taste."""
    from brain.recipe_import import normalize_ingredient

    assert normalize_ingredient(
        {"name": "1/2 komkommer", "quantity": ""}
    ) == {"name": "komkommer", "quantity": "1/2"}
    assert normalize_ingredient(
        {"name": "kom komkommer", "quantity": "1/2"}
    ) == {"name": "komkommer", "quantity": "1/2"}
    assert normalize_ingredient(
        {"name": "komkommer", "quantity": "1/2 kom"}
    ) == {"name": "komkommer", "quantity": "1/2"}
    assert normalize_ingredient(
        {"name": "bosuitjes bosui", "quantity": "2"}
    ) == {"name": "bosuitjes", "quantity": "2"}
    assert normalize_ingredient(
        {"name": "bosui", "quantity": "2 bosuitjes"}
    ) == {"name": "bosuitjes", "quantity": "2"}
    # Protected Dutch bunch unit — do not strip ``bos`` from quantity.
    assert normalize_ingredient(
        {"name": "bosui", "quantity": "1 bos"}
    ) == {"name": "bosui", "quantity": "1 bos"}
    assert normalize_ingredient(
        {"name": "peper en zout", "quantity": "aan de smaak"}
    ) == {"name": "peper en zout", "quantity": "to taste"}
    assert normalize_ingredient(
        {"name": "peper en zout (voor dressing)", "quantity": "te bespreken"}
    ) == {"name": "peper en zout", "quantity": "to taste", "group": "dressing"}
    assert normalize_ingredient(
        {"name": "peper en zout", "quantity": "teugen"}
    ) == {"name": "peper en zout", "quantity": "to taste"}
    assert normalize_ingredient(
        {"name": "kerriepoeder (voor dressing)", "quantity": "1 tl"}
    ) == {"name": "kerriepoeder", "quantity": "1 tl", "group": "dressing"}
    assert normalize_ingredient(
        {"name": "yoghurt", "quantity": "100 gr", "group": "dressing"}
    ) == {"name": "yoghurt", "quantity": "100 gr", "group": "dressing"}
    assert normalize_ingredient(
        {"name": "saus base", "quantity": "1 el", "group": "saus"}
    ) == {"name": "saus base", "quantity": "1 el", "group": "sauce"}
    # Do not collapse distinct ingredients that share a prefix.
    assert normalize_ingredient(
        {"name": "paprika paprikapoeder", "quantity": "1 tl"}
    ) == {"name": "paprika paprikapoeder", "quantity": "1 tl"}
    # Do not rewrite measurement units into the name.
    assert normalize_ingredient(
        {"name": "thee", "quantity": "2 theelepels"}
    ) == {"name": "thee", "quantity": "2 theelepels"}
    assert normalize_ingredient(
        {"name": "aan de smaak peper en zout", "quantity": ""}
    ) == {"name": "peper en zout", "quantity": "to taste"}
    # Stem strip leaving empty qty still defaults to to taste.
    assert normalize_ingredient(
        {"name": "komkommer", "quantity": "kom"}
    ) == {"name": "komkommer", "quantity": "to taste"}


def test_normalize_oversized_group_fails_payload() -> None:
    from brain.recipe_import import RecipeNormalizeError, normalize_ingredient

    try:
        normalize_ingredient(
            {"name": "x", "quantity": "1", "group": "g" * 41}
        )
        raise AssertionError("expected RecipeNormalizeError")
    except RecipeNormalizeError as exc:
        assert "group exceeds 40" in exc.message

    payload, err = normalize_recipe_payload(
        {
            "title": "T",
            "ingredients": [{"name": "x", "quantity": "1", "group": "g" * 41}],
            "steps": ["Do."],
        }
    )
    assert payload is None
    assert err is not None
    assert "group exceeds 40" in err


_KIP_KERRIE_PASTE = """\
Importeer dit recept:
Een lekkere kip kerrie pastasalade met gerookte kip, frisse groenten en een romige kerriedressing.
Bereidingstijd: 25minuten min
Recept voor: 4 personen
Benodigdheden:
300 gr pasta bijvoorbeeld fusilli
300 gr gerookte kipreepjes
150 gr mais (Bonduelle)
1/2 komkommer
2 bosuitjes
150 gr doperwten diepvries
2 tl kerriepoeder
1 tl paprikapoeder
peper en zout

Voor de dressing
100 gr Griekse yoghurt
2 el mayonaise (Thomy)
1 tl kerriepoeder
1 tl honing
1 tl citroensap
peper en zout

Bereidingswijze:
Kook de pasta volgens de aanwijzingen op de verpakking.
"""


def test_merge_subsection_ingredients_recovers_dropped_dressing() -> None:
    """T-049: paste subsection lines are merged with group when the model omits them."""
    from brain.recipe_import import (
        merge_subsection_ingredients_from_source,
        normalize_recipe_payload,
        parse_subsection_ingredients_from_text,
    )

    extras = parse_subsection_ingredients_from_text(_KIP_KERRIE_PASTE)
    assert len(extras) == 6
    assert extras[0]["name"] == "Griekse yoghurt"
    assert extras[0]["quantity"] == "100 gr"
    assert extras[0]["group"] == "dressing"
    assert extras[-1]["name"] == "peper en zout"
    assert extras[-1]["group"] == "dressing"

    main_only = [
        {"name": "pasta bijvoorbeeld fusilli", "quantity": "300 gr"},
        {"name": "gerookte kipreepjes", "quantity": "300 gr"},
        {"name": "mais (Bonduelle)", "quantity": "150 gr"},
        {"name": "komkommer", "quantity": "1/2"},
        {"name": "bosuitjes", "quantity": "2"},
        {"name": "doperwten diepvries", "quantity": "150 gr"},
        {"name": "kerriepoeder", "quantity": "2 tl"},
        {"name": "paprikapoeder", "quantity": "1 tl"},
        {"name": "peper en zout", "quantity": "to taste"},
    ]
    merged = merge_subsection_ingredients_from_source(_KIP_KERRIE_PASTE, main_only)
    assert len(merged) == 15
    from brain.recipe_import import normalize_ingredient

    norms = [normalize_ingredient(i) for i in merged]
    assert all(n is not None for n in norms)
    dressing = [n for n in norms if n and n.get("group") == "dressing"]
    assert len(dressing) == 6
    names = [n["name"] for n in norms if n]
    assert "Griekse yoghurt" in names
    assert "mayonaise (Thomy)" in names
    assert "honing" in names
    assert "citroensap" in names
    assert all("(voor dressing)" not in n for n in names)
    assert sum(1 for n in norms if n and n["name"] == "peper en zout") == 2

    payload, err = normalize_recipe_payload(
        {
            "title": "Kip kerrie pastasalade",
            "servings": 4,
            "ingredients": merged,
            "steps": ["Kook de pasta.", "Serve."],
        }
    )
    assert err is None
    assert payload is not None
    assert len(payload["ingredients"]) == 15


def test_merge_subsection_skips_already_grouped() -> None:
    from brain.recipe_import import merge_subsection_ingredients_from_source

    already = [
        {"name": "pasta", "quantity": "300 gr"},
        {"name": "Griekse yoghurt", "quantity": "100 gr", "group": "dressing"},
        {"name": "mayonaise (Thomy)", "quantity": "2 el", "group": "dressing"},
        {"name": "kerriepoeder", "quantity": "1 tl", "group": "dressing"},
        {"name": "honing", "quantity": "1 tl", "group": "dressing"},
        {"name": "citroensap", "quantity": "1 tl", "group": "dressing"},
        {"name": "peper en zout", "quantity": "to taste", "group": "dressing"},
    ]
    merged = merge_subsection_ingredients_from_source(_KIP_KERRIE_PASTE, already)
    assert len(merged) == 7


def test_merge_subsection_upgrades_ungrouped_dressing_no_dupes() -> None:
    """Model kept dressing without group — merge upgrades, does not append again."""
    from brain.recipe_import import (
        merge_subsection_ingredients_from_source,
        normalize_ingredient,
    )

    fifteen_ungrouped = [
        {"name": "pasta (bijvoorbeeld fusilli)", "quantity": "300 gr"},
        {"name": "gerookte kipreepjes", "quantity": "300 gr"},
        {"name": "mais (Bonduelle)", "quantity": "150 gr"},
        {"name": "komkommer", "quantity": "1/2"},
        {"name": "bosuitjes", "quantity": "2"},
        {"name": "doperwten diepvries", "quantity": "150 gr"},
        {"name": "kerriepoeder", "quantity": "2 tl"},
        {"name": "paprikapoeder", "quantity": "1 tl"},
        {"name": "peper en zout", "quantity": "to taste"},
        {"name": "Griekse yoghurt", "quantity": "100 gr"},
        {"name": "mayonaise (Thomy)", "quantity": "2 el"},
        {"name": "kerriepoeder", "quantity": "1 tl"},
        {"name": "honing", "quantity": "1 tl"},
        {"name": "citroensap", "quantity": "1 tl"},
        {"name": "peper en zout", "quantity": "to taste"},
    ]
    merged = merge_subsection_ingredients_from_source(
        _KIP_KERRIE_PASTE, fifteen_ungrouped
    )
    assert len(merged) == 15
    norms = [normalize_ingredient(i) for i in merged]
    assert norms[8] is not None and "group" not in norms[8]
    assert norms[8]["name"] == "peper en zout"
    assert norms[9] is not None
    assert norms[9]["name"] == "Griekse yoghurt"
    assert norms[9].get("group") == "dressing"
    assert norms[11] is not None and norms[11].get("group") == "dressing"
    assert norms[14] is not None and norms[14].get("group") == "dressing"
    assert sum(1 for n in norms if n and "yoghurt" in n["name"].lower()) == 1
    assert sum(1 for n in norms if n and n["name"] == "peper en zout") == 2


def test_merge_plain_plus_group_twin_with_g_vs_gr() -> None:
    """Live failure: plain yoghurt 100 g + paste extra 100 gr must not double."""
    from brain.recipe_import import merge_subsection_ingredients_from_source

    messy = [
        {"name": "pasta", "quantity": "300 g"},
        {"name": "peper en zout", "quantity": "to taste"},
        {"name": "Griekse yoghurt", "quantity": "100 g"},
        {"name": "mayonaise (Thomy)", "quantity": "2 el"},
        {"name": "Griekse yoghurt (voor dressing)", "quantity": "100 gr"},
        {"name": "mayonaise (Thomy) (voor dressing)", "quantity": "2 el"},
    ]
    merged = merge_subsection_ingredients_from_source(_KIP_KERRIE_PASTE, messy)
    yoghurt = [
        i for i in merged if "yoghurt" in str(i.get("name", "")).lower()
    ]
    assert len(yoghurt) == 1
    assert yoghurt[0].get("group") == "dressing"
    assert "(voor dressing)" not in yoghurt[0]["name"]
    mayo = [i for i in merged if "mayonaise" in str(i.get("name", "")).lower()]
    assert len(mayo) == 1
    assert mayo[0].get("group") == "dressing"
    # Full dressing block recovered; main peper stays + dressing peper.
    assert len([i for i in merged if i.get("group") == "dressing"]) == 6
    assert sum(1 for i in merged if i.get("name") == "peper en zout") == 2
    assert len(merged) == 8  # pasta + main peper + 6 dressing


_KIP_KERRIE_LLM_INGREDIENTS = [
    {"name": "pasta bijvoorbeeld fusilli", "quantity": "300 gr"},
    {"name": "gerookte kipreepjes", "quantity": "300 gr"},
    {"name": "mais (Bonduelle)", "quantity": "150 gr"},
    {"name": "komkommer", "quantity": "1/2"},
    {"name": "bosuitjes", "quantity": "2"},
    {"name": "doperwten diepvries", "quantity": "150 gr"},
    {"name": "kerriepoeder", "quantity": "2 tl"},
    {"name": "paprikapoeder", "quantity": "1 tl"},
    {"name": "peper en zout", "quantity": ""},
    {"name": "Griekse yoghurt", "quantity": "100 gr", "group": "dressing"},
    {"name": "mayonaise (Thomy)", "quantity": "2 el", "group": "dressing"},
    {"name": "kerriepoeder", "quantity": "1 tl", "group": "dressing"},
    {"name": "honing", "quantity": "1 tl", "group": "dressing"},
    {"name": "citroensap", "quantity": "1 tl", "group": "dressing"},
    {"name": "peper en zout", "quantity": "", "group": "dressing"},
]


def test_normalize_t049_fixture_payload_keeps_15_and_dressing_group() -> None:
    """T-049 fixture shape: 15 ingredients, group=dressing, confirm headings."""
    from brain.recipe_import import build_recipe_confirm_reply

    payload, err = normalize_recipe_payload(
        {
            "title": "Kip kerrie pastasalade",
            "servings": 4,
            "ingredients": list(_KIP_KERRIE_LLM_INGREDIENTS),
            "steps": [
                "Kook de pasta.",
                "Spoel af.",
                "Snijd groenten.",
                "Meng dressing.",
                "Meng salade.",
                "Voeg dressing toe.",
                "Koel desgewenst.",
            ],
        }
    )
    assert err is None
    assert payload is not None
    ings = payload["ingredients"]
    assert len(ings) == 15
    names = [i["name"] for i in ings]
    assert names[8] == "peper en zout"
    assert "group" not in ings[8]
    assert names[9] == "Griekse yoghurt"
    assert ings[9]["group"] == "dressing"
    assert "mayonaise (Thomy)" in names
    assert all("(voor dressing)" not in n for n in names)
    assert sum(1 for i in ings if i.get("group") == "dressing") == 6
    assert ings[8]["quantity"] == "to taste"
    assert ings[14]["quantity"] == "to taste"
    assert ings[14]["group"] == "dressing"
    assert all("kom komkommer" not in i["name"] for i in ings)
    assert all("bosuitjes bosui" not in i["name"] for i in ings)

    confirm = build_recipe_confirm_reply(
        payload, user_message="Importeer dit recept: pasta"
    )
    assert "15 ingrediënten" in confirm
    assert "7 stappen" in confirm
    assert "Voor de dressing" in confirm
    assert "- 100 gr Griekse yoghurt" in confirm


def test_stage_then_confirm_dispatches_t049_dressing_groups() -> None:
    """Staged fixture list (incl. group) is what recipes.add receives on ja."""
    calls: list[dict] = []
    store = PendingRecipeStore()
    cid = "conv-recipe-t049"
    recipe_args = {
        "title": "Kip kerrie pastasalade",
        "servings": 4,
        "ingredients": list(_KIP_KERRIE_LLM_INGREDIENTS),
        "steps": [
            "Kook de pasta.",
            "Spoel af.",
            "Snijd groenten.",
            "Meng dressing.",
            "Meng salade.",
            "Voeg dressing toe.",
            "Koel desgewenst.",
        ],
    }

    client1 = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments=recipe_args,
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="Klaar om op te slaan?"),
        ]
    )
    result1 = run_turn(
        client1,
        [ChatMessage(role="user", content="Importeer dit recept: Benodigdheden...")],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert result1.stopped_reason == StoppedReason.FINAL
    assert calls == []
    assert "15 ingrediënten" in (result1.content or "")
    assert "Voor de dressing" in (result1.content or "")
    pending = store.get(cid)
    assert pending is not None
    assert len(pending["ingredients"]) == 15
    dressing = [i for i in pending["ingredients"] if i.get("group") == "dressing"]
    assert len(dressing) == 6
    assert any(i["name"] == "Griekse yoghurt" for i in dressing)
    assert any(i["name"] == "honing" for i in dressing)

    client2 = _ScriptedClient(
        [ChatMessage(role="assistant", content="ignored")],
    )
    result2 = run_turn(
        client2,
        [ChatMessage(role="user", content="ja")],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert result2.stopped_reason == StoppedReason.FINAL
    assert len(calls) == 1
    dispatched = calls[0]["ingredients"]
    assert len(dispatched) == 15
    assert [i["name"] for i in dispatched] == [
        i["name"] for i in pending["ingredients"]
    ]
    assert any(
        i.get("group") == "dressing" and "citroensap" in i["name"].lower()
        for i in dispatched
    )


def test_stage_merges_dressing_when_model_omits_subsection() -> None:
    """Live failure mode: model stages 9 main lines; paste still has Voor de dressing."""
    calls: list[dict] = []
    store = PendingRecipeStore()
    cid = "conv-recipe-t049-merge"
    main_only = [
        {"name": "pasta bijvoorbeeld fusilli", "quantity": "300 g"},
        {"name": "gerookte kipreepjes", "quantity": "300 g"},
        {"name": "mais (Bonduelle)", "quantity": "150 g"},
        {"name": "komkommer", "quantity": "1/2"},
        {"name": "bosuitjes", "quantity": "2"},
        {"name": "doperwten diepvries", "quantity": "150 g"},
        {"name": "kerriepoeder", "quantity": "2 tl"},
        {"name": "paprikapoeder", "quantity": "1 tl"},
        {"name": "peper en zout", "quantity": "to taste"},
    ]
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments={
                                "title": "Kip kerrie pastasalade",
                                "servings": 4,
                                "ingredients": main_only,
                                "steps": ["Kook de pasta.", "Serveer."],
                            },
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="ignored"),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content=_KIP_KERRIE_PASTE)],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert "15 ingrediënten" in (result.content or "")
    assert "Voor de dressing" in (result.content or "")
    pending = store.get(cid)
    assert pending is not None
    assert len(pending["ingredients"]) == 15
    dressing = [i for i in pending["ingredients"] if i.get("group") == "dressing"]
    assert len(dressing) == 6
    assert any("yoghurt" in i["name"].lower() for i in dressing)
    assert any("honing" in i["name"].lower() for i in dressing)
    assert all("(voor dressing)" not in i["name"] for i in pending["ingredients"])


def test_paste_recipe_empty_response_nudges_extract() -> None:
    """First smoke failure: empty Ollama turn must nudge, not die empty_response."""
    calls: list[dict] = []
    store = PendingRecipeStore()
    cid = "conv-recipe-t047-empty"
    client = _ScriptedClient(
        [
            ChatMessage(role="assistant", content=""),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments={
                                "title": "Kip kerrie pastasalade",
                                "servings": 4,
                                "ingredients": list(_KIP_KERRIE_LLM_INGREDIENTS),
                                "steps": ["Kook.", "Serve."],
                            },
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="ignored"),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content=_KIP_KERRIE_PASTE)],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert any(s.anomaly == "recipe_extract_empty" for s in result.steps)
    assert store.has(cid)
    assert "15 ingrediënten" in (result.content or "")


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage]) -> None:
        self._responses = list(responses)

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        return ChatResponse(message=self._responses.pop(0))


def _recipe_add_tool(calls: list[dict]) -> Tool:
    def execute(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        return json.dumps(
            {
                "id": "r1",
                "name": kwargs.get("title"),
                "servings": kwargs.get("servings", 4),
                "ingredients": kwargs.get("ingredients"),
                "steps": kwargs.get("steps"),
                "instructions": "\n".join(kwargs.get("steps") or []),  # type: ignore[arg-type]
                "tags": [],
            }
        )

    return Tool(
        name="homebase.recipes.add",
        description="add recipe",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "servings": {"type": "integer"},
                "ingredients": {"type": "array"},
                "steps": {"type": "array"},
                "source_url": {"type": "string"},
            },
            "required": ["title", "ingredients", "steps"],
        },
        execute=execute,
        service="homebase",
    )


def test_stage_then_confirm_dispatches_steps_list() -> None:
    calls: list[dict] = []
    store = PendingRecipeStore()
    cid = "conv-recipe-1"
    recipe_args = {
        "title": "Pannenkoeken",
        "servings": 4,
        "ingredients": [
            {"name": "bloem", "quantity": "250 g"},
            {"name": "melk", "quantity": "500 ml"},
            {"name": "ei", "quantity": "2"},
        ],
        "steps": [
            "1. Mix flour, milk and eggs.",
            "Heat a lightly oiled pan.",
            "Pour and flip.",
        ],
    }

    # Turn 1: model stages via recipes.add
    client1 = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments=recipe_args,
                        )
                    )
                ],
            ),
            ChatMessage(
                role="assistant",
                content=(
                    "Save Pannenkoeken (4 servings, 3 ingredients, 3 steps)? "
                    "1. Mix flour…"
                ),
            ),
        ]
    )
    result1 = run_turn(
        client1,
        [ChatMessage(role="user", content="Save this recipe: paste body")],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert result1.stopped_reason == StoppedReason.FINAL
    assert calls == []  # not dispatched yet
    assert store.has(cid)
    assert "Pannenkoeken" in (result1.content or "")
    lower = (result1.content or "").lower()
    assert "opgeslagen" not in lower or "nog niet" in lower
    assert "niet opgeslagen" in lower or "not saved" in lower or "nog niet" in lower
    pending = store.get(cid)
    assert pending is not None
    assert isinstance(pending["steps"], list)
    assert pending["steps"][0] == "Mix flour, milk and eggs."

    # Turn 2: bare yes → auto-dispatch + forced saved reply (no Ollama soft Q)
    client2 = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="Goedemorgen! Wat kan ik voor je doen?",
            ),
        ]
    )
    result2 = run_turn(
        client2,
        [ChatMessage(role="user", content="ja")],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert result2.stopped_reason == StoppedReason.FINAL
    assert len(calls) == 1
    assert isinstance(calls[0]["steps"], list)
    assert calls[0]["steps"][0] == "Mix flour, milk and eggs."
    assert not store.has(cid)
    content2 = result2.content or ""
    assert "Pannenkoeken" in content2
    assert "opgeslagen" in content2.lower() or "saved" in content2.lower()
    assert "nog iets" not in content2.lower()
    assert "anything else" not in content2.lower()
    assert "goedemorgen" not in content2.lower()
    assert "good morning" not in content2.lower()
    assert store.get_post_save(cid) is not None
    assert any(s.anomaly == "recipe_saved_forced" for s in result2.steps)


def test_meal_plan_never_stages_add() -> None:
    calls: list[dict] = []
    store = PendingRecipeStore()
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments={
                                "title": "Kip",
                                "ingredients": [{"name": "kip", "quantity": "1"}],
                                "steps": ["Cook."],
                            },
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="Here are ideas with chicken."),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="recept met kip")],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id="c2",
        pending_recipes=store,
        max_iterations=4,
    )
    assert calls == []
    assert not store.has("c2")
    assert any("write blocked" in (m.content or "") for m in result.messages)


def test_suggest_duplicate_title_suffixes() -> None:
    from brain.recipe_import import suggest_duplicate_title

    assert suggest_duplicate_title("Pannenkoeken") == "Pannenkoeken (2)"
    assert suggest_duplicate_title("Pannenkoeken (2)") == "Pannenkoeken (3)"


def test_duplicate_title_restages_confirmable_suffix() -> None:
    calls: list[dict] = []

    def execute(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        title = kwargs.get("title")
        if title == "Pannenkoeken":
            return json.dumps(
                {
                    "error": {
                        "code": "conflict",
                        "message": "Recipe title already exists",
                    }
                }
            )
        return json.dumps(
            {
                "id": "r2",
                "name": title,
                "ingredients": kwargs.get("ingredients"),
                "steps": kwargs.get("steps"),
                "tags": [],
            }
        )

    tool = Tool(
        name="homebase.recipes.add",
        description="add",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
        execute=execute,
        service="homebase",
    )
    store = PendingRecipeStore()
    cid = "dup-1"
    store.set(
        cid,
        {
            "title": "Pannenkoeken",
            "ingredients": [{"name": "bloem", "quantity": "250 g"}],
            "steps": ["Mix."],
        },
    )
    # No Ollama round — conflict forces rename confirm and returns.
    client = _ScriptedClient([])
    result = run_turn(
        client,
        [ChatMessage(role="user", content="ja")],
        tools={"homebase.recipes.add": tool},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert len(calls) == 1
    assert store.has(cid)
    assert store.is_confirmable(cid)
    assert store.get(cid)["title"] == "Pannenkoeken (2)"
    assert "Pannenkoeken (2)" in (result.content or "")
    assert "bestaat al" in (result.content or "").lower() or "already exists" in (
        result.content or ""
    ).lower()
    assert any(s.anomaly == "recipe_duplicate_rename_forced" for s in result.steps)

    # Bare ja again saves under the proposed suffix.
    client2 = _ScriptedClient(
        [ChatMessage(role="assistant", content="Saved as Pannenkoeken (2).")]
    )
    run_turn(
        client2,
        [ChatMessage(role="user", content="ja")],
        tools={"homebase.recipes.add": tool},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=4,
    )
    assert len(calls) == 2
    assert calls[1]["title"] == "Pannenkoeken (2)"
    assert not store.has(cid)


def test_stale_pending_cleared_on_unrelated_turn() -> None:
    store = PendingRecipeStore()
    cid = "stale-1"
    store.set(
        cid,
        {
            "title": "X",
            "ingredients": [{"name": "a", "quantity": "1"}],
            "steps": ["Do."],
        },
    )
    client = _ScriptedClient(
        [ChatMessage(role="assistant", content="It's sunny.")]
    )
    run_turn(
        client,
        [ChatMessage(role="user", content="what's the weather?")],
        tools={},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=2,
    )
    assert not store.has(cid)


def test_url_import_fetch_search_add_fits_in_three_iterations() -> None:
    """Regression: fetch → search → add used to hit MAX_ITERATIONS before confirm."""
    calls: list[dict] = []
    store = PendingRecipeStore()
    cid = "chow-mein-1"
    recipe_args = {
        "title": "Easy Chicken Chow Mein",
        "servings": 4,
        "ingredients": [{"name": "chicken", "quantity": "400 g"}],
        "steps": ["Stir fry noodles.", "Serve hot."],
        "source_url": "https://www.recipesbyanne.com/recipe/easy-chicken-chow-mein/",
    }
    seen_tool_lists: list[list[str]] = []

    def fetch_execute(**_kwargs: object) -> str:
        return (
            "Easy Chicken Chow Mein\nIngredients\nchicken 400 g\n"
            "Directions\n1. Stir fry noodles.\n2. Serve hot."
        )

    def search_execute(**_kwargs: object) -> str:
        return json.dumps({"recipes": []})

    tools = {
        "web.fetch": Tool(
            name="web.fetch",
            description="fetch",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            execute=fetch_execute,
        ),
        "homebase.recipes.search": Tool(
            name="homebase.recipes.search",
            description="search",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
            },
            execute=search_execute,
            service="homebase",
        ),
        "homebase.recipes.add": _recipe_add_tool(calls),
    }

    class _SpyClient(_ScriptedClient):
        def chat(self, messages, tools=None, *, think=False, stream=False):
            names: list[str] = []
            for s in tools or []:
                fn = s.get("function") if isinstance(s, dict) else None
                if isinstance(fn, dict) and fn.get("name"):
                    names.append(str(fn["name"]))
            seen_tool_lists.append(names)
            return super().chat(messages, tools=tools, think=think, stream=stream)

    client = _SpyClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="web.fetch",
                            arguments={
                                "url": (
                                    "https://www.recipesbyanne.com/recipe/"
                                    "easy-chicken-chow-mein/"
                                )
                            },
                        )
                    )
                ],
            ),
            # Post-fetch: search is rejected / not offered — model should add next.
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.search",
                            arguments={"q": "Easy Chicken Chow Mein"},
                        )
                    )
                ],
            ),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments=recipe_args,
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="should not be needed"),
        ]
    )
    result = run_turn(
        client,
        [
            ChatMessage(
                role="user",
                content=(
                    "importeer dit recept: "
                    "https://www.recipesbyanne.com/recipe/easy-chicken-chow-mein/"
                ),
            )
        ],
        tools=tools,
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert calls == []
    assert store.has(cid)
    assert "Easy Chicken Chow Mein" in (result.content or "")
    assert "nog niet opgeslagen" in (result.content or "").lower()
    assert any(s.anomaly == "recipe_confirm_forced" for s in result.steps)
    # After fetch, only recipes.add remains in the Ollama tool list.
    assert any(names == ["homebase.recipes.add"] for names in seen_tool_lists)
    assert any(
        "skip recipes.search" in (m.content or "") for m in result.messages
    )


def test_url_import_max_iterations_salvage_when_not_staged() -> None:
    store = PendingRecipeStore()
    cid = "fetch-only-1"

    def fetch_execute(**_kwargs: object) -> str:
        return "Some Recipe\nIngredients\neggs\nDirections\nCook."

    tools = {
        "web.fetch": Tool(
            name="web.fetch",
            description="fetch",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            execute=fetch_execute,
        ),
        "homebase.recipes.search": Tool(
            name="homebase.recipes.search",
            description="search",
            parameters={"type": "object", "properties": {}},
            execute=lambda **_: json.dumps({"recipes": []}),
            service="homebase",
        ),
    }
    # Burn the recipe-save budget (max 5) without ever staging.
    search_msg = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            ToolCall(
                function=ToolCallFunction(
                    name="homebase.recipes.search",
                    arguments={"q": "Some"},
                )
            )
        ],
    )
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="web.fetch",
                            arguments={"url": "https://example.com/r"},
                        )
                    )
                ],
            ),
            *[search_msg for _ in range(6)],
        ]
    )
    result = run_turn(
        client,
        [
            ChatMessage(
                role="user",
                content="importeer dit recept: https://example.com/r",
            )
        ],
        tools=tools,
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=3,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert not store.has(cid)
    assert "opgehaald" in (result.content or "").lower()


def test_write_pending_does_not_stream_draft_greeting() -> None:
    """Recipe-save turns must not stream 'Good morning' drafts before tools."""
    deltas: list[str] = []
    store = PendingRecipeStore()
    cid = "no-stream-1"
    recipe_args = {
        "title": "Soup",
        "ingredients": [{"name": "water", "quantity": "1 l"}],
        "steps": ["Boil."],
    }
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="Good morning! It's currently 11:05 AM in Amsterdam.",
            ),
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments=recipe_args,
                        )
                    )
                ],
            ),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Save this recipe: paste body")],
        tools={"homebase.recipes.add": _recipe_add_tool([])},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=3,
        on_assistant_delta=deltas.append,
        stream_final=True,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert store.has(cid)
    assert not any("Good morning" in d for d in deltas)
    assert "Soup" in (result.content or "")


def test_stateless_recipe_add_blocked() -> None:
    calls: list[dict] = []
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        function=ToolCallFunction(
                            name="homebase.recipes.add",
                            arguments={
                                "title": "X",
                                "ingredients": [{"name": "a", "quantity": "1"}],
                                "steps": ["Do."],
                            },
                        )
                    )
                ],
            ),
            ChatMessage(role="assistant", content="Need a conversation to save."),
        ]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Save this recipe please")],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=None,
        pending_recipes=PendingRecipeStore(),
        max_iterations=4,
    )
    assert calls == []
    assert any("conversation" in (m.content or "") for m in result.messages)


def test_post_save_ja_clarifies_without_morning_greeting() -> None:
    """T-048: soft follow-up + ja must not become Goedemorgen."""
    store = PendingRecipeStore()
    cid = "post-save-ja"
    store.set_post_save(
        cid,
        title="Kip kerrie pastasalade (2)",
        soft_followup_offered=True,
        dutch=True,
    )
    deltas: list[str] = []
    client = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="Goedemorgen! Wat kan ik voor je doen?",
            ),
        ]
    )
    result = run_turn(
        client,
        [
            ChatMessage(
                role="assistant",
                content=(
                    "Kip kerrie pastasalade (2) is opgeslagen. "
                    "Is er nog iets dat u wilt aanpassen of toevoegen?"
                ),
            ),
            ChatMessage(role="user", content="ja"),
        ],
        tools={},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=2,
        on_assistant_delta=deltas.append,
        stream_final=True,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    content = (result.content or "").lower()
    assert "goedemorgen" not in content
    assert "good morning" not in content
    assert "aanpassen" in content
    assert "kip kerrie" in content
    assert store.get_post_save(cid) is None
    assert not any("Goedemorgen" in d or "Good morning" in d for d in deltas)
    assert any(s.anomaly == "recipe_post_save_clarify" for s in result.steps)


def test_post_save_nee_closes_without_write() -> None:
    store = PendingRecipeStore()
    cid = "post-save-nee"
    store.set_post_save(
        cid,
        title="Soup",
        soft_followup_offered=True,
        dutch=True,
    )
    calls: list[dict] = []
    client = _ScriptedClient(
        [ChatMessage(role="assistant", content="Goedemorgen!")]
    )
    result = run_turn(
        client,
        [
            ChatMessage(
                role="assistant",
                content="Is er nog iets dat u wilt aanpassen of toevoegen?",
            ),
            ChatMessage(role="user", content="nee"),
        ],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=2,
    )
    assert result.stopped_reason == StoppedReason.FINAL
    assert calls == []
    assert "oké" in (result.content or "").lower() or "oke" in (
        result.content or ""
    ).lower()
    assert "goedemorgen" not in (result.content or "").lower()
    assert store.get_post_save(cid) is None
    assert any(s.anomaly == "recipe_post_save_close" for s in result.steps)


def test_pending_confirm_wins_over_post_save_soft_followup() -> None:
    """Rename confirm still dispatches; post-save soft state must not steal ja."""
    store = PendingRecipeStore()
    cid = "pending-wins"
    recipe_args = {
        "title": "Soup (2)",
        "ingredients": [{"name": "water", "quantity": "1 l"}],
        "steps": ["Boil."],
    }
    store.set(cid, recipe_args)
    store.set_post_save(
        cid,
        title="Old Soup",
        soft_followup_offered=True,
        dutch=False,
    )
    calls: list[dict] = []
    client = _ScriptedClient([])
    result = run_turn(
        client,
        [ChatMessage(role="user", content="yes")],
        tools={"homebase.recipes.add": _recipe_add_tool(calls)},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=2,
    )
    assert len(calls) == 1
    assert calls[0]["title"] == "Soup (2)"
    assert "saved" in (result.content or "").lower()
    assert "goedemorgen" not in (result.content or "").lower()
    assert not store.has(cid)


def test_is_soft_followup_offer_heuristic() -> None:
    from brain.recipe_import import is_soft_followup_offer

    assert is_soft_followup_offer(
        "Is er nog iets dat u wilt aanpassen of toevoegen?"
    )
    assert is_soft_followup_offer("Anything else you'd like to change?")
    assert not is_soft_followup_offer("Soup is opgeslagen (3 ingrediënten, 2 stappen).")
    assert not is_soft_followup_offer("Ik kan de stappen aanpassen later.")


def test_dutch_import_bare_yes_keeps_dutch_forced_replies() -> None:
    """T-048 follow-up: bare English yes must not flip NL dialog to EN."""
    calls: list[dict] = []

    def execute(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        title = str(kwargs.get("title") or "")
        if title == "Kip kerrie pastasalade":
            return json.dumps(
                {
                    "error": {
                        "code": "conflict",
                        "message": "Recipe title already exists",
                    }
                }
            )
        return json.dumps(
            {
                "id": "r-nl",
                "name": title,
                "servings": 4,
                "ingredients": kwargs.get("ingredients"),
                "steps": kwargs.get("steps"),
                "tags": [],
            }
        )

    tool = Tool(
        name="homebase.recipes.add",
        description="add",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
        execute=execute,
        service="homebase",
    )
    store = PendingRecipeStore()
    cid = "nl-locale-1"
    store.set(
        cid,
        {
            "title": "Kip kerrie pastasalade",
            "servings": 4,
            "ingredients": [{"name": "pasta", "quantity": "300 gr"}],
            "steps": ["Kook de pasta."],
        },
        dutch=True,
    )

    result_dup = run_turn(
        _ScriptedClient([]),
        [ChatMessage(role="user", content="yes")],
        tools={"homebase.recipes.add": tool},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=2,
    )
    assert "bestaat al" in (result_dup.content or "").lower()
    assert "already exists" not in (result_dup.content or "").lower()
    assert "Kip kerrie pastasalade (2)" in (result_dup.content or "")
    assert store.is_dutch(cid)

    result_save = run_turn(
        _ScriptedClient([]),
        [ChatMessage(role="user", content="yes")],
        tools={"homebase.recipes.add": tool},
        conversation_id=cid,
        pending_recipes=store,
        max_iterations=2,
    )
    content = result_save.content or ""
    assert "opgeslagen" in content.lower()
    assert "personen" in content.lower()
    assert "saved to homebase" not in content.lower()
    assert "servings" not in content.lower()
    assert calls[-1]["title"] == "Kip kerrie pastasalade (2)"
    post = store.get_post_save(cid)
    assert post is not None
    assert post.dutch is True
