"""Dev utility: talk to the raw model with Mimir's system prompt (bypasses the brain).

Useful for iterating on config/system_prompt.md before Phase 2 wires prompts
into the real service. Not product code; stdlib only.

One-shot:  uv run python scripts/try_prompt.py "how was your day?"
Chat REPL: uv run python scripts/try_prompt.py
Raw model: uv run python scripts/try_prompt.py --no-system "who are you?"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROMPT_PATH = Path("config/system_prompt.md")


def ask(url: str, model: str, messages: list[dict]) -> str:
    body = json.dumps(
        {"model": model, "messages": messages, "stream": False, "think": False}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)["message"]["content"]
    except urllib.error.URLError as exc:
        print(f"ERROR: Ollama unreachable at {url} ({exc.reason})", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="*", help="one-shot message; omit for REPL")
    parser.add_argument(
        "--no-system", action="store_true", help="skip system prompt (compare raw model)"
    )
    args = parser.parse_args()

    from brain.config import load_config

    cfg = load_config()
    url = cfg.ollama.url.rstrip("/") + "/api/chat"
    model = cfg.ollama.model

    messages: list[dict] = []
    if not args.no_system:
        if not PROMPT_PATH.is_file():
            print(f"ERROR: {PROMPT_PATH} not found", file=sys.stderr)
            return 1
        messages.append({"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")})
    label = "model" if args.no_system else "mimir"

    if args.message:
        messages.append({"role": "user", "content": " ".join(args.message)})
        print(ask(url, model, messages))
        return 0

    mode = "no system prompt" if args.no_system else "system prompt"
    print(f"{label} chat ({model}, {mode}). Ctrl+C to exit.")
    while True:
        try:
            line = input("you> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if not line:
            continue
        messages.append({"role": "user", "content": line})
        reply = ask(url, model, messages)
        messages.append({"role": "assistant", "content": reply})
        print(f"{label}> {reply}\n")


if __name__ == "__main__":
    sys.exit(main())
