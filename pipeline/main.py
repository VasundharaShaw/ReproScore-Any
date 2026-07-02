"""
pipeline/main.py — ReproScore pipeline entry point.

Usage:
    python3 -m pipeline setup                     # check environment
    python3 -m pipeline run                       # score one repo interactively
    python3 -m pipeline run --count N             # process N repos from DB
    python3 -m pipeline run --interactive         # enter a single repo URL manually
    python3 -m pipeline score --repo-dir <path> --repo-id <int>

Optional token (for higher GitHub API rate limits):
    export GITHUB_API_TOKEN=your_token_here
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.config import DB_FILE


# ── Token setup ───────────────────────────────────────────────────────────────

def cmd_setup(args) -> int:
    """Check environment and guide the user to set missing tokens."""
    print("\n── Environment Setup ────────────────────────────────")

    github_token = os.environ.get("GITHUB_API_TOKEN", "")

    if github_token:
        print(f"  GITHUB_API_TOKEN  : SET ({github_token[:6]}...)")
    else:
        print("  GITHUB_API_TOKEN  : NOT SET (optional — increases GitHub API rate limits)")
        print("    → Get yours at: https://github.com → Settings → Developer settings → Personal access tokens")
        print("    → Then run:     export GITHUB_API_TOKEN=your_token_here")

    print("─────────────────────────────────────────────────────")
    print("\n[SETUP] Environment ready.")
    print("  Next: python3 -m pipeline run --interactive")
    return 0


# ── Subcommand handlers ────────────────────────────────────────────────────────

def cmd_run(args) -> int:
    """Clone repos, score with RRS/ROS/RCS, execute notebooks."""
    from pipeline.runner import run
    return run(
        target_count=args.count,
        interactive=getattr(args, "interactive", False),
    )


def cmd_score(args) -> int:
    """Score a single cloned repository."""
    if not args.repo_dir or not args.repo_id:
        print("[ERROR] --repo-dir and --repo-id are required for score.", file=sys.stderr)
        return 1
    print(f"[MAIN] Scoring repo_id={args.repo_id} at {args.repo_dir} ...")
    import subprocess
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "pipeline" / "score.py"),
            "--repo-dir", args.repo_dir,
            "--repo-id", str(args.repo_id),
            "--db", str(DB_FILE),
        ],
        env={**os.environ},
    )
    return result.returncode


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="ReproScore — notebook reproducibility scorer",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Check environment and optional tokens")

    run_p = sub.add_parser("run", help="Clone, score, and execute repos")
    run_p.add_argument("--count", type=int, default=1,
                       help="Number of repos to process from DB (default: 1)")
    run_p.add_argument("--interactive", action="store_true",
                       help="Enter a single repo URL manually")

    score_p = sub.add_parser("score", help="Score a single cloned repo")
    score_p.add_argument("--repo-dir", required=True, help="Path to cloned repo")
    score_p.add_argument("--repo-id", required=True, type=int,
                         help="Row ID in repo_targets table")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "setup": cmd_setup,
        "run":   cmd_run,
        "score": cmd_score,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
