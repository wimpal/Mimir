"""TUI composer: multiline paste must survive (recipe import)."""

from __future__ import annotations

import asyncio

from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Input

from clients.tui.composer import ChatComposer


class _InputApp(App[None]):
    def compose(self) -> ComposeResult:
        yield Input(id="i")


class _ComposerApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ChatComposer(id="c")


RECIPE = (
    "Maak deze romige pasta met kipgehakt.\n"
    "Bereidingstijd: 30minuten\n"
    "200 gr pasta\n"
    "300 gr kipgehakt\n"
    "Kook de pasta volgens de instructies op de verpakking.\n"
    "Serveer met wat geraspte Parmezaanse kaas."
)


def test_textual_input_paste_keeps_only_first_line() -> None:
    """Document upstream behaviour — single-line Input is unsafe for recipes."""

    async def _run() -> None:
        async with _InputApp().run_test() as pilot:
            inp = pilot.app.query_one("#i", Input)
            inp.post_message(events.Paste(RECIPE))
            await pilot.pause()
            assert inp.value == "Maak deze romige pasta met kipgehakt."
            assert "Bereidingstijd" not in inp.value

    asyncio.run(_run())


def test_chat_composer_paste_keeps_all_lines() -> None:
    async def _run() -> None:
        async with _ComposerApp().run_test() as pilot:
            composer = pilot.app.query_one("#c", ChatComposer)
            composer.post_message(events.Paste(RECIPE))
            await pilot.pause()
            assert "Bereidingstijd" in composer.text
            assert "200 gr pasta" in composer.text
            assert "Kook de pasta volgens" in composer.text
            assert "Parmezaanse" in composer.text
            assert composer.text.count("\n") >= 5

    asyncio.run(_run())


def test_chat_composer_enter_submits_without_adding_newline() -> None:
    submitted: list[str] = []

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield ChatComposer(id="c")

        def on_chat_composer_submitted(
            self, event: ChatComposer.Submitted
        ) -> None:
            submitted.append(event.text)

    async def _run() -> None:
        async with _App().run_test() as pilot:
            composer = pilot.app.query_one("#c", ChatComposer)
            composer.load_text("hello world")
            await pilot.press("enter")
            await pilot.pause()
            assert submitted == ["hello world"]
            assert "\n" not in submitted[0]

    asyncio.run(_run())


def test_chat_composer_shift_enter_inserts_newline() -> None:
    async def _run() -> None:
        async with _ComposerApp().run_test() as pilot:
            composer = pilot.app.query_one("#c", ChatComposer)
            composer.load_text("line one")
            await pilot.press("shift+enter")
            await pilot.pause()
            assert "\n" in composer.text
            assert "line one" in composer.text

    asyncio.run(_run())
