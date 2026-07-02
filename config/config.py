"""
config/config.py — Central configuration for the ReproScore pipeline.

Usage:
    from config.config import PROJECT_ROOT, DB_FILE

Optional:
    export GITHUB_API_TOKEN=your_token_here  # for higher GitHub API rate limits
"""
from __future__ import annotations
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR    = PROJECT_ROOT / "input"
OUTPUT_DIR   = PROJECT_ROOT / "output"
REPOS_DIR    = OUTPUT_DIR / "cloned_repos"
COMP_DIR     = OUTPUT_DIR / "comparisons"
LOG_DIR      = OUTPUT_DIR / "logs"
DB_DIR       = OUTPUT_DIR / "db"
DB_FILE      = DB_DIR / "db.sqlite"

# ── Execution settings ─────────────────────────────────────────────────────────
TARGET_COUNT = int(os.environ.get("TARGET_COUNT", "10"))

# ── API tokens ─────────────────────────────────────────────────────────────────
GITHUB_API_TOKEN = os.environ.get("GITHUB_API_TOKEN", "")

# ── Request settings ───────────────────────────────────────────────────────────
REQUEST_DELAY_SEC = 3
REQUEST_TIMEOUT   = 30

def require_db() -> Path:
    """Return DB_FILE or raise a clear error if it does not exist."""
    if not DB_FILE.exists():
        raise FileNotFoundError(
            f"\n[ERROR] Database not found at {DB_FILE}\n"
            "Run: python3 -m pipeline run --interactive  to score a repo."
        )
    return DB_FILE
