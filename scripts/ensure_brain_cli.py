"""CLI wrapper for brain_launcher.ensure_brain_running (Task Scheduler / login)."""

from __future__ import annotations

import argparse
import sys

from clients.tui.brain_client import normalize_brain_url
from clients.tui.brain_launcher import ensure_brain_running


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure Mimir brain is running.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="Brain base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for /health after spawn (default: 60)",
    )
    args = parser.parse_args()
    url = normalize_brain_url(args.url)
    result = ensure_brain_running(url, ready_timeout_s=args.ready_timeout)
    print(result.message)
    if result.already_running or result.started:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
