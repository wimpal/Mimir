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

    # Turn 2: bare yes → auto-dispatch
    client2 = _ScriptedClient(
        [
            ChatMessage(
                role="assistant",
                content="Saved Pannenkoeken with 3 ingredients and 3 steps.",
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
