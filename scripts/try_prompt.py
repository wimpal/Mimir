"""Dev utility: talk to the raw model with Mimir's system prompt (bypasses the brain).

Useful for iterating on config/system_prompt.md before Phase 2 wires prompts
into the real service. Not product code.

One-shot:  uv run python scripts/try_prompt.py "how was your day?"
Chat REPL: uv run python scripts/try_prompt.py
Raw model: uv run python scripts/try_prompt.py --no-system "who are you?"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brain.ollama import ChatMessage, OllamaClient, OllamaError

PROMPT_PATH = Path("config/system_prompt.md")


def ask(client: OllamaClient, messages: list[ChatMessage], *, think: bool) -> str:
    try:
        response = client.chat(messages, think=think, stream=False)
    except OllamaError as exc:
        print(f"ERROR: Ollama unreachable ({exc})", file=sys.stderr)
        raise SystemExit(1) from exc
    return response.message.content or ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*", help="one-shot message; omit for REPL")
    parser.add_argument(
        "--no-system", action="store_true", help="skip system prompt (compare raw model)"
    )
    args = parser.parse_args()

    from brain.config import load_config

    cfg = load_config()
    think = cfg.ollama.think

    messages: list[ChatMessage] = []
    if not args.no_system:
        if not PROMPT_PATH.is_file():
            print(f"ERROR: {PROMPT_PATH} not found", file=sys.stderr)
            return 1
        messages.append(
            ChatMessage(role="system", content=PROMPT_PATH.read_text(encoding="utf-8"))
        )
    label = "model" if args.no_system else "mimir"

    with OllamaClient(
        cfg.ollama.url,
        cfg.ollama.model,
        num_ctx=cfg.ollama.num_ctx,
        timeout_s=cfg.timeouts.ollama_s,
    ) as client:
        if args.message:
            messages.append(ChatMessage(role="user", content=" ".join(args.message)))
            print(ask(client, messages, think=think))
            return 0

        mode = "no system prompt" if args.no_system else "system prompt"
        print(f"REPL ({label}, {mode}). Empty line or Ctrl+C to quit.")
        while True:
            try:
                line = input(f"{label}> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                break
            turn = list(messages) + [ChatMessage(role="user", content=line)]
            reply = ask(client, turn, think=think)
            print(reply)
            messages.append(ChatMessage(role="user", content=line))
            messages.append(ChatMessage(role="assistant", content=reply))

    return 0


if __name__ == "__main__":
    sys.exit(main())
