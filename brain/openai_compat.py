"""Thin OpenAI-compatible adapter — tools stay inside the brain."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from brain.service import BrainService, ChatOutcome


def openai_completion_from_outcome(
    outcome: ChatOutcome,
    *,
    model: str,
) -> dict[str, Any]:
    """Map a BrainService outcome to a non-streaming chat.completion object."""
    return {
        "id": f"chatcmpl-{outcome.turn_id or uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": outcome.reply,
                },
                "finish_reason": "stop"
                if outcome.stopped_reason == "final"
                else outcome.stopped_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def run_openai_chat(
    service: BrainService,
    body: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Handle an OpenAI-style chat/completions request.

    Client-supplied ``tools`` are ignored — Mimir runs its own tool loop.
    Client ``system`` messages are dropped by BrainService.build_messages.
    """
    stream = bool(body.get("stream", False))
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        return 400, {
            "error": {
                "message": "messages must be a non-empty array",
                "type": "invalid_request_error",
            }
        }

    messages: list[dict[str, Any]] = []
    for m in raw_messages:
        if isinstance(m, dict):
            messages.append(m)

    outcome = service.run_chat(messages=messages, stream=stream)
    if outcome.http_status == 501:
        return 501, {
            "error": {
                "message": outcome.reply,
                "type": "not_implemented",
            }
        }
    if outcome.http_status == 400:
        return 400, {
            "error": {
                "message": outcome.reply,
                "type": "invalid_request_error",
            }
        }

    model = service.settings.ollama.model
    return 200, openai_completion_from_outcome(outcome, model=model)


def models_list(model: str) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": "mimir",
            }
        ],
    }
