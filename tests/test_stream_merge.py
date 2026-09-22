"""T-072: SSE stream vs final reply merge must not double-emit."""

from brain.service import stream_final_unsent


def test_stream_final_unsent_empty_stream_emits_full_reply() -> None:
    assert stream_final_unsent("", "Office light is off.") == "Office light is off."


def test_stream_final_unsent_prefix_emits_suffix_only() -> None:
    assert stream_final_unsent("Office light", "Office light is off.") == " is off."


def test_stream_final_unsent_exact_match_emits_nothing() -> None:
    text = "Graag!"
    assert stream_final_unsent(text, text) == ""


def test_stream_final_unsent_streamed_longer_emits_nothing() -> None:
    assert stream_final_unsent("Hello there.", "Hello") == ""


def test_stream_final_unsent_mismatch_does_not_reemit_full_reply() -> None:
    # Emoji strip / sanitize can make streamed diverge from final reply so
    # neither is a prefix of the other. Old code re-emitted the full reply.
    streamed = "Thanks! "
    reply = "Graag!"
    assert not reply.startswith(streamed)
    assert not streamed.startswith(reply)
    assert stream_final_unsent(streamed, reply) == ""
