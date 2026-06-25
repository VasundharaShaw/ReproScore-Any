"""
pipeline/notebooks.py — Notebook discovery and output comparison.

Replaces src/notebooks.sh (discover_notebooks, compare_notebook_outputs,
compare_notebook_outputs_json).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pipeline.db import get_notebook_id_from_db, get_repo_id_from_db
from pipeline.logger import log


def discover_notebooks(repo_dir: Path, repo_id: int, db_file: Path) -> str:
    """
    Find all .ipynb files in *repo_dir* (excluding checkpoints and _output files).
    Insert records into the notebooks table. Update notebooks_count in repo_targets.
    Return the semicolon-separated notebook paths (relative to repo_dir).
    """
    import sqlite3

    log(f"[NOTEBOOK] Discovering notebooks in {repo_dir} ...")

    notebooks = sorted(
        p for p in repo_dir.rglob("*.ipynb")
        if ".ipynb_checkpoints" not in p.parts
        and not p.stem.endswith("_output")
    )

    if not notebooks:
        log(f"[NOTEBOOK] No notebooks found in {repo_dir}.")
        return ""

    relative = [str(p.relative_to(repo_dir)) for p in notebooks]
    notebook_paths = ";".join(relative)
    count = len(relative)
    log(f"[NOTEBOOK] Found {count} notebook(s): {notebook_paths}")

    con = sqlite3.connect(db_file)
    try:
        con.execute(
            "UPDATE repo_targets SET notebooks = ?, notebooks_count = ? WHERE id = ?",
            (notebook_paths, count, repo_id),
        )
        for nb_rel in relative:
            con.execute(
                "INSERT OR IGNORE INTO notebooks (repository_id, name, language) VALUES (?, ?, 'python')",
                (repo_id, nb_rel),
            )
        con.commit()
    finally:
        con.close()

    log(f"[NOTEBOOK] Inserted {count} notebook record(s) into DB.")
    return notebook_paths


def compare_notebook_outputs(
    repo_dir: Path,
    repo_id: int,
    github_url: str,
    notebook_paths: str,
    comp_dir: Path,
    db_file: Path,
    project_root: Path,
    run_id: int,
    log_dir: Path,
    log_file: Path | None = None,
) -> None:
    """
    Compare original vs executed notebooks for all notebooks in *notebook_paths*.
    Writes JSON comparison files to *comp_dir* and calls compare_notebook.py.
    """
    log(f"[NOTEBOOK] Comparing outputs for: {repo_dir.name}")
    comp_dir.mkdir(parents=True, exist_ok=True)

    compare_script = project_root / "analysis" / "compare_notebook.py"
    nb_count = len([n for n in notebook_paths.split(";") if n.strip()])

    for nb_rel in notebook_paths.split(";"):
        nb_rel = nb_rel.strip()
        if not nb_rel:
            continue

        original = repo_dir / nb_rel
        if not original.exists():
            log(f"[NOTEBOOK] Not found: {original} — skipping")
            continue

        nb_path = Path(nb_rel)
        base_name = nb_path.stem
        executed = repo_dir / nb_path.parent / f"{base_name}_output.ipynb"
        comparison_file = comp_dir / f"{base_name}_comparison.json"

        notebook_id = get_notebook_id_from_db(db_file, nb_rel)
        resolved_repo_id = get_repo_id_from_db(db_file, github_url) or repo_id

        log(f"[NOTEBOOK] ID={notebook_id}  REPO_ID={resolved_repo_id}  path={nb_rel}")

        if not notebook_id:
            log(f"[NOTEBOOK] No DB record for {nb_rel} — skipping")
            continue

        if not executed.exists():
            log(f"[NOTEBOOK] Output missing for {nb_rel} — recording failure")
            comparison_file.write_text(json.dumps({
                "notebook": nb_rel,
                "NOTEBOOK_ID": notebook_id,
                "REPO_ID": resolved_repo_id,
                "status": "failed",
                "reason": "output_notebook_not_created",
            }, indent=2))
            continue

        _run_comparison(
            compare_script=compare_script,
            original=original,
            executed=executed,
            nb_rel=nb_rel,
            repo_id=resolved_repo_id,
            comparison_file=comparison_file,
            db_file=db_file,
            run_id=run_id,
            github_url=github_url,
            nb_count=nb_count,
            log_dir=log_dir,
            log_file=log_file,
        )


def _run_comparison(
    compare_script: Path,
    original: Path,
    executed: Path,
    nb_rel: str,
    repo_id: int,
    comparison_file: Path,
    db_file: Path,
    run_id: int,
    github_url: str,
    nb_count: int,
    log_dir: Path,
    log_file: Path | None,
) -> None:
    """Call analysis/compare_notebook.py with all env vars summary.py requires."""
    env = {
        **os.environ,
        "OUTPUT_DB_FILE": str(db_file),
        "RUN_ID": str(run_id),
        "GITHUB_REPO": github_url,
        "NOTEBOOKS_COUNT": str(nb_count),
        "LOG_DIR": str(log_dir),
    }

    cmd = [
        sys.executable, str(compare_script),
        str(original), str(executed),
        nb_rel, str(repo_id),
        "--json", str(comparison_file),
    ]

    lf_handle = open(log_file, "a") if log_file else subprocess.DEVNULL
    r = subprocess.run(cmd, stdout=lf_handle, stderr=subprocess.STDOUT, env=env)
    if log_file:
        lf_handle.close()

    if r.returncode != 0:
        log(f"[NOTEBOOK] compare_notebook.py failed for {nb_rel} (exit {r.returncode})")
    else:
        log(f"[NOTEBOOK] Comparison complete for {nb_rel}")
