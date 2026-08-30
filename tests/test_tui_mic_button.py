"""Tests for TUI microphone control and Nerd Font icon mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from clients.tui.icons import (
    LABEL_IDLE,
    LABEL_RECORDING,
    NF_MIC,
    NF_RECORD,
    IconMode,
    icon_mode,
    mic_display,
    mic_widget_width,
)
from clients.tui.mic_button import MicButton

_ASSETS = Path(__file__).resolve().parents[1] / "clients" / "tui" / "assets"


def test_mic_svg_is_lucide_outline() -> None:
    svg = (_ASSETS / "mic.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 24 24"' in svg
    assert "lucide" in svg.lower()


def test_nerd_font_mic_codepoints() -> None:
    """Pinned to Nerd Fonts 3.5.1 md-microphone / md-record."""
    assert ord(NF_MIC) == 0xF036C
    assert ord(NF_RECORD) == 0xF044A
    assert len(NF_MIC) == 1
    assert len(NF_RECORD) == 1


def test_mic_display_nerd_mode() -> None:
    assert mic_display(recording=False, mode=IconMode.NERD) == NF_MIC
    assert mic_display(recording=True, mode=IconMode.NERD) == NF_RECORD


def test_mic_display_text_mode() -> None:
    assert mic_display(recording=False, mode=IconMode.TEXT) == LABEL_IDLE
    assert mic_display(recording=True, mode=IconMode.TEXT) == LABEL_RECORDING
    assert LABEL_IDLE.isascii() and LABEL_RECORDING.isascii()


def test_mic_widget_width_by_mode() -> None:
    assert mic_widget_width(mode=IconMode.NERD) == 1
    assert mic_widget_width(mode=IconMode.TEXT) == 5


def test_icon_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIMIR_TUI_ICON_MODE", raising=False)
    assert icon_mode() is IconMode.NERD
    monkeypatch.setenv("MIMIR_TUI_ICON_MODE", "text")
    assert icon_mode() is IconMode.TEXT
    monkeypatch.setenv("MIMIR_TUI_ICON_MODE", "nerd")
    assert icon_mode() is IconMode.NERD


def test_mic_button_render_nerd_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIMIR_TUI_ICON_MODE", raising=False)
    btn = MicButton()
    assert btn.render() == NF_MIC
    btn.recording = True
    assert btn.render() == NF_RECORD


def test_mic_button_render_text_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIMIR_TUI_ICON_MODE", "text")
    btn = MicButton()
    assert btn.render() == "mic"
    btn.recording = True
    assert btn.render() == "rec"
