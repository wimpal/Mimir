"""Multiline chat composer — Textual Input drops all but the first pasted line."""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


class ChatComposer(TextArea):
    """Chat input that keeps full multiline pastes (recipes, logs, etc.).

    Enter sends; Shift+Enter (or Ctrl+J) inserts a newline.
    """

    class Submitted(Message):
        """User pressed Enter to send the current draft."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    # Keep TextArea shortcuts (paste/copy/…) and document send/newline keys.
    BINDINGS = [
        Binding("enter", "submit", "Send", show=False),
        Binding("shift+enter", "newline", "Newline", show=False),
        Binding("ctrl+j", "newline", "Newline", show=False),
        *TextArea.BINDINGS,
    ]

    def __init__(self, *, placeholder: str = "", id: str | None = None) -> None:
        super().__init__(
            "",
            id=id,
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            highlight_cursor_line=False,
            compact=True,
            placeholder=placeholder,
        )

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self.text))

    def action_newline(self) -> None:
        self.insert("\n")

    def clear_draft(self) -> None:
        self.load_text("")

    async def _on_key(self, event: events.Key) -> None:
        # TextArea._on_key maps Enter → "\n" and stops the event before bindings
        # run, so send/newline must be handled here.
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.action_submit()
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.action_newline()
            return
        await super()._on_key(event)
