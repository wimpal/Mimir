"""TUI icon glyphs (Nerd Fonts 3.x) and ASCII fallbacks.

Requires a Nerd Font Mono in the terminal profile (see clients/tui/README.md).
Verified against Nerd Fonts 3.5.1 glyphnames: ``md-microphone``, ``md-record``.
"""

from __future__ import annotations

import os
from enum import StrEnum

# Nerd Fonts Material Design Icons (private use) — one terminal cell each.
NF_MIC = "\U000f036c"  # nf-md-microphone
NF_RECORD = "\U000f044a"  # nf-md-record

LABEL_IDLE = "mic"
LABEL_RECORDING = "rec"

_ENV_ICON_MODE = "MIMIR_TUI_ICON_MODE"


class IconMode(StrEnum):
    NERD = "nerd"
    TEXT = "text"


def icon_mode() -> IconMode:
    raw = os.environ.get(_ENV_ICON_MODE, "nerd").strip().lower()
    if raw == "text":
        return IconMode.TEXT
    return IconMode.NERD


def mic_display(*, recording: bool, mode: IconMode | None = None) -> str:
    mode = mode or icon_mode()
    if mode is IconMode.TEXT:
        return LABEL_RECORDING if recording else LABEL_IDLE
    return NF_RECORD if recording else NF_MIC


def mic_widget_width(mode: IconMode | None = None) -> int:
    """Terminal columns for MicButton (1 glyph vs ``mic``/``rec``)."""
    mode = mode or icon_mode()
    return 1 if mode is IconMode.NERD else 5
