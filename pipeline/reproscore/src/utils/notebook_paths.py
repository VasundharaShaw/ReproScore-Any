"""
pipeline/reproscore/utils/notebook_paths.py
============================================
Shared utility for finding Jupyter notebooks in a repository,
with consistent exclusion of non-project directories.

Vendored from github.com/myVSR/reproscore — do not edit directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Set

_EXCLUDED_DIRS: Set[str] = {
    ".ipynb_checkpoints",
    ".reproscore",
    "site-packages",
    "venv",
    ".venv",
    "env",
    "node_modules",
    "__pycache__",
    ".git",
    ".tox",
    ".nox",
    "dist",
    "build",
    "eggs",
    ".eggs",
}

_LIB_PYTHON_PREFIX = "lib"
_PYTHON_DIR_PREFIX = "python"


def _has_embedded_python_lib(parts: tuple) -> bool:
    """Return True if the path contains a lib/pythonX.Y segment."""
    for i, part in enumerate(parts):
        if part == _LIB_PYTHON_PREFIX and i + 1 < len(parts):
            nxt = parts[i + 1]
            if nxt.startswith(_PYTHON_DIR_PREFIX) and len(nxt) > len(_PYTHON_DIR_PREFIX):
                return True
    return False


def is_excluded_notebook(path: Path) -> bool:
    """Return True if a notebook path should be excluded from analysis."""
    parts = path.parts
    for part in parts:
        if part in _EXCLUDED_DIRS:
            return True
    return _has_embedded_python_lib(parts)


def find_notebooks(repo: Path) -> list[Path]:
    """Return all project .ipynb files in repo, sorted for determinism."""
    return sorted(
        p for p in repo.rglob("*.ipynb")
        if not is_excluded_notebook(p)
    )
