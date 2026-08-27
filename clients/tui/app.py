"""Textual full-screen Chat client for Mimir — blend chrome + world tree."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static

from clients.tui.brain_client import (
    DEFAULT_BRAIN_URL,
    DEFAULT_TURN_TIMEOUT_S,
    BrainClient,
    BrainClientError,
    normalize_brain_url,
)
from clients.tui.brain_launcher import ensure_brain_running
from clients.tui.history import HistoryScreen
from clients.tui.settings import SettingsScreen
from clients.tui.splash import Splash
from clients.tui.state import ChatState, save_state
from clients.tui.window_title import set_console_title

HELP_TEXT = """Commands:
  /new      — start a new Conversation
  /history  — browse and resume a past Conversation
  /settings — view and edit Preferences
  /copy     — copy the last reply to the clipboard
  /quit     — exit
  /help     — show this help
  Esc       — interrupt the current turn while working
  Ctrl+Shift+C — copy the last reply
  Drag to select, then Ctrl+C — copy selection (does not quit)

Chat normally by typing a message and pressing Enter.
If the brain is down, Mimir tries to start it (needs `uv` + repo)."""


def host_clipboard_copy(text: str) -> bool:
    """Best-effort OS clipboard write (complements Textual's OSC 52)."""
    if not text:
        return False
    data_utf8 = text.encode("utf-8")
    if sys.platform == "win32":
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                ["clip"],
                input=text.encode("utf-16le"),
                check=False,
                creationflags=flags,
            )
            return completed.returncode == 0
        except OSError:
            return False
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["pbcopy"], input=data_utf8, check=False
            )
            return completed.returncode == 0
        except OSError:
            return False
    for cmd in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        try:
            completed = subprocess.run(cmd, input=data_utf8, check=False)
            if completed.returncode == 0:
                return True
        except OSError:
            continue
    return False


def _host_label(url: str) -> str:
    try:
        parsed = urlparse(normalize_brain_url(url))
        host = parsed.hostname or url
        if parsed.port:
            return f"{host}:{parsed.port}"
        return host
    except ValueError:
        return url


class Transcript(VerticalScroll):
    """Scrollable message list."""

    DEFAULT_CSS = """
    Transcript {
        scrollbar-background: #0d0f0c;
        scrollbar-background-hover: #141914;
        scrollbar-background-active: #1a2418;
        scrollbar-color: #3d4a38;
        scrollbar-color-hover: #7cb342;
        scrollbar-color-active: #9ccc65;
        scrollbar-corner-color: #0d0f0c;
    }
    """

    def append_line(self, text: str, *, classes: str = "") -> Static:
        widget = Static(text, classes=classes)
        self.mount(widget)
        self.scroll_end(animate=False)
        return widget

    def append_user(self, text: str) -> Static:
        return self.append_line(f"> {text}", classes="user")

    def append_assistant(self, text: str = "") -> Static:
        return self.append_line(text, classes="assistant")

    def append_tool_line(self, text: str) -> Static:
        return self.append_line(text, classes="tool-line")

    def append_system(self, text: str) -> Static:
        return self.append_line(text, classes="system")

    def append_error(self, text: str) -> Static:
        return self.append_line(text, classes="error")


class MimirApp(App[None]):
    """Claude/Amp blend Chat client with a green world-tree splash."""

    CSS = """
    Screen {
        layout: vertical;
        background: #0d0f0c;
        scrollbar-background: #0d0f0c;
        scrollbar-background-hover: #141914;
        scrollbar-background-active: #1a2418;
        scrollbar-color: #3d4a38;
        scrollbar-color-hover: #7cb342;
        scrollbar-color-active: #9ccc65;
        scrollbar-corner-color: #0d0f0c;
    }

    #topbar {
        dock: top;
        height: 1;
        width: 100%;
        padding: 0 1;
        background: #0d0f0c;
        layout: horizontal;
    }
    #meta {
        width: 1fr;
        color: #6b7280;
        text-align: right;
        content-align: right middle;
    }

    #body {
        height: 1fr;
    }

    #splash {
        display: block;
    }
    #splash.hidden {
        display: none;
    }

    #transcript {
        height: 1fr;
        padding: 0 1 1 1;
        scrollbar-background: #0d0f0c;
        scrollbar-background-hover: #141914;
        scrollbar-background-active: #1a2418;
        scrollbar-color: #3d4a38;
        scrollbar-color-hover: #7cb342;
        scrollbar-color-active: #9ccc65;
        scrollbar-corner-color: #0d0f0c;
    }
    #transcript .user {
        background: #1a2418;
        color: #c5e1a5;
        padding: 0 1;
        margin-top: 1;
        border-left: thick #7cb342;
    }
    #transcript .assistant {
        color: #e8eaed;
        margin-top: 1;
        padding: 0 1;
    }
    #transcript .tool-line {
        color: #5c6370;
        text-style: dim;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin: 0;
        padding: 0 1;
        border: none;
        background: transparent;
    }
    #transcript .error {
        color: #e57373;
        margin-top: 1;
        padding: 0 1;
        border-left: thick #e57373;
    }
    #transcript .system {
        color: #6b7280;
        text-style: dim;
        margin-top: 1;
        padding: 0 1;
    }

    #work-status {
        height: 1;
        padding: 0 1;
        color: #7cb342;
        background: #0d0f0c;
    }
    #work-status.hidden {
        display: none;
    }

    #input-wrap {
        height: auto;
        margin: 0 1 0 1;
        padding: 0 1;
        border: round #3d4a38;
        background: #121612;
    }
    #input-wrap:focus-within {
        border: round #7cb342;
    }
    #input {
        background: transparent;
        border: none;
        padding: 0;
    }
    #input:focus {
        background: transparent;
    }

    #hints {
        height: 1;
        padding: 0 1;
        color: #6b7280;
        background: #0d0f0c;
        border-top: solid #2a3328;
    }
    """

    BINDINGS = [
        # Do not bind ctrl+c to quit — Textual uses it to copy a selection.
        Binding("ctrl+d", "quit", "Quit", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding(
            "ctrl+shift+c",
            "copy_last_reply",
            "Copy last reply",
            show=False,
        ),
        Binding("escape", "interrupt", "Interrupt", show=False),
    ]

    def __init__(
        self,
        *,
        brain_url: str = DEFAULT_BRAIN_URL,
        turn_timeout_s: float = DEFAULT_TURN_TIMEOUT_S,
        state_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.brain_url = brain_url
        self.turn_timeout_s = turn_timeout_s
        self.state_path = state_path
        self._client: BrainClient | None = None
        self._conversation_id: str | None = None
        self._busy = False
        self._busy_kind: str | None = None
        self._restore_gen = 0
        self._history_gen = 0
        self._settings_gen = 0
        self._stream_task: asyncio.Task[None] | None = None
        self._assistant_widget: Static | None = None
        self._assistant_text = ""
        self._last_assistant_text = ""
        self._showing_splash = True

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("", id="meta")
        with Vertical(id="body"):
            yield Splash(id="splash")
            yield Transcript(id="transcript")
        yield Static("", id="work-status", classes="hidden")
        with Vertical(id="input-wrap"):
            yield Input(placeholder="Message Mimir…", id="input")
        yield Static(
            "/new  /history  /settings  /copy  /help  /quit  ·  "
            "Ctrl+Shift+C copy  ·  Esc interrupt",
            id="hints",
        )

    def copy_to_clipboard(self, text: str) -> None:
        """OSC 52 via Textual, plus host clipboard on Windows/macOS/Linux."""
        super().copy_to_clipboard(text)
        host_clipboard_copy(text)

    def action_copy_last_reply(self) -> None:
        text = (self._last_assistant_text or "").strip()
        if not text:
            self.notify("Nothing to copy yet", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify("Copied last reply")

    def on_mount(self) -> None:
        set_console_title("Mimir")
        self.title = "Mimir"
        self._client = BrainClient(
            self.brain_url, turn_timeout_s=self.turn_timeout_s
        )
        # Always start a fresh Conversation; resume is explicit via /history.
        self._conversation_id = None
        self._persist()
        self._show_splash(True)
        self._set_busy(True, note="Connecting…", kind="startup")
        self._focus_input()
        self._startup()

    async def on_unmount(self) -> None:
        await self._cancel_stream()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _transcript(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    def _splash(self) -> Splash:
        return self.query_one("#splash", Splash)

    def _show_splash(self, show: bool) -> None:
        self._showing_splash = show
        splash = self._splash()
        splash.set_class(not show, "hidden")

    def _hide_splash_for_chat(self) -> None:
        if self._showing_splash:
            self._show_splash(False)

    def _refresh_meta(self, note: str | None = None) -> None:
        host = _host_label(self.brain_url)
        cid = (self._conversation_id or "new")[:8]
        parts = [host, f"convo {cid}"]
        if note:
            parts.append(note)
        self.query_one("#meta", Static).update(" · ".join(parts))

    def _set_work_status(self, text: str | None) -> None:
        status = self.query_one("#work-status", Static)
        if text:
            status.update(text)
            status.remove_class("hidden")
        else:
            status.update("")
            status.add_class("hidden")

    def _persist(self) -> None:
        save_state(
            ChatState(conversation_id=self._conversation_id),
            self.state_path,
        )

    def _focus_input(self) -> None:
        self.query_one("#input", Input).focus()

    def _set_busy(
        self,
        busy: bool,
        *,
        note: str | None = None,
        kind: str | None = None,
    ) -> None:
        self._busy = busy
        self._busy_kind = kind if busy else None
        inp = self.query_one("#input", Input)
        # Keep the field usable during connect so typing works on open.
        inp.disabled = busy and kind != "startup"
        if busy:
            if kind == "startup":
                self._set_work_status(note or "Connecting…")
                self._refresh_meta("connecting…")
            elif kind == "history":
                self._set_work_status(note or "Loading history…")
                self._refresh_meta("working")
            elif kind == "settings":
                self._set_work_status(note or "Loading settings…")
                self._refresh_meta("working")
            else:
                self._set_work_status(
                    note or "Working… (esc to interrupt)"
                )
                self._refresh_meta("working")
        else:
            self._set_work_status(None)
            self._refresh_meta("ready")

    async def _cancel_stream(self) -> None:
        task = self._stream_task
        self._stream_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def action_interrupt(self) -> None:
        if not self._busy:
            return
        kind = self._busy_kind
        if kind == "startup":
            return
        if kind == "history":
            self._history_gen += 1
            self._restore_gen += 1
            self._set_busy(False)
            self._focus_input()
            return
        if kind == "settings":
            self._settings_gen += 1
            self._set_busy(False)
            self._focus_input()
            return
        self._history_gen += 1
        self._settings_gen += 1
        self._restore_gen += 1
        await self._cancel_stream()
        self._hide_splash_for_chat()
        self._transcript().append_system("Interrupted.")
        self._set_busy(False)
        self._focus_input()

    @work(exclusive=True)
    async def _startup(self) -> None:
        assert self._client is not None
        try:
            health = await self._client.health()
        except BrainClientError:
            self._refresh_meta("starting brain…")
            self._set_work_status("Starting brain…")
            result = await asyncio.to_thread(
                ensure_brain_running, self.brain_url
            )
            if not result.already_running and not result.started:
                self._hide_splash_for_chat()
                self._transcript().append_error(result.message)
                self._refresh_meta("offline")
                self._set_busy(False)
                self._focus_input()
                return
            try:
                health = await self._client.health()
            except BrainClientError as exc:
                self._hide_splash_for_chat()
                self._transcript().append_error(
                    f"Brain still unreachable: {exc}"
                )
                self._refresh_meta("offline")
                self._set_busy(False)
                self._focus_input()
                return

        if health.status != "ok":
            detail = health.detail or health.status
            # Keep splash; note warning in meta only unless severe.
            self._refresh_meta(f"degraded ({detail})")
        else:
            self._refresh_meta("ready")

        # Don't clear busy if /history or /settings already owns the UI.
        if (
            self._history_gen == 0
            and self._settings_gen == 0
            and self._busy_kind == "startup"
        ):
            self._set_busy(False)
            self._focus_input()

    async def _restore_history(self) -> None:
        assert self._client is not None
        cid = self._conversation_id
        if not cid:
            return
        gen = self._restore_gen + 1
        self._restore_gen = gen
        try:
            messages = await self._client.list_messages(cid)
        except BrainClientError as exc:
            if gen != self._restore_gen:
                return
            self._hide_splash_for_chat()
            self._transcript().append_error(
                f"Could not restore history: {exc}"
            )
            return
        if gen != self._restore_gen:
            return
        if not messages:
            self._show_splash(True)
            return
        self._hide_splash_for_chat()
        last_assistant = ""
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user":
                self._transcript().append_user(content)
            elif role == "assistant":
                self._transcript().append_assistant(content)
                if content.strip():
                    last_assistant = content
        self._last_assistant_text = last_assistant

    def _new_conversation(self) -> None:
        self._history_gen += 1
        self._settings_gen += 1
        self._restore_gen += 1
        self._conversation_id = None
        self._last_assistant_text = ""
        self._persist()
        self._transcript().remove_children()
        self._show_splash(True)
        self._set_busy(False)
        self._refresh_meta("ready")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        # Ignore nested Inputs (e.g. /settings modal owns its own field).
        if event.input.id != "input":
            return
        text = (event.value or "").strip()
        if not text:
            return
        # Don't wipe typed text if Enter lands during connect / another busy state.
        if self._busy:
            return
        event.input.value = ""

        if text.startswith("/"):
            await self._handle_command(text)
            return

        await self._send_message(text)

    async def _handle_command(self, text: str) -> None:
        cmd = text.split(maxsplit=1)[0].lower()
        if cmd in ("/quit", "/exit", "/q"):
            self.exit()
            return
        if cmd == "/new":
            await self._cancel_stream()
            self._new_conversation()
            return
        if cmd == "/history":
            self._open_history()
            return
        if cmd == "/settings":
            self._open_settings()
            return
        if cmd == "/copy":
            self.action_copy_last_reply()
            return
        if cmd == "/help":
            self._hide_splash_for_chat()
            self._transcript().append_system(HELP_TEXT)
            return
        self._hide_splash_for_chat()
        self._transcript().append_error(
            f"Unknown command: {cmd}. Try /help."
        )

    @work
    async def _open_history(self) -> None:
        assert self._client is not None
        self._history_gen += 1
        op = self._history_gen
        self._restore_gen += 1
        self._set_busy(True, note="Loading history…", kind="history")
        try:
            try:
                rows = await self._client.list_conversations()
            except BrainClientError as exc:
                if op != self._history_gen:
                    return
                self._hide_splash_for_chat()
                self._transcript().append_error(
                    f"Could not load history: {exc}"
                )
                return
            if op != self._history_gen:
                return
            if not rows:
                self._hide_splash_for_chat()
                self._transcript().append_system("No past Conversations.")
                return
            self._set_work_status(None)
            selected = await self.push_screen_wait(HistoryScreen(rows))
            if op != self._history_gen:
                return
            if not selected:
                return
            self._set_work_status("Restoring…")
            await self._resume_conversation(selected, history_gen=op)
        finally:
            if op == self._history_gen:
                self._set_busy(False)
                self._focus_input()

    @work
    async def _open_settings(self) -> None:
        assert self._client is not None
        self._settings_gen += 1
        op = self._settings_gen
        self._set_busy(True, note="Loading settings…", kind="settings")
        try:
            try:
                rows = await self._client.get_preferences()
            except BrainClientError as exc:
                if op != self._settings_gen:
                    return
                self._hide_splash_for_chat()
                self._transcript().append_error(
                    f"Could not load preferences: {exc}"
                )
                return
            if op != self._settings_gen:
                return
            if not rows:
                self._hide_splash_for_chat()
                self._transcript().append_system("No preferences available.")
                return
            self._set_work_status(None)
            edited = await self.push_screen_wait(SettingsScreen(rows))
            if op != self._settings_gen:
                return
            if not edited:
                return
            self._set_work_status("Saving…")
            try:
                saved = await self._client.put_preference(
                    edited.key, edited.value
                )
            except BrainClientError as exc:
                if op != self._settings_gen:
                    return
                self._hide_splash_for_chat()
                self._transcript().append_error(
                    f"Could not save preference: {exc}"
                )
                return
            if op != self._settings_gen:
                return
            self._hide_splash_for_chat()
            stored = saved.get("value", edited.value)
            self._transcript().append_system(
                f"Preference saved: {edited.key} = {stored}"
            )
        finally:
            if op == self._settings_gen:
                self._set_busy(False)
                self._focus_input()

    async def _resume_conversation(
        self, conversation_id: str, *, history_gen: int
    ) -> None:
        if history_gen != self._history_gen:
            return
        self._conversation_id = conversation_id.strip()
        self._persist()
        self._transcript().remove_children()
        self._refresh_meta("restoring…")
        self._set_work_status("Restoring…")
        await self._restore_history()
        if history_gen != self._history_gen:
            return
        self._refresh_meta("ready")

    async def _send_message(self, text: str) -> None:
        assert self._client is not None
        self._restore_gen += 1
        await self._cancel_stream()

        self._hide_splash_for_chat()
        tr = self._transcript()
        tr.append_user(text)
        self._assistant_text = ""
        self._assistant_widget = tr.append_assistant("")
        self._set_busy(
            True, note="Working… (esc to interrupt)", kind="stream"
        )

        async def run_stream() -> None:
            assert self._client is not None
            assistant = self._assistant_widget
            try:
                async for event in self._client.stream_chat(
                    text, conversation_id=self._conversation_id
                ):
                    etype = event.get("type")
                    if etype == "meta":
                        cid = event.get("conversation_id")
                        if isinstance(cid, str) and cid.strip():
                            self._conversation_id = cid.strip()
                            self._persist()
                            self._refresh_meta("working")
                    elif etype == "tool_start":
                        name = event.get("name") or "tool"
                        self._set_work_status(
                            f"{name}… (esc to interrupt)"
                        )
                    elif etype == "tool_end":
                        pass
                    elif etype == "token":
                        chunk = event.get("text")
                        if isinstance(chunk, str) and assistant is not None:
                            self._assistant_text += chunk
                            assistant.update(self._assistant_text)
                            tr.scroll_end(animate=False)
                    elif etype == "error":
                        msg = event.get("message") or "Something went wrong."
                        tr.append_error(str(msg))
                        cid = event.get("conversation_id")
                        if isinstance(cid, str) and cid.strip():
                            self._conversation_id = cid.strip()
                            self._persist()
                    elif etype == "done":
                        cid = event.get("conversation_id")
                        if isinstance(cid, str) and cid.strip():
                            self._conversation_id = cid.strip()
                            self._persist()
                        tools = event.get("tools_used") or []
                        if isinstance(tools, list) and tools:
                            # One dim line under the reply (dedupe, keep order).
                            seen: list[str] = []
                            for name in tools:
                                label = str(name).strip()
                                if label and label not in seen:
                                    seen.append(label)
                            if seen:
                                tr.append_tool_line(" · ".join(seen))
            except asyncio.CancelledError:
                raise
            except BrainClientError as exc:
                tr.append_error(str(exc))
            finally:
                if self._assistant_text.strip():
                    self._last_assistant_text = self._assistant_text
                self._stream_task = None
                self._set_busy(False)
                self._focus_input()

        self._stream_task = asyncio.create_task(run_stream())
        try:
            await self._stream_task
        except asyncio.CancelledError:
            self._set_busy(False)
            self._focus_input()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mimir",
        description="Mimir terminal Chat client (Talks to a running brain).",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("MIMIR_BRAIN_URL", DEFAULT_BRAIN_URL),
        help=f"Brain base URL (default: {DEFAULT_BRAIN_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(
            os.environ.get("MIMIR_TURN_TIMEOUT_S", DEFAULT_TURN_TIMEOUT_S)
        ),
        help="SSE read timeout in seconds (default: 180)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Path to chat_state.json (default: ~/.mimir/chat_state.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    set_console_title("Mimir")
    args = build_parser().parse_args(argv)
    app = MimirApp(
        brain_url=args.url,
        turn_timeout_s=args.timeout,
        state_path=args.state,
    )
    app.run()


if __name__ == "__main__":
    main()
