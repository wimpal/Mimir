"""Modal Conversation picker for `/history` resume."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option


def format_history_option(row: dict[str, Any]) -> str:
    """One-line label for a Conversation list row."""
    preview = str(row.get("preview") or "").strip() or "(no preview)"
    updated = str(row.get("updated_at") or "").strip()
    cid = str(row.get("id") or "").strip()
    short = cid[:8] if cid else "?"
    parts = [preview]
    if updated:
        parts.append(updated)
    parts.append(short)
    return "  ·  ".join(parts)


class HistoryScreen(ModalScreen[str | None]):
    """Pick a past Conversation; Esc dismisses with ``None``."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    HistoryScreen {
        align: center middle;
    }

    HistoryScreen > #history-panel {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        border: solid #4a7c59;
        background: #121610;
        padding: 1 2;
    }

    HistoryScreen #history-title {
        color: #c5d4b8;
        text-style: bold;
        margin-bottom: 1;
    }

    HistoryScreen #history-hint {
        color: #6b7280;
        margin-top: 1;
    }

    HistoryScreen OptionList {
        height: auto;
        max-height: 20;
        border: none;
        background: transparent;
    }

    HistoryScreen OptionList:focus {
        border: none;
    }
    """

    def __init__(self, conversations: list[dict[str, Any]]) -> None:
        super().__init__()
        self._conversations = conversations

    def compose(self) -> ComposeResult:
        options: list[Option] = []
        for row in self._conversations:
            cid = str(row.get("id") or "").strip()
            if not cid:
                continue
            options.append(Option(format_history_option(row), id=cid))
        with Vertical(id="history-panel"):
            yield Label("Past Conversations", id="history-title")
            yield OptionList(*options, id="history-list")
            yield Static("↑↓ select  ·  Enter resume  ·  Esc cancel", id="history-hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#history-list", OptionList)
        option_list.focus()
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        cid = event.option_id
        if cid:
            self.dismiss(cid)

    def action_cancel(self) -> None:
        self.dismiss(None)
