"""
pipeline/env.py — Python version detection, venv creation, and notebook execution.

Replaces src/pyenv.sh (detect_python_version, ensure_pyenv_version,
setup_pyenv_env, run_in_pyenv_env, cleanup_pyenv_env, analyze_env_error).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.logger import log

PYENV_ROOT = Path(os.environ.get("PYENV_ROOT", Path.home() / ".pyenv"))
VENV_BASE_DIR = Path(os.environ.get("VENV_BASE_DIR", Path.home() / ".repo_venvs"))
FALLBACK_PYTHON = "3.10"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class EnvResult:
    ok: bool
    error_type: str = ""
    error_message: str = ""
    venv_dir: Path | None = None
    python_bin: Path | None = None
    pip_bin: Path | None = None


# ---------------------------------------------------------------------------
# Version detection  (replaces detect_python_version)
# ---------------------------------------------------------------------------

def detect_python_version(repo_dir: Path) -> str:
    """
    Detect the Python version a repo wants, in priority order:
      1. binder/runtime.txt
      2. runtime.txt
      3. .python-version
      4. setup.py / setup.cfg python_requires
      5. Fallback: FALLBACK_PYTHON
    """
    for runtime in [repo_dir / "binder" / "runtime.txt", repo_dir / "runtime.txt"]:
        if runtime.exists():
            m = re.search(r"python-(\d+\.\d+[\.\d]*)", runtime.read_text(), re.I)
            if m:
                log(f"[PYENV] Python version from {runtime.name}: {m.group(1)}")
                return m.group(1)

    pv = repo_dir / ".python-version"
    if pv.exists():
        v = pv.read_text().strip()
        if v:
            log(f"[PYENV] Python version from .python-version: {v}")
            return v

    for setup_file in [repo_dir / "setup.py", repo_dir / "setup.cfg"]:
        if setup_file.exists():
            m = re.search(r"python_requires\s*=\s*['\"]?>=\s*(\d+\.\d+)", setup_file.read_text())
            if m:
                log(f"[PYENV] Python version from {setup_file.name}: {m.group(1)}")
                return m.group(1)

    log(f"[PYENV] No version hint found — defaulting to {FALLBACK_PYTHON}")
    return FALLBACK_PYTHON


# ---------------------------------------------------------------------------
# pyenv version resolution  (replaces ensure_pyenv_version)
# ---------------------------------------------------------------------------

def ensure_pyenv_version(requested: str, log_file: Path | None = None) -> str | None:
    """
    Ensure *requested* Python version is installed via pyenv.
    Returns the resolved full version string, or None on failure.
    """
    pyenv = PYENV_ROOT / "bin" / "pyenv"

    def _versions() -> list[str]:
        r = subprocess.run([str(pyenv), "versions", "--bare"],
                           capture_output=True, text=True)
        return r.stdout.splitlines()

    if requested in _versions():
        log(f"[PYENV] Python {requested} already installed.")
        return requested

    # Resolve partial version (e.g. "3.8" → "3.8.18")
    result = subprocess.run([str(pyenv), "install", "--list"],
                            capture_output=True, text=True)
    candidates = [
        v.strip() for v in result.stdout.splitlines()
        if re.match(rf"^\s*{re.escape(requested)}\.\d+$", v)
        and not re.search(r"(dev|a|b|rc)", v)
    ]
    resolved = candidates[-1] if candidates else None

    if not resolved:
        log(f"[ERROR] [PYENV] No installable version matches: {requested}")
        return None

    if resolved in _versions():
        log(f"[PYENV] Python {resolved} already installed.")
        return resolved

    log(f"[PYENV] Installing Python {resolved} ...")
    extra: dict = {}
    if log_file:
        extra = {"stdout": open(log_file, "a"), "stderr": subprocess.STDOUT}
    r = subprocess.run([str(pyenv), "install", resolved], **extra)
    if log_file and "stdout" in extra:
        extra["stdout"].close()

    if r.returncode != 0:
        log(f"[ERROR] [PYENV] Failed to install Python {resolved}")
        return None

    log(f"[PYENV] Python {resolved} installed.")
    return resolved


# ---------------------------------------------------------------------------
# venv setup  (replaces setup_pyenv_env)
# ---------------------------------------------------------------------------

def setup_pyenv_env(
    repo_dir: Path,
    requirements_file: Path,
    setup_paths: str,
    repo_name: str,
    log_file: Path | None = None,
) -> EnvResult:
    """
    Create a fresh venv for *repo_dir*, install jupyter + requirements.
    Returns an EnvResult with ok=True and bin paths on success.
    """
    requested = detect_python_version(repo_dir)
    resolved = ensure_pyenv_version(requested, log_file)
    if not resolved:
        return EnvResult(
            ok=False,
            error_type="PYTHON_INSTALL_FAIL",
            error_message=f"Failed to install Python {requested} via pyenv",
        )

    python_bin = PYENV_ROOT / "versions" / resolved / "bin" / "python"
    if not python_bin.exists():
        return EnvResult(
            ok=False,
            error_type="PYTHON_BINARY_MISSING",
            error_message=f"Python binary missing: {python_bin}",
        )

    venv_dir = VENV_BASE_DIR / repo_name
    if venv_dir.exists():
        log(f"[PYENV] Removing existing venv at {venv_dir}")
        shutil.rmtree(venv_dir)

    VENV_BASE_DIR.mkdir(parents=True, exist_ok=True)
    log(f"[PYENV] Creating venv at {venv_dir} using Python {resolved}")

    def _run(*cmd, **kw):
        extra = {}
        if log_file:
            extra = {"stdout": open(log_file, "a"), "stderr": subprocess.STDOUT}
        r = subprocess.run(list(cmd), **extra, **kw)
        if log_file and "stdout" in extra:
            extra["stdout"].close()
        return r

    r = _run(str(python_bin), "-m", "venv", str(venv_dir))
    if r.returncode != 0:
        return EnvResult(
            ok=False,
            error_type="VENV_CREATE_FAIL",
            error_message=f"Failed to create venv using Python {resolved}",
        )

    pip = venv_dir / "bin" / "pip"
    python = venv_dir / "bin" / "python"

    # Upgrade pip + install jupyter
    _run(str(pip), "install", "--upgrade", "pip", "setuptools", "wheel")
    r = _run(str(pip), "install", "jupyter", "nbconvert")
    if r.returncode != 0:
        return EnvResult(
            ok=False,
            error_type="JUPYTER_INSTALL_FAIL",
            error_message="Failed to install jupyter/nbconvert into venv",
        )

    # Install requirements line-by-line (non-fatal per package)
    if requirements_file.exists():
        log(f"[PYENV] Installing requirements from {requirements_file} ...")
        for line in requirements_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            r = _run(str(pip), "install", "--no-cache-dir", line)
            if r.returncode == 0:
                log(f"[PYENV] ✓ {line}")
            else:
                log(f"[PYENV] ✗ {line} (skipping, non-fatal)")
    else:
        log(f"[PYENV] No requirements.txt at {requirements_file}")

    # Install setup.py packages
    if setup_paths:
        for setup_rel in setup_paths.split(";"):
            setup_rel = setup_rel.strip()
            if not setup_rel:
                continue
            setup_dir = repo_dir / Path(setup_rel).parent
            if (setup_dir / "setup.py").exists():
                log(f"[PYENV] Installing from {setup_dir}")
                _run(str(pip), "install", "--no-cache-dir", ".", cwd=str(setup_dir))
            else:
                log(f"[PYENV] No setup.py in {setup_dir}, skipping")

    py_ver = subprocess.run([str(python), "--version"],
                            capture_output=True, text=True).stdout.strip()
    log(f"[PYENV] Environment ready. Python: {py_ver}")

    return EnvResult(ok=True, venv_dir=venv_dir, python_bin=python, pip_bin=pip)


# ---------------------------------------------------------------------------
# Notebook execution  (replaces run_in_pyenv_env)
# ---------------------------------------------------------------------------

def run_in_pyenv_env(
    repo_dir: Path,
    notebook_paths: str,
    venv_dir: Path,
    repo_name: str,
    log_file: Path | None = None,
    exec_log: Path | None = None,
) -> EnvResult:
    """
    Execute all notebooks in *notebook_paths* (semicolon-separated) using
    the venv at *venv_dir*. Returns EnvResult with ok=True if at least one
    notebook executed successfully.
    """
    nbconvert = venv_dir / "bin" / "jupyter"
    if not nbconvert.exists():
        return EnvResult(
            ok=False,
            error_type="NO_NOTEBOOK_PATHS",
            error_message="jupyter not found in venv",
        )

    if not notebook_paths:
        return EnvResult(
            ok=False,
            error_type="NO_NOTEBOOK_PATHS",
            error_message="NOTEBOOK_PATHS was empty",
        )

    any_executed = False

    for nb_rel in notebook_paths.split(";"):
        nb_rel = nb_rel.strip()
        if not nb_rel:
            continue

        full_path = repo_dir / nb_rel
        if not full_path.exists():
            log(f"[PYENV] Notebook not found: {full_path}")
            _write_exec_log(exec_log, f"EXEC_FAIL|{repo_name}|{nb_rel}|0|NOTEBOOK_NOT_FOUND")
            continue

        nb_dir = full_path.parent
        base_name = full_path.stem
        output_nb = nb_dir / f"{base_name}_output.ipynb"

        log(f"[PYENV] Executing notebook: {nb_rel}")
        import time
        start = time.monotonic()

        lf_handle = open(log_file, "a") if log_file else subprocess.DEVNULL
        r = subprocess.run(
            [
                str(nbconvert), "nbconvert",
                "--to", "notebook",
                "--execute",
                "--allow-errors",
                "--ExecutePreprocessor.kernel_name=python3",
                full_path.name,
                "--output", f"{base_name}_output.ipynb",
            ],
            cwd=str(nb_dir),
            stdout=lf_handle,
            stderr=subprocess.STDOUT,
        )
        if log_file:
            lf_handle.close()

        duration = round(time.monotonic() - start, 2)

        if not output_nb.exists():
            log(f"[PYENV] Output notebook not created for {nb_rel} (exit {r.returncode})")
            _write_exec_log(exec_log, f"EXEC_FAIL|{repo_name}|{nb_rel}|{duration}")
            continue

        any_executed = True
        has_errors = '"output_type": "error"' in output_nb.read_text(encoding="utf-8", errors="replace")
        status = "SUCCESS_WITH_ERRORS" if has_errors else "SUCCESS"
        log(f"[PYENV] Notebook {status.lower().replace('_', ' ')}: {nb_rel}")
        _write_exec_log(exec_log, f"{status}|{repo_name}|{nb_rel}|{duration}")

    if not any_executed:
        return EnvResult(
            ok=False,
            error_type="NOTEBOOK_EXECUTION_ERROR",
            error_message="No notebooks were successfully executed",
        )

    return EnvResult(ok=True)


def _write_exec_log(exec_log: Path | None, line: str) -> None:
    if exec_log:
        exec_log.parent.mkdir(parents=True, exist_ok=True)
        with open(exec_log, "a") as f:
            f.write(line + "\n")
    print(line)


# ---------------------------------------------------------------------------
# Cleanup  (replaces cleanup_pyenv_env)
# ---------------------------------------------------------------------------

def cleanup_pyenv_env(venv_dir: Path | None) -> None:
    """Remove the repo venv to reclaim disk. Replaces bash cleanup_pyenv_env."""
    if venv_dir and venv_dir.exists():
        log(f"[PYENV] Cleaning up venv: {venv_dir}")
        shutil.rmtree(venv_dir)


# ---------------------------------------------------------------------------
# Error analysis  (replaces analyze_env_error)
# ---------------------------------------------------------------------------

_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (r"NoSuchKernel|No such kernel",        "KERNEL_NOT_FOUND",          "Jupyter kernel not found"),
    (r"nbconvert.*failed|Error executing",   "NOTEBOOK_EXECUTION_ERROR",  "Failed to execute notebook"),
    (r"ModuleNotFoundError",                 "MODULE_NOT_FOUND",          "Missing Python module"),
    (r"ImportError",                         "IMPORT_ERROR",              "Import error in notebook"),
    (r"SyntaxError",                         "SYNTAX_ERROR",              "Python syntax error in notebook"),
    (r"MemoryError",                         "MEMORY_ERROR",              "Out of memory error"),
    (r"TimeoutError|timeout",               "TIMEOUT_ERROR",             "Execution timed out"),
    (r"ConnectionError|URLError",           "NETWORK_ERROR",             "Network connection error"),
]


def analyze_env_error(log_file: Path) -> tuple[str, str]:
    """
    Scan *log_file* for known error patterns.
    Returns (error_type, error_message). Replaces bash analyze_env_error.
    """
    if not log_file.exists():
        return "ENV_RUN_FAIL", "Environment execution failed"

    text = log_file.read_text(errors="replace")
    for pattern, error_type, error_message in _ERROR_PATTERNS:
        if re.search(pattern, text, re.I):
            if error_type == "MODULE_NOT_FOUND":
                m = re.search(r"No module named ['\"]?([^'\" \n]+)", text)
                error_message = f"Missing Python module: {m.group(1) if m else 'unknown'}"
            elif error_type == "IMPORT_ERROR":
                m = re.search(r"ImportError.*", text)
                error_message = (m.group(0)[:200] if m else error_message)
            return error_type, error_message

    return "ENV_RUN_FAIL", "Environment execution failed"
