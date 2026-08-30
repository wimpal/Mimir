"""Clickable microphone control for the TUI.

Default: Nerd Font ``md-microphone`` / ``md-record`` (one cell, light grey).
Fallback: ASCII ``mic`` / ``rec`` when ``MIMIR_TUI_ICON_MODE=text``.
Lucide SVG at assets/mic.svg is for GUI/mobile clients only.
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from clients.tui.icons import IconMode, icon_mode, mic_display


class MicButton(Static):
    """Mic icon button — click toggles voice capture in the parent app."""

    DEFAULT_CSS = """
    MicButton {
        height: 3;
        min-height: 3;
        max-height: 3;
        margin-left: 1;
        padding: 0;
        background: transparent;
        border: none;
        color: #b0b6be;
        text-style: bold;
        content-align: center middle;
        pointer: pointer;
    }
    MicButton.icon-nerd {
        width: 1;
        min-width: 1;
        max-width: 1;
    }
    MicButton.icon-text {
        width: 5;
        min-width: 5;
        max-width: 5;
    }
    MicButton:hover {
        color: #e8eaed;
    }
    MicButton.recording {
        color: #e57373;
    }
    MicButton.disabled {
        color: #4b5563;
        pointer: default;
    }
    """

    recording: reactive[bool] = reactive(False)
    disabled: reactive[bool] = reactive(False)
    _hovered: reactive[bool] = reactive(False)

    class Pressed(Message):
        """Mic button clicked."""

        def __init__(self, widget: MicButton) -> None:
            super().__init__()
            self.widget = widget

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._icon_mode = icon_mode()

    def render(self) -> str:
        return mic_display(recording=self.recording, mode=self._icon_mode)

    def on_mount(self) -> None:
        self.can_focus = not self.disabled
        self.set_class(self._icon_mode is IconMode.NERD, "icon-nerd")
        self.set_class(self._icon_mode is IconMode.TEXT, "icon-text")

    def watch_recording(self, recording: bool) -> None:
        self.set_class(recording, "recording")
        self.refresh()

    def watch_disabled(self, disabled: bool) -> None:
        self.set_class(disabled, "disabled")
        self.can_focus = not disabled
        self.refresh()

    def on_enter(self, _event: events.Enter) -> None:
        if not self.disabled:
            self._hovered = True
            self.refresh()

    def on_leave(self, _event: events.Leave) -> None:
        self._hovered = False
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        if self.disabled:
            return
        event.stop()
        self.post_message(MicButton.Pressed(self))
