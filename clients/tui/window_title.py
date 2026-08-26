"""Set the console / terminal window title."""

from __future__ import annotations

import sys


def set_console_title(title: str = "Mimir") -> None:
    """Best-effort window title.

    On Windows use SetConsoleTitleW only. Do **not** write an OSC title
    sequence terminated with BEL (``\\007``) — conhost treats BEL as a
    system beep (the startup notification sound).
    """
    text = title or "Mimir"
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(str(text))
        except Exception:  # noqa: BLE001
            pass
        return

    # Other platforms: OSC 0 ; title ST (ESC \\). Avoid BEL — some hosts beep.
    try:
        sys.stdout.write(f"\033]0;{text}\033\\")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass
