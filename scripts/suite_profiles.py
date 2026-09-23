#!/usr/bin/env python3
"""Run tool_call_suite across Model profiles (T-074 / T-075 bake-offs).

Examples:
  uv run python scripts/suite_profiles.py --pulled-only
  uv run python scripts/suite_profiles.py default granite42_8b mythos_9b_unhinged
  uv run python scripts/suite_profiles.py --pulled-only --no-restore-default

Writes per-profile logs under data/logs/suite_<profile>_<stamp>.log and a
scoreboard summary file. Restores Active ``default`` at the end unless
``--no-restore-default``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_SUMMARY_RE = re.compile(
    r"^(?:model=|pass_rate=|right_tool=|latency_ms |EXIT |reasons=|weather_pinned=|"
    r"pref_pinned=|jellyfin_pinned=|calendar_pinned=|followup_pinned=).*$",
    re.MULTILINE,
)
_PASS_RE = re.compile(
    r"pass_rate=(?P<passed>\d+)/(?P<total>\d+) \((?P<pct>\d+)%\)"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(
    argv: list[str],
    *,
    cwd: Path,
    log_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    merged = os.environ.copy()
    # Windows consoles often use cp1252; suite previews can include exotic Unicode.
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    merged.setdefault("PYTHONUTF8", "1")
    if env:
        merged.update(env)
    if log_path is None:
        completed = subprocess.run(argv, cwd=str(cwd), env=merged, check=False)
        return int(completed.returncode)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as fh:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            env=merged,
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return int(completed.returncode)


def _ollama_tags(url: str) -> set[str]:
    base = url.rstrip("/")
    req = urllib.request.Request(f"{base}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"ERROR: cannot list Ollama tags at {base}: {exc}") from exc
    names: set[str] = set()
    for row in data.get("models") or []:
        name = row.get("name")
        if isinstance(name, str) and name:
            names.add(name)
            # Ollama may omit :latest in some listings; accept both forms.
            if ":" not in name:
                names.add(f"{name}:latest")
            elif name.endswith(":latest"):
                names.add(name[: -len(":latest")])
    return names


def _load_profile_models(cwd: Path) -> dict[str, str]:
    """Return {profile_name: ollama_model} via model_profiles list parsing."""
    # Prefer config load in-process so we don't depend on CLI text format.
    sys.path.insert(0, str(cwd))
    from brain.config import load_config

    settings = load_config(resolve_active=False)
    return {name: p.model for name, p in settings.ollama.profiles.items()}


def _extract_scoreboard(text: str) -> tuple[str | None, str | None, bool | None]:
    """Return (pass_rate_line, model_line, exit_ok)."""
    model_line = None
    pass_line = None
    exit_ok: bool | None = None
    for line in text.splitlines():
        if line.startswith("model="):
            model_line = line.strip()
        elif line.startswith("pass_rate="):
            pass_line = line.strip()
        elif line.startswith("EXIT OK"):
            exit_ok = True
        elif line.startswith("EXIT FAIL"):
            exit_ok = False
    return pass_line, model_line, exit_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batch-run tool_call_suite across Model profiles.",
    )
    parser.add_argument(
        "profiles",
        nargs="*",
        help="Profile names (default: all catalog profiles, or --pulled-only)",
    )
    parser.add_argument(
        "--pulled-only",
        action="store_true",
        help="Skip profiles whose Ollama tag is not present locally",
    )
    parser.add_argument(
        "--no-restore-default",
        action="store_true",
        help="Leave Active on the last trial profile (skip restore)",
    )
    parser.add_argument(
        "--skip-restart",
        action="store_true",
        help="Only write sticky Active (you restart the brain yourself)",
    )
    args = parser.parse_args(argv)

    cwd = _repo_root()
    catalog = _load_profile_models(cwd)
    if not catalog:
        print("ERROR: no ollama.profiles in config", file=sys.stderr)
        return 1

    selected = list(args.profiles) if args.profiles else sorted(catalog)
    unknown = [n for n in selected if n not in catalog]
    if unknown:
        known = ", ".join(sorted(catalog))
        print(f"ERROR: unknown profile(s) {unknown}; known: {known}", file=sys.stderr)
        return 1

    if args.pulled_only:
        from brain.config import load_config

        settings = load_config(resolve_active=False)
        tags = _ollama_tags(settings.ollama.url)
        kept: list[str] = []
        for name in selected:
            tag = catalog[name]
            if tag in tags or tag.split(":")[0] in tags:
                kept.append(name)
            else:
                print(f"skip {name}: ollama tag not pulled ({tag})")
        selected = kept

    if not selected:
        print("ERROR: no profiles to run", file=sys.stderr)
        return 1

    from brain.config import load_config as _load_active

    prior_active = _load_active().ollama.active_profile
    print(f"prior_active={prior_active}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_dir = cwd / "data" / "logs"
    summary_path = log_dir / f"suite_batch_{stamp}.txt"
    rows: list[str] = []

    print(f"profiles={selected}")
    print(f"summary={summary_path}")

    overall_rc = 0
    with summary_path.open("w", encoding="utf-8") as summary:
        summary.write(f"suite_profiles batch {stamp}\n")
        summary.write(f"profiles={selected}\n")
        summary.write(f"prior_active={prior_active}\n\n")

        for name in selected:
            print(f"\n######## PROFILE {name} ({catalog[name]}) ########", flush=True)
            summary.write(f"######## {name} ({catalog[name]}) ########\n")
            use_argv = [
                sys.executable,
                "-m",
                "brain.model_profiles",
                "use",
                name,
            ]
            if not args.skip_restart:
                use_argv.append("--restart")
            rc_use = _run(use_argv, cwd=cwd)
            if rc_use != 0:
                print(f"ERROR: failed to switch to {name} (rc={rc_use})", file=sys.stderr)
                summary.write(f"SWITCH_FAIL rc={rc_use}\n\n")
                overall_rc = 1
                continue

            log_path = log_dir / f"suite_{name}_{stamp}.log"
            json_path = log_dir / f"suite_{name}_{stamp}_autopsy.json"
            rc_suite = _run(
                [
                    sys.executable,
                    str(cwd / "scripts" / "tool_call_suite.py"),
                    "--json-out",
                    str(json_path),
                ],
                cwd=cwd,
                log_path=log_path,
            )
            text = log_path.read_text(encoding="utf-8", errors="replace")
            pass_line, model_line, exit_ok = _extract_scoreboard(text)
            header_bits = [model_line or f"model={catalog[name]}", pass_line or "pass_rate=?"]
            if exit_ok is True:
                header_bits.append("EXIT OK")
            elif exit_ok is False:
                header_bits.append("EXIT FAIL")
            else:
                header_bits.append(f"suite_rc={rc_suite}")
            line = " | ".join(header_bits)
            print(line, flush=True)
            rows.append(f"{name}: {line}")
            summary.write(line + "\n")
            for m in _SUMMARY_RE.finditer(text):
                summary.write(m.group(0) + "\n")
            summary.write(f"log={log_path}\n\n")
            if rc_suite != 0:
                overall_rc = 1

        if not args.no_restore_default:
            restore_name = prior_active if prior_active in catalog else "default"
            print(f"\n######## restore {restore_name} ########", flush=True)
            restore = [
                sys.executable,
                "-m",
                "brain.model_profiles",
                "use",
                restore_name,
            ]
            if not args.skip_restart:
                restore.append("--restart")
            rc_restore = _run(restore, cwd=cwd)
            summary.write(f"restore_{restore_name} rc={rc_restore}\n")
            if rc_restore != 0:
                overall_rc = 1

        summary.write("\n=== Scoreboard ===\n")
        for row in rows:
            summary.write(row + "\n")
            print(row)

    print(f"\nWrote {summary_path}")
    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
