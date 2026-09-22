"""CLI for Model profiles + sticky Active model (T-074 / ADR-016).

Usage:
  uv run python -m brain.model_profiles list
  uv run python -m brain.model_profiles show
  uv run python -m brain.model_profiles use <name> [--restart]
  uv run python -m brain.model_profiles use default [--restart]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from brain.active_model import ActiveModelStateError, write_active_profile
from brain.config import (
    _OLLAMA_EFFECTIVE_ENV,
    ConfigError,
    Settings,
    find_mimir_repo_root,
    load_config,
)


def _cmd_list(settings: Settings) -> int:
    active = settings.ollama.active_profile
    print(f"active_profile={active} (sticky; may be unknown until repaired)")
    print("profiles:")
    for name in sorted(settings.ollama.profiles):
        p = settings.ollama.profiles[name]
        marker = " *" if name == active else ""
        print(f"  {name}: model={p.model} num_ctx={p.num_ctx} think={p.think}{marker}")
    return 0


def _cmd_show(settings: Settings) -> int:
    o = settings.ollama
    print(f"active_profile={o.active_profile}")
    print(f"model={o.model}")
    print(f"num_ctx={o.num_ctx}")
    print(f"think={o.think}")
    print(f"keep_alive={o.keep_alive}")
    if o.env_masked_fields:
        parts = [
            f"{field} via {_OLLAMA_EFFECTIVE_ENV[field]}"
            for field in o.env_masked_fields
        ]
        print(f"env_mask={', '.join(parts)}")
    else:
        print("env_mask=(none)")
    if o.active_profile not in o.profiles:
        print(
            f"WARNING: Active profile {o.active_profile!r} is not in catalog; "
            "run: uv run python -m brain.model_profiles use default",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_use(settings: Settings, name: str, *, restart: bool) -> int:
    cleaned = name.strip()
    if cleaned not in settings.ollama.profiles:
        known = ", ".join(sorted(settings.ollama.profiles))
        print(
            f"unknown profile {cleaned!r}; known: {known}",
            file=sys.stderr,
        )
        return 1
    data_dir = settings.runtime.ensure_data_dir()
    try:
        path = write_active_profile(data_dir, cleaned)
    except ActiveModelStateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    p = settings.ollama.profiles[cleaned]
    print(f"active_profile={cleaned} model={p.model} wrote={path}")
    if not restart:
        print("restart brain to apply (e.g. powershell -File scripts/restart_mimir.ps1 -BrainOnly)")
        return 0
    return _restart_brain()


def _restart_brain() -> int:
    root = find_mimir_repo_root()
    if root is None:
        print(
            "ERROR: cannot find Mimir repo root for restart; "
            "restart manually: powershell -File scripts/restart_mimir.ps1 -BrainOnly",
            file=sys.stderr,
        )
        return 1
    script = root / "scripts" / "restart_mimir.ps1"
    if sys.platform == "win32" and script.is_file():
        print(f"restarting brain via {script} -BrainOnly …")
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-BrainOnly",
            ],
            cwd=str(root),
            check=False,
        )
        return int(completed.returncode)
    print(
        "ERROR: --restart is only automated on Windows via scripts/restart_mimir.ps1; "
        "restart the brain manually, then re-check: "
        "uv run python -m brain.model_profiles show",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m brain.model_profiles",
        description="Switch sticky Active Model profile (T-074).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Config path (default: MIMIR_CONFIG or config/config.yaml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List profiles and sticky Active name")
    sub.add_parser("show", help="Show effective Active model settings")

    use_p = sub.add_parser("use", help="Set sticky Active profile")
    use_p.add_argument("profile", help="Profile name (e.g. default, trial_14b)")
    use_p.add_argument(
        "--restart",
        action="store_true",
        help="Restart brain after switch (Windows: restart_mimir.ps1 -BrainOnly)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # Catalog path: do not fail on stale Active so `use default` can repair.
        catalog = load_config(
            args.config, resolve_active=False
        )
        if args.command == "list":
            return _cmd_list(catalog)
        if args.command == "use":
            return _cmd_use(catalog, args.profile, restart=args.restart)
        if args.command == "show":
            # Prefer fully resolved settings when Active is valid.
            try:
                resolved = load_config(args.config, resolve_active=True)
            except ConfigError:
                return _cmd_show(catalog)
            return _cmd_show(resolved)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
