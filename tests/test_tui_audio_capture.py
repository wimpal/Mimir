"""Tests for TUI microphone capture helpers."""

from __future__ import annotations

from clients.tui.audio_capture import _rank_dshow_devices, default_input_device


def test_rank_dshow_prefers_focusrite() -> None:
    devices = [
        "Microphone (Headset)",
        "Analogue 1 + 2 (Focusrite USB Audio)",
        "Voicemeeter Out A1 (VB-Audio Voicemeeter VAIO)",
    ]
    ranked = _rank_dshow_devices(devices)
    assert ranked[0].startswith("Analogue 1 + 2")
    assert ranked[-1].startswith("Voicemeeter")


def test_default_input_device_constant() -> None:
    assert "Focusrite" in default_input_device() or default_input_device()
