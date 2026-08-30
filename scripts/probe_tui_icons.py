#!/usr/bin/env python3
"""Print TUI mic glyphs so you can verify the terminal font in one glance.

Usage:
  uv run python scripts/probe_tui_icons.py

If nerd idle/rec show as □ or ?, install a Nerd Font Mono and set it in
Windows Terminal (see clients/tui/README.md). Use MIMIR_TUI_ICON_MODE=text
to force ASCII fallback.
"""

from __future__ import annotations

import sys

from clients.tui.icons import (
    LABEL_IDLE,
    LABEL_RECORDING,
    NF_MIC,
    NF_RECORD,
    icon_mode,
)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    mode = icon_mode()
    print(f"MIMIR_TUI_ICON_MODE={mode.value}")
    print(f"nerd idle: |{NF_MIC}|  U+{ord(NF_MIC):04X}")
    print(f"nerd rec:  |{NF_RECORD}|  U+{ord(NF_RECORD):04X}")
    print(f"text idle: |{LABEL_IDLE}|")
    print(f"text rec:  |{LABEL_RECORDING}|")
    print()
    print("Expect a microphone and record circle on line 1–2 when the profile")
    print("font is a Nerd Font Mono (e.g. JetBrainsMono NFM).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
