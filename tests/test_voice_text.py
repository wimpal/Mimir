"""Tests for speech-oriented TTS text prep."""

from __future__ import annotations

from brain.voice.text import prepare_text_for_speech


def test_strips_markdown_and_bullets() -> None:
    raw = "**Kaas** staat op de lijst.\n\n- melk\n- brood"
    out = prepare_text_for_speech(raw, locale="nl")
    assert "**" not in out
    assert "Kaas staat op de lijst" in out
    assert "melk" in out
    assert "brood" in out


def test_expands_currency_nl() -> None:
    out = prepare_text_for_speech("We betaalden €12,50.", locale="nl")
    assert "12 euro en 50 cent" in out


def test_expands_temperature_nl() -> None:
    out = prepare_text_for_speech("Morgen 18 °C.", locale="nl")
    assert "18 graden Celsius" in out


def test_pronunciation_sir_nl() -> None:
    out = prepare_text_for_speech("Goedemorgen sir.", locale="nl")
    assert "[[sɜː]]" in out


def test_empty_returns_empty() -> None:
    assert prepare_text_for_speech("   ", locale="nl") == ""
