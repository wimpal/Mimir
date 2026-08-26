"""Set the Windows console window title to a short app name."""

from __future__ import annotations

import sys


def set_console_title(title: str = "Mimir") -> None:
    """Best-effort: Windows console title, plus OSC for VT-capable terminals."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(str(title))
        except Exception:  # noqa: BLE001
            pass
    # Many terminals (Windows Terminal, mintty) honor OSC 0/2
    try:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
