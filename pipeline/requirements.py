"""
pipeline/requirements.py — Extract and merge requirements for a repo.

Replaces src/requirements.sh. Uses nbformat + ast instead of the
bash approach of nbconvert-to-python then grep.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from pipeline.logger import log

# Standard library module names to skip (mirrors the bash array)
_STDLIB: frozenset[str] = frozenset({
    "abc", "argparse", "array", "ast", "asynchat", "asyncio", "asyncore",
    "base64", "binascii", "bisect", "builtins", "calendar", "collections",
    "concurrent", "contextlib", "copy", "copyreg", "csv", "ctypes",
    "datetime", "decimal", "difflib", "dis", "distutils", "doctest",
    "email", "encodings", "enum", "errno", "filecmp", "fileinput",
    "fnmatch", "fractions", "functools", "gc", "getopt", "getpass",
    "gettext", "glob", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "imaplib", "imp", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "keyword", "linecache", "locale", "logging",
    "lzma", "mailbox", "math", "mmap", "modulefinder", "multiprocessing",
    "numbers", "operator", "optparse", "os", "pathlib", "pdb", "pickle",
    "pkgutil", "platform", "plistlib", "poplib", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "queue", "quopri", "random",
    "re", "readline", "reprlib", "sched", "selectors", "shelve", "shlex",
    "shutil", "signal", "site", "smtpd", "smtplib", "socket",
    "socketserver", "sqlite3", "ssl", "stat", "string", "stringprep",
    "struct", "subprocess", "sys", "sysconfig", "tarfile", "telnetlib",
    "tempfile", "termios", "textwrap", "threading", "time", "timeit",
    "tokenize", "traceback", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "warnings", "wave", "weakref", "webbrowser",
    "xml", "xmlrpc", "zipfile", "zipimport", "zlib",
})


def _extract_imports_from_source(source: str) -> set[str]:
    """Parse Python source with ast and return top-level imported module names."""
    modules: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return modules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def _extract_imports_from_notebook(nb_path: Path) -> set[str]:
    """Extract imported module names from all code cells in a .ipynb file."""
    try:
        nb = json.loads(nb_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        log(f"[REQUIREMENT] Could not parse {nb_path}: {e}")
        return set()

    modules: set[str] = set()
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        # Strip IPython magics before parsing
        clean = "\n".join(
            line for line in source.splitlines()
            if not line.strip().startswith(("%", "!"))
        )
        modules |= _extract_imports_from_source(clean)
    return modules


def process_requirements(
    repo_dir: Path,
    notebook_paths: str,
    requirement_paths: str,
) -> Path:
    """
    Build a combined requirements.txt for *repo_dir* by merging:
      1. Any explicitly provided requirements files (requirement_paths)
      2. Imports extracted from notebooks (notebook_paths)
    Returns the path to the written requirements.txt.

    Replaces bash process_requirements().
    """
    log("[REQUIREMENT] Processing requirements for repository...")

    file_reqs: list[str] = []
    nb_reqs: set[str] = set()

    # ── Part 1: explicit requirements files ───────────────────────────────────
    if requirement_paths:
        for req_rel in requirement_paths.split(";"):
            req_rel = req_rel.strip()
            if not req_rel:
                continue
            full = repo_dir / req_rel
            if full.exists():
                log(f"[REQUIREMENT] Adding {full} to combined requirements.")
                for line in full.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        file_reqs.append(line)
            else:
                log(f"[WARNING] Requirements file '{full}' not found, skipping.")
    else:
        log("[REQUIREMENT] No REQUIREMENT_PATHS provided.")

    # ── Part 2: imports from notebooks ────────────────────────────────────────
    if notebook_paths:
        for nb_rel in notebook_paths.split(";"):
            nb_rel = nb_rel.strip()
            if not nb_rel:
                continue
            nb_path = repo_dir / nb_rel
            if not nb_path.exists():
                log(f"[WARNING] Notebook '{nb_path}' not found, skipping.")
                continue
            log(f"[REQUIREMENT] Extracting imports from {nb_path.name} ...")
            raw = _extract_imports_from_notebook(nb_path)
            for mod in raw:
                if not mod or mod in _STDLIB:
                    continue
                # Skip local modules (a .py file with the same name exists in repo)
                if list(repo_dir.rglob(f"{mod}.py")):
                    log(f"[REQUIREMENT] Skipping local module: {mod}")
                    continue
                nb_reqs.add(mod)
                log(f"[REQUIREMENT] Added external library from notebook: {mod}")
    else:
        log("[WARNING] No NOTEBOOK_PATHS provided, skipping import extraction.")

    # ── Part 3: combine and deduplicate ───────────────────────────────────────
    combined = sorted(set(file_reqs) | nb_reqs)
    requirements_file = repo_dir / "requirements.txt"
    requirements_file.write_text("\n".join(combined) + ("\n" if combined else ""),
                                  encoding="utf-8")

    if combined:
        log(f"[REQUIREMENT] requirements.txt written with {len(combined)} packages.")
    else:
        log("[WARNING] No requirements found. Empty requirements.txt created.")

    return requirements_file
