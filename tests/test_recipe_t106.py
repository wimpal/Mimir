"""T-106 recipe edit / tag / calories / shopping write-guard tests."""

from __future__ import annotations

from brain.mcp.write_guard import (
    check_write_allowed,
    user_message_requests_write,
)
from brain.recipe_import import (
    calories_answer_from_recipe,
    merge_recipe_get_with_deltas,
    user_message_requests_recipe_edit,
    user_message_requests_recipe_to_shopping,
)


def test_recipe_edit_phrases_request_write() -> None:
    assert user_message_requests_recipe_edit("Edit the pastasalade")
    assert user_message_requests_recipe_edit("Pas het recept aan")
    assert user_message_requests_recipe_edit("Tag it as lunch")
    assert user_message_requests_recipe_edit("Zet calorieën op 450")
    assert user_message_requests_recipe_edit("Mark chili optional")
    assert user_message_requests_write("Update the recipe steps")
    assert check_write_allowed(
        "homebase.recipes.update",
        "Edit the pasta salad recipe",
    ) is None
    assert check_write_allowed(
        "homebase.recipes.update",
        "recept met kip",
    ) is not None
    assert check_write_allowed(
        "homebase.recipes.update",
        "yes",
        recipe_pending=True,
    ) is None


def test_lunch_and_calories_are_read_only() -> None:
    assert not user_message_requests_write("Lunch recipes")
    assert not user_message_requests_write("recepten voor lunch")
    assert not user_message_requests_write("How many calories in pastasalade?")
    assert not user_message_requests_write("Hoeveel calorieën heeft de pastasalade?")


def test_no_invented_nutrition() -> None:
    assert calories_answer_from_recipe({"calories": 450}) == "450.0"
    assert calories_answer_from_recipe({"name": "x"}) is None
    assert calories_answer_from_recipe(None) is None


def test_merge_update_payload_preserves_explicit_nutrition_only() -> None:
    base = {
        "id": "abc",
        "name": "Pastasalade",
        "servings": 4,
        "ingredients": [{"name": "pasta", "quantity": "200 g"}],
        "steps": ["Boil"],
        "tags": ["lunch"],
        "calories": 400,
    }
    merged = merge_recipe_get_with_deltas(base, {"tags": ["lunch", "dinner"]})
    assert merged["title"] == "Pastasalade"
    assert merged["id"] == "abc"
    assert merged["tags"] == ["lunch", "dinner"]
    assert merged["calories"] == 400
    cleared = merge_recipe_get_with_deltas(base, {"calories": None})
    assert cleared.get("calories") is None


def test_recipe_shopping_confirm_path() -> None:
    assert user_message_requests_recipe_to_shopping(
        "Add the pastasalade to the shopping list"
    )
    assert user_message_requests_write(
        "Zet pastasalade op de boodschappenlijst"
    )
    assert check_write_allowed(
        "homebase.shopping_list.add_item",
        "Add the pastasalade to the shopping list",
    ) is None
    assert check_write_allowed(
        "homebase.shopping_list.add_item",
        "what's for dinner?",
    ) is not None
