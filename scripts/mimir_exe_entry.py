"""Console entry for the frozen Mimir.exe (PyInstaller).

Keeps the window open on startup failures so double-click errors are readable.
"""

from __future__ import annotations

import sys
import traceback


def main() -> int:
    try:
        from clients.tui.window_title import set_console_title

        set_console_title("Mimir")
        from clients.tui.app import main as app_main

        app_main()
        return 0
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 1 if code else 0
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        try:
            input("\nPress Enter to close…")
        except EOFError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
