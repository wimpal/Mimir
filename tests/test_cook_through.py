"""Tests for conversational cook-through (T-095)."""

from __future__ import annotations

import json
from pathlib import Path

from brain.agent import StoppedReason, run_turn
from brain.cook_through import (
    advance_step,
    extract_cook_recipe_query,
    format_step_reply,
    get_active_progress,
    is_cook_next,
    is_cook_prev,
    is_cook_repeat_step,
    is_cook_start,
    is_cook_stop,
    parse_search_hits,
    resolve_recipe_from_hits,
    seed_cook_session,
    stop_cook_session,
)
from brain.db import Database
from brain.ollama import ChatMessage, ChatResponse
from brain.recipe_import import PendingRecipeStore
from brain.repeat_last import is_repeat_intent
from brain.tools import Tool


def test_cook_start_phrases() -> None:
    assert is_cook_start("Cook the pastasalade")
    assert is_cook_start("cook pastasalade")
    assert is_cook_start("Walk me through the pastasalade")
    assert is_cook_start("Guide me through pasta salad")
    assert is_cook_start("Step by step the pastasalade")
    assert is_cook_start("Kook de pastasalade")
    assert is_cook_start("Stap voor stap pastasalade")
    assert is_cook_start("Loop me door de pastasalade")
    assert extract_cook_recipe_query("Cook the pastasalade") == "pastasalade"
    assert extract_cook_recipe_query("Kook de pastasalade!") == "pastasalade"


def test_cook_start_negatives_full_list_and_writes() -> None:
    assert not is_cook_start("Show me the pastasalade")
    assert not is_cook_start("Toon het recept pastasalade")
    assert not is_cook_start("List the steps for pastasalade")
    assert not is_cook_start("How do I make pastasalade")
    assert not is_cook_start("Hoe maak ik pastasalade")
    assert not is_cook_start("What can I cook tonight?")
    assert not is_cook_start("Wat kunnen we koken")
    assert not is_cook_start("recept met kip")
    assert not is_cook_start("Cook the pastasalade and add it to the shopping list")
    assert not is_cook_start("Edit the pastasalade")
    assert not is_cook_start("Change the steps")
    assert not is_cook_start("Save this recipe: https://example.com")
    assert not is_cook_start("next step")
    assert not is_cook_start("")


def test_cook_nav_phrases() -> None:
    assert is_cook_next("Next step")
    assert is_cook_next("next")
    assert is_cook_next("Volgende stap")
    assert is_cook_next("volgende")
    assert is_cook_prev("Previous step")
    assert is_cook_prev("prev")
    assert is_cook_prev("Vorige stap")
    assert is_cook_prev("vorige")
    assert is_cook_prev("what was the last step?")
    assert is_cook_prev("What was the previous step")
    assert is_cook_prev("wat was de vorige stap")
    assert is_cook_prev("Wat was de laatste stap?")
    assert is_cook_repeat_step("Repeat the step")
    assert is_cook_repeat_step("current step")
    assert is_cook_repeat_step("Herhaal deze stap")
    assert is_cook_repeat_step("huidige stap")
    assert is_cook_stop("Stop cooking")
    assert is_cook_stop("klaar met koken")
    assert is_cook_stop("stop")
    # T-054 phrases must not be cook-repeat
    assert not is_cook_repeat_step("Repeat that")
    assert not is_cook_repeat_step("Nog een keer")
    assert is_repeat_intent("Nog een keer")
    assert is_repeat_intent("Repeat that")


def test_resolve_exact_and_ambiguous() -> None:
    hits = [
        {"id": "r1", "name": "Pastasalade"},
        {"id": "r2", "name": "Pastasalade kip"},
    ]
    got = resolve_recipe_from_hits(hits, "pastasalade")
    assert not isinstance(got, str)
    assert got.recipe_id == "r1"

    assert resolve_recipe_from_hits([], "pastasalade") == "not_found"
    assert (
        resolve_recipe_from_hits(
            [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
            "pasta",
        )
        == "ambiguous"
    )
    sole = resolve_recipe_from_hits(
        [{"id": "x", "name": "Kip pastasalade"}], "pastasalade"
    )
    assert not isinstance(sole, str)
    assert sole.recipe_id == "x"


def test_parse_search_hits_shapes() -> None:
    assert parse_search_hits(json.dumps([{"id": "1", "name": "A"}]))[0]["id"] == "1"
    wrapped = parse_search_hits(
        json.dumps({"recipes": [{"id": "2", "name": "B"}]})
    )
    assert wrapped[0]["id"] == "2"
    assert parse_search_hits("error: down") == []


def test_progress_keying_and_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "mimir.db"
    db = Database(db_path)
    db.ensure_conversation("c1")
    seed_cook_session(
        db,
        "c1",
        recipe_id="r1",
        title="Pastasalade",
        steps=["Boil pasta", "Mix dressing", "Toss"],
        locale="en",
    )
    reply, anomaly = advance_step(db, "c1", delta=1)
    assert anomaly == "cook_through_next"
    assert "step 2/3" in reply
    assert "Mix dressing" in reply

    # Restart: new Database handle on same file recovers active index.
    db2 = Database(db_path)
    active = get_active_progress(db2, "c1")
    assert active is not None
    assert active.recipe_id == "r1"
    assert active.step_index == 1
    reply2, _ = advance_step(db2, "c1", delta=1)
    assert "step 3/3" in reply2
    finished, fin_anom = advance_step(db2, "c1", delta=1)
    assert fin_anom == "cook_through_finished"
    assert "last step" in finished.lower() or "done" in finished.lower()


def test_switch_and_stop(tmp_path: Path) -> None:
    db = Database(tmp_path / "mimir.db")
    db.ensure_conversation("c1")
    seed_cook_session(
        db,
        "c1",
        recipe_id="r1",
        title="A",
        steps=["a1", "a2"],
        locale="en",
    )
    advance_step(db, "c1", delta=1)
    progress, switched = seed_cook_session(
        db,
        "c1",
        recipe_id="r2",
        title="B",
        steps=["b1", "b2", "b3"],
        locale="nl",
    )
    assert switched is True
    assert progress.step_index == 0
    assert get_active_progress(db, "c1").recipe_id == "r2"
    assert db.get_cook_progress("c1", "r1") is None

    stop_reply, anomaly = stop_cook_session(db, "c1", locale="nl")
    assert anomaly == "cook_through_stop"
    assert "gestopt" in stop_reply.lower()
    assert get_active_progress(db, "c1") is None


def test_format_step_switched_nl() -> None:
    text = format_step_reply(
        title="Pastasalade",
        index=0,
        steps=["Kook de pasta"],
        locale="nl",
        switched=True,
    )
    assert "Gewisseld" in text
    assert "stap 1/1" in text


class _ScriptedClient:
    def __init__(self, responses: list[ChatMessage] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls = 0

    def chat(self, messages, tools=None, *, think=False, stream=False) -> ChatResponse:
        self.calls += 1
        if not self._responses:
            raise AssertionError("Ollama should not be called for cook-through")
        return ChatResponse(message=self._responses.pop(0))


def _recipe_tools(
    *,
    search_payload: object,
    get_payload: object | None = None,
) -> dict[str, Tool]:
    calls: list[str] = []

    def search(**kwargs: object) -> str:
        calls.append("search")
        return json.dumps(search_payload)

    def get(**kwargs: object) -> str:
        calls.append("get")
        payload = get_payload
        if payload is None:
            payload = {
                "id": "r1",
                "name": "Pastasalade",
                "steps": ["Boil pasta", "Mix dressing", "Toss salad"],
            }
        return json.dumps(payload)

    return {
        "homebase.recipes.search": Tool(
            name="homebase.recipes.search",
            description="search",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
            execute=search,
        ),
        "homebase.recipes.get": Tool(
            name="homebase.recipes.get",
            description="get",
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
            execute=get,
        ),
        "_calls": calls,  # type: ignore[dict-item]
    }


def test_agent_cook_start_next_prev_stop(tmp_path: Path) -> None:
    db = Database(tmp_path / "cook.db")
    tools = _recipe_tools(
        search_payload=[{"id": "r1", "name": "Pastasalade"}],
    )
    # strip helper key
    registry = {
        k: v for k, v in tools.items() if k.startswith("homebase.")
    }
    client = _ScriptedClient()
    cid = "conv-cook"

    start = run_turn(
        client,
        [ChatMessage(role="user", content="Cook the pastasalade")],
        tools=registry,
        conversation_id=cid,
        db=db,
    )
    assert start.stopped_reason == StoppedReason.FINAL
    assert client.calls == 0
    assert "step 1/3" in start.content
    assert "Boil pasta" in start.content
    assert any(s.anomaly == "cook_through_start" for s in start.steps)

    nxt = run_turn(
        client,
        [
            ChatMessage(role="user", content="Cook the pastasalade"),
            ChatMessage(role="assistant", content=start.content),
            ChatMessage(role="user", content="Next step"),
        ],
        tools=registry,
        conversation_id=cid,
        db=db,
    )
    assert "step 2/3" in nxt.content
    assert "Mix dressing" in nxt.content

    prev = run_turn(
        client,
        [ChatMessage(role="user", content="Previous step")],
        tools=registry,
        conversation_id=cid,
        db=db,
    )
    assert "step 1/3" in prev.content

    stop = run_turn(
        client,
        [ChatMessage(role="user", content="Stop cooking")],
        tools=registry,
        conversation_id=cid,
        db=db,
    )
    assert "stopped" in stop.content.lower()
    assert get_active_progress(db, cid) is None


def test_agent_empty_steps_refuse(tmp_path: Path) -> None:
    db = Database(tmp_path / "cook.db")
    registry = {
        k: v
        for k, v in _recipe_tools(
            search_payload=[{"id": "r1", "name": "Empty"}],
            get_payload={"id": "r1", "name": "Empty", "steps": []},
        ).items()
        if k.startswith("homebase.")
    }
    result = run_turn(
        _ScriptedClient(),
        [ChatMessage(role="user", content="Cook the Empty")],
        tools=registry,
        conversation_id="c-empty",
        db=db,
    )
    assert "no cook steps" in result.content.lower()
    assert get_active_progress(db, "c-empty") is None


def test_agent_show_recipe_does_not_short_circuit(tmp_path: Path) -> None:
    """Full-list phrases must reach Ollama (not cook-through)."""
    db = Database(tmp_path / "cook.db")
    client = _ScriptedClient(
        [ChatMessage(role="assistant", content="Here is the full recipe…")]
    )
    result = run_turn(
        client,
        [ChatMessage(role="user", content="Show me the pastasalade")],
        tools={},
        conversation_id="c-show",
        db=db,
    )
    assert client.calls == 1
    assert "full recipe" in result.content.lower()
    assert not any(
        (s.anomaly or "").startswith("cook_through") for s in result.steps
    )


def test_nav_rejects_compound_and_no_active(tmp_path: Path) -> None:
    assert not is_cook_next("next step and add milk")
    assert not is_cook_stop("stop cooking and add to shopping")
    db = Database(tmp_path / "cook.db")
    result = run_turn(
        _ScriptedClient(
            [ChatMessage(role="assistant", content="should not run")]
        ),
        [ChatMessage(role="user", content="Next step")],
        tools={},
        conversation_id="c-none",
        db=db,
    )
    assert "no cook-through" in result.content.lower()
    # Scripted client unused — short-circuit
    assert result.steps[0].anomaly == "cook_through_no_active"


def test_cook_by_recipe_id(tmp_path: Path) -> None:
    db = Database(tmp_path / "cook.db")
    rid = "clxyz123recipeid"
    registry = {
        k: v
        for k, v in _recipe_tools(
            search_payload=[],
            get_payload={
                "id": rid,
                "name": "By Id",
                "steps": ["One", "Two"],
            },
        ).items()
        if k.startswith("homebase.")
    }
    # Empty search → direct get by id
    result = run_turn(
        _ScriptedClient(),
        [ChatMessage(role="user", content=f"Cook the {rid}")],
        tools=registry,
        conversation_id="c-id",
        db=db,
    )
    assert "step 1/2" in result.content
    assert "One" in result.content
    assert get_active_progress(db, "c-id").recipe_id == rid


def test_after_tool_arity_on_cook_start(tmp_path: Path) -> None:
    db = Database(tmp_path / "cook.db")
    registry = {
        k: v
        for k, v in _recipe_tools(
            search_payload=[{"id": "r1", "name": "Pastasalade"}],
        ).items()
        if k.startswith("homebase.")
    }
    seen: list[tuple[str, str]] = []

    def after_tool(name: str, result: str, working: list) -> None:
        seen.append((name, result[:20]))

    result = run_turn(
        _ScriptedClient(),
        [ChatMessage(role="user", content="Cook the pastasalade")],
        tools=registry,
        conversation_id="c-at",
        db=db,
        after_tool=after_tool,
    )
    assert "step 1/3" in result.content
    assert [n for n, _ in seen] == [
        "homebase.recipes.search",
        "homebase.recipes.get",
    ]


def test_pending_m3_bare_stop_does_not_clear_cook(tmp_path: Path) -> None:
    db = Database(tmp_path / "cook.db")
    db.ensure_conversation("c1")
    seed_cook_session(
        db,
        "c1",
        recipe_id="r1",
        title="Pastasalade",
        steps=["One", "Two"],
        locale="en",
    )
    pending = PendingRecipeStore()
    pending.set(
        "c1",
        {"title": "Other", "ingredients": [], "steps": ["x"]},
        dutch=False,
    )
    client = _ScriptedClient(
        [ChatMessage(role="assistant", content="Cancelled path fell through")]
    )
    # Bare stop with pending: recipe cancel clears pending; cook must remain.
    run_turn(
        client,
        [ChatMessage(role="user", content="stop")],
        tools={},
        conversation_id="c1",
        db=db,
        pending_recipes=pending,
    )
    assert get_active_progress(db, "c1") is not None
    assert not pending.is_confirmable("c1")
