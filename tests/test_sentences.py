"""Unit tests for streaming sentence boundary detection."""

from __future__ import annotations

from brain.voice.sentences import SentenceBuffer


def test_two_english_sentences() -> None:
    buf = SentenceBuffer()
    assert buf.feed("Hello. Wor") == ["Hello."]
    assert buf.feed("ld!") == ["World!"]
    assert buf.flush() is None


def test_nl_abbreviation_dhr() -> None:
    buf = SentenceBuffer()
    text = "Dhr. Jansen woont hier."
    assert buf.feed(text) == [text]
    assert buf.flush() is None


def test_eg_abbreviation() -> None:
    buf = SentenceBuffer()
    text = "Zie b.v. e.g. dit."
    assert buf.feed(text) == [text]
    assert buf.flush() is None


def test_euro_decimal() -> None:
    buf = SentenceBuffer()
    text = "Het kost €19,23 vandaag."
    assert buf.feed(text) == [text]
    assert buf.flush() is None


def test_exclamation_two_sentences() -> None:
    buf = SentenceBuffer()
    assert buf.feed("Kijk uit! Pas op.") == ["Kijk uit!", "Pas op."]
    assert buf.flush() is None


def test_trailing_no_punctuation() -> None:
    buf = SentenceBuffer()
    assert buf.feed("Nog even") == []
    assert buf.flush() == "Nog even"


def test_empty_feed() -> None:
    buf = SentenceBuffer()
    assert buf.feed("") == []
    assert buf.flush() is None


def test_incremental_multi_sentence() -> None:
    buf = SentenceBuffer()
    out: list[str] = []
    for chunk in ("Eerste zin. ", "Tweede zin. ", "Derde"):
        out.extend(buf.feed(chunk))
    out.extend([buf.flush()] if buf.flush() is not None else [])
    # flush consumed buffer; re-test
    buf2 = SentenceBuffer()
    out2: list[str] = []
    for chunk in ("Eerste zin. ", "Tweede zin. ", "Derde"):
        out2.extend(buf2.feed(chunk))
    tail = buf2.flush()
    if tail:
        out2.append(tail)
    assert out2 == ["Eerste zin.", "Tweede zin.", "Derde"]
