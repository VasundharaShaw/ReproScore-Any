"""
pipeline/logger.py — Logging utilities for the pipeline.

Replaces src/logging.sh (log, now_sec, elapsed_sec).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

_logger = logging.getLogger("repro_astro")
_log_file: Path | None = None


def configure(log_file: Path | None = None, level: int = logging.INFO) -> None:
    """Call once at startup (or per-repo) to set output destination."""
    global _log_file
    _log_file = log_file

    _logger.setLevel(level)
    _logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # Always log to stdout
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    _logger.addHandler(sh)

    # Optionally mirror to a file
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        _logger.addHandler(fh)


def log(msg: str) -> None:
    """Drop-in replacement for bash log()."""
    _logger.info(msg)


def now_sec() -> float:
    """Return current epoch time in seconds (replaces bash now_sec)."""
    return time.monotonic()


def elapsed_sec(start: float) -> float:
    """Return seconds elapsed since start (replaces bash elapsed_sec)."""
    return round(time.monotonic() - start, 2)
