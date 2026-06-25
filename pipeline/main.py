"""
pipeline/main.py — Reproducibility Astro pipeline entry point.

Usage:
    python3 pipeline/main.py setup            # check/set API tokens
    python3 pipeline/main.py collect          # fetch articles from NASA ADS
    python3 pipeline/main.py mentions         # extract notebook mentions from arXiv
    python3 pipeline/main.py mentions --limit N  # process only N articles
    python3 pipeline/main.py run              # clone repos, score, execute notebooks
    python3 pipeline/main.py run --count N    # process N repos (default: 1)
    python3 pipeline/main.py run --interactive  # enter a single repo URL manually
    python3 pipeline/main.py score --repo-dir <path> --repo-id <int>
    python3 pipeline/main.py all              # run full pipeline (collect → mentions → run)

Tokens — set these before running:
    export ADS_API_TOKEN=your_ads_token_here
    export GITHUB_API_TOKEN=your_github_token_here  # only needed for run
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Ensure project root is on sys.path so config and pipeline packages resolve
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.config import DB_FILE, require_ads_token


# ── Token setup ───────────────────────────────────────────────────────────────

def cmd_setup(args) -> int:
    """Check API tokens and guide the user to set missing ones."""
    print("\n── Token Setup ──────────────────────────────────────")

    ads_token = os.environ.get("ADS_API_TOKEN", "")
    github_token = os.environ.get("GITHUB_API_TOKEN", "")

    if ads_token:
        print(f"  ADS_API_TOKEN     : SET ({ads_token[:6]}...)")
    else:
        print("  ADS_API_TOKEN     : NOT SET")
        print("    → Get yours at: https://ui.adsabs.harvard.edu → Account → Settings → API Token")
        print("    → Then run:     export ADS_API_TOKEN=your_token_here")

    if github_token:
        print(f"  GITHUB_API_TOKEN  : SET ({github_token[:6]}...)")
    else:
        print("  GITHUB_API_TOKEN  : NOT SET (only needed for run)")
        print("    → Get yours at: https://github.com → Settings → Developer settings → Personal access tokens")
        print("    → Then run:     export GITHUB_API_TOKEN=your_token_here")

    print("─────────────────────────────────────────────────────")

    if not ads_token:
        print("\n[SETUP] ADS_API_TOKEN is required for collect. Set it and re-run.")
        return 1

    print("\n[SETUP] Validating ADS token ...")
    try:
        import requests
        resp = requests.get(
            "https://api.adsabs.harvard.edu/v1/search/query",
            params={"q": "test", "rows": 1},
            headers={"Authorization": f"Bearer {ads_token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            print("[SETUP] ADS token valid ✓")
        elif resp.status_code == 401:
            print("[SETUP] ADS token invalid or expired ✗")
            return 1
        else:
            print(f"[SETUP] ADS API returned {resp.status_code} — may be a temporary issue")
    except Exception as e:
        print(f"[SETUP] Could not reach ADS API: {e}")
        return 1

    print("\n[SETUP] All required tokens are set. Ready to run the pipeline.")
    print("  Next: python3 pipeline/main.py collect")
    return 0


# ── Subcommand handlers ────────────────────────────────────────────────────────

def cmd_collect(args) -> int:
    """Fetch articles from NASA ADS and populate the database."""
    try:
        require_ads_token()
    except EnvironmentError as e:
        print(e, file=sys.stderr)
        print("Run: python3 pipeline/main.py setup", file=sys.stderr)
        return 1
    print("[MAIN] Running collect_ads.py ...")
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "pipeline" / "collect_ads.py")],
        env={**os.environ},
    )
    return result.returncode


def cmd_mentions(args) -> int:
    """Extract notebook mentions from arXiv LaTeX sources."""
    print("[MAIN] Running extract_mentions.py ...")
    cmd = [sys.executable, str(PROJECT_ROOT / "pipeline" / "extract_mentions.py")]
    if hasattr(args, "limit") and args.limit:
        cmd += ["--limit", str(args.limit)]
    result = subprocess.run(cmd, env={**os.environ})
    return result.returncode


def cmd_run(args) -> int:
    """Clone repos, score with RRS, execute notebooks — pure Python, no bash."""
    from pipeline.runner import run
    return run(
        target_count=args.count,
        interactive=getattr(args, "interactive", False),
    )


def cmd_score(args) -> int:
    """Score a single cloned repository with RRS."""
    if not args.repo_dir or not args.repo_id:
        print("[ERROR] --repo-dir and --repo-id are required for score.", file=sys.stderr)
        return 1
    print(f"[MAIN] Scoring repo_id={args.repo_id} at {args.repo_dir} ...")
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


def cmd_all(args) -> int:
    """Run the full pipeline: collect → mentions → run."""
    print("[MAIN] Running full pipeline ...")

    rc = cmd_collect(args)
    if rc != 0:
        print("[MAIN] collect failed — aborting.", file=sys.stderr)
        return rc

    rc = cmd_mentions(args)
    if rc != 0:
        print("[MAIN] mentions failed — aborting.", file=sys.stderr)
        return rc

    return cmd_run(args)


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Reproducibility Astro — pipeline entry point",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Check and validate API tokens")

    sub.add_parser("collect", help="Fetch articles from NASA ADS")

    mentions_p = sub.add_parser("mentions", help="Extract notebook mentions from arXiv")
    mentions_p.add_argument("--limit", type=int, default=None,
                            help="Process only N articles (default: all)")

    run_p = sub.add_parser("run", help="Clone, score, and execute repos")
    run_p.add_argument("--count", type=int, default=1,
                       help="Number of repos to process (default: 1)")
    run_p.add_argument("--interactive", action="store_true",
                       help="Enter a single repo URL manually (option 1 mode)")

    score_p = sub.add_parser("score", help="Score a single cloned repo")
    score_p.add_argument("--repo-dir", required=True, help="Path to cloned repo")
    score_p.add_argument("--repo-id", required=True, type=int,
                         help="Row ID in repo_targets table")

    all_p = sub.add_parser("all", help="Run full pipeline: collect → mentions → run")
    all_p.add_argument("--count", type=int, default=1,
                       help="Number of repos to process in run step (default: 1)")
    all_p.add_argument("--limit", type=int, default=None,
                       help="Limit articles for mentions step (default: all)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "setup":    cmd_setup,
        "collect":  cmd_collect,
        "mentions": cmd_mentions,
        "run":      cmd_run,
        "score":    cmd_score,
        "all":      cmd_all,
    }
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
