"""
config/config.py — Central configuration for all Python pipeline scripts.

Usage:
    from config.config import PROJECT_ROOT, DB_FILE, ADS_API_TOKEN

Tokens are read from environment variables. Set them before running:
    export ADS_API_TOKEN=your_token_here
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
ADS_API_TOKEN = os.environ.get("ADS_API_TOKEN", "")

# ── arXiv settings ─────────────────────────────────────────────────────────────
ARXIV_EPRINT_URL  = "https://arxiv.org/e-print/{arxiv_id}"
REQUEST_DELAY_SEC = 3
REQUEST_TIMEOUT   = 30


def require_ads_token() -> str:
    """Return ADS_API_TOKEN or raise a clear error if unset."""
    if not ADS_API_TOKEN:
        raise EnvironmentError(
            "\n[ERROR] ADS_API_TOKEN is not set.\n"
            "Run: export ADS_API_TOKEN=your_token_here"
        )
    return ADS_API_TOKEN


def require_db() -> Path:
    """Return DB_FILE or raise a clear error if it does not exist."""
    if not DB_FILE.exists():
        raise FileNotFoundError(
            f"\n[ERROR] Database not found at {DB_FILE}\n"
            "Run collect.sh first to populate the database."
        )
    return DB_FILE
