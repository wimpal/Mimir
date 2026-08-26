"""Tests for console title helper (no caption overlay; that is frozen-exe only)."""

from clients.tui.window_title import set_console_title


def test_set_console_title_does_not_raise() -> None:
    set_console_title("Mimir")
    set_console_title("")
