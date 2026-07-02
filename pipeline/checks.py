"""
pipeline/checks.py — Dependency and repository validation.

Replaces src/checks.sh (command_exists, validate_repo).
"""
from __future__ import annotations

import shutil
import subprocess

from pipeline.logger import log


def command_exists(name: str) -> bool:
    """Return True if *name* is available on PATH (replaces bash command_exists)."""
    return shutil.which(name) is not None


def require_commands(*names: str) -> None:
    """Raise RuntimeError listing any commands not found on PATH."""
    missing = [n for n in names if not command_exists(n)]
    if missing:
        raise RuntimeError(f"Required commands not found: {', '.join(missing)}")


def validate_repo(repo_url: str) -> bool:
    """
    Return True if *repo_url* is a reachable git remote.
    Replaces bash validate_repo() which ran: git ls-remote <url>
    """
    log(f"[REPO] Validating repository URL: {repo_url}")
    result = subprocess.run(
        ["git", "ls-remote", repo_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode == 0:
        log("[REPO] Repository URL is valid.")
        return True
    else:
        log(f"[ERROR] Invalid repository URL - {repo_url}")
        return False
