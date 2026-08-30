"""Tests for TTS audio assembly helpers (no live Piper models)."""

from __future__ import annotations

import numpy as np

from brain.voice.tts import _apply_fade, _normalize_audio, _silence_samples


def test_silence_samples() -> None:
    s = _silence_samples(22050, 0.1)
    assert s.shape == (2205,)
    assert np.all(s == 0)


def test_peak_normalize() -> None:
    audio = np.array([0.25, -0.5, 0.25], dtype=np.float32)
    out = _normalize_audio(audio, mode="peak", target_peak=1.0)
    assert float(np.max(np.abs(out))) == 1.0


def test_fade_reduces_edges() -> None:
    audio = np.ones(1000, dtype=np.float32)
    out = _apply_fade(audio, sample_rate=22050, fade_ms=10.0)
    assert out[0] < 1.0
    assert out[-1] < 1.0
    assert out[500] == 1.0
