"""Modal Preferences editor for `/settings`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option


@dataclass(frozen=True)
class SettingsEdit:
    """User chose a new value for one Preference key."""

    key: str
    value: str


def format_preference_display(key: str, value: str | None) -> str:
    """Human label for a Preference row (stored form → display)."""
    if value is None or not str(value).strip():
        shown = "(unset)"
    elif key == "favorite_genres":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            shown = str(value).strip()
        else:
            if isinstance(parsed, list) and parsed:
                shown = ", ".join(str(g) for g in parsed)
            else:
                shown = "(unset)"
    else:
        shown = str(value).strip() or "(unset)"
    return f"{key}  ·  {shown}"


def edit_seed_value(key: str, value: str | None) -> str:
    """Prefill Input with a human-editable form of the stored value."""
    if value is None or not str(value).strip():
        return ""
    if key == "favorite_genres":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return str(value).strip()
        if isinstance(parsed, list):
            return ", ".join(str(g) for g in parsed)
    return str(value).strip()


class SettingsScreen(ModalScreen[SettingsEdit | None]):
    """Browse allowlisted Preferences; Enter edits one; Esc dismisses."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
    }

    SettingsScreen > #settings-panel {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        border: solid #4a7c59;
        background: #121610;
        padding: 1 2;
    }

    SettingsScreen #settings-title {
        color: #c5d4b8;
        text-style: bold;
        margin-bottom: 1;
    }

    SettingsScreen #settings-hint {
        color: #6b7280;
        margin-top: 1;
    }

    SettingsScreen OptionList {
        height: auto;
        max-height: 12;
        border: none;
        background: transparent;
    }

    SettingsScreen OptionList:focus {
        border: none;
    }

    SettingsScreen #settings-edit {
        margin-top: 1;
        border: solid #4a7c59;
        background: #0d0f0c;
    }

    SettingsScreen #settings-edit.hidden {
        display: none;
    }
    """

    def __init__(self, preferences: list[dict[str, Any]]) -> None:
        super().__init__()
        self._preferences = preferences
        self._editing_key: str | None = None
        self._values: dict[str, str | None] = {}
        for row in preferences:
            key = str(row.get("key") or "").strip()
            if not key:
                continue
            raw = row.get("value")
            self._values[key] = None if raw is None else str(raw)

    def compose(self) -> ComposeResult:
        options: list[Option] = []
        for key, value in self._values.items():
            options.append(
                Option(format_preference_display(key, value), id=key)
            )
        with Vertical(id="settings-panel"):
            yield Label("Preferences", id="settings-title")
            yield OptionList(*options, id="settings-list")
            yield Input(
                placeholder="New value…",
                id="settings-edit",
                classes="hidden",
            )
            yield Static(
                "↑↓ select  ·  Enter edit  ·  Esc cancel",
                id="settings-hint",
            )

    def on_mount(self) -> None:
        option_list = self.query_one("#settings-list", OptionList)
        option_list.focus()
        if option_list.option_count > 0:
            option_list.highlighted = 0

    def _hint(self, text: str) -> None:
        self.query_one("#settings-hint", Static).update(text)

    def _begin_edit(self, key: str) -> None:
        self._editing_key = key
        edit = self.query_one("#settings-edit", Input)
        edit.remove_class("hidden")
        edit.value = edit_seed_value(key, self._values.get(key))
        edit.placeholder = f"New value for {key}…"
        self._hint("Enter save  ·  Esc back")
        edit.focus()

    def _end_edit(self) -> None:
        self._editing_key = None
        edit = self.query_one("#settings-edit", Input)
        edit.value = ""
        edit.add_class("hidden")
        self._hint("↑↓ select  ·  Enter edit  ·  Esc cancel")
        self.query_one("#settings-list", OptionList).focus()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if self._editing_key is not None:
            return
        key = event.option_id
        if key:
            self._begin_edit(str(key))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id != "settings-edit":
            return
        key = self._editing_key
        if not key:
            return
        text = (event.value or "").strip()
        if not text:
            return
        self.dismiss(SettingsEdit(key=key, value=text))

    def action_cancel(self) -> None:
        if self._editing_key is not None:
            self._end_edit()
            return
        self.dismiss(None)
