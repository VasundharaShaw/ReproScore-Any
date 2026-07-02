"""
pipeline/runner.py — Per-repository orchestration and batch processing.

Replaces src/repo.sh (process_repo, process_sqlite_flow,
create_repository_run, finalize_repository_run, get_or_create_repo_id)
and the orchestration logic in pipeline/main.sh.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from pipeline.checks import validate_repo
from pipeline.db import (
    create_repository_run,
    ensure_pipeline_tables,
    export_to_csv,
    finalize_repository_run,
    get_or_create_repo_id,
)
from pipeline.env import (
    EnvResult,
    analyze_env_error,
    cleanup_pyenv_env,
    run_in_pyenv_env,
    setup_pyenv_env,
)
from pipeline.logger import configure as configure_logger
from pipeline.logger import elapsed_sec, log, now_sec
from pipeline.notebooks import compare_notebook_outputs, discover_notebooks
from pipeline.requirements import process_requirements


# ---------------------------------------------------------------------------
# Paths — resolved from PROJECT_ROOT in config
# ---------------------------------------------------------------------------

def _get_paths() -> dict:
    from config.config import DB_FILE, PROJECT_ROOT
    root = Path(PROJECT_ROOT)
    output = root / "output"
    return {
        "project_root": root,
        "db_file": Path(DB_FILE),
        "repos_dir": output / "cloned_repos",
        "comp_dir": output / "comparisons",
        "log_dir": output / "logs",
        "csv_dir": output / "csv",
    }


def initialize_directories(paths: dict) -> None:
    for key in ("repos_dir", "comp_dir", "log_dir", "csv_dir"):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["db_file"].parent.mkdir(parents=True, exist_ok=True)
    log("[INIT] Initialized directory structure")


# ---------------------------------------------------------------------------
# process_repo()  — full per-repo flow
# Mirrors bash process_repo() in src/repo.sh
# ---------------------------------------------------------------------------

def process_repo(
    github_url: str,
    paths: dict,
    repo_id: int,
) -> bool:
    """
    Full per-repo flow:
      validate → clone/pull → discover → score → requirements
      → setup env → execute → compare → finalize

    Returns True if the repo executed successfully.
    """
    db_file = paths["db_file"]
    repos_dir = paths["repos_dir"]
    log_dir = paths["log_dir"]
    comp_dir = paths["comp_dir"]
    project_root = paths["project_root"]

    start = now_sec()
    repo_name = Path(github_url.rstrip("/")).stem.removesuffix(".git")
    repo_dir = repos_dir / repo_name
    log_file = log_dir / f"{repo_name}.log"
    log_file.write_text("")  # reset per run

    configure_logger(log_file=log_file)
    exec_log = log_dir / "notebook_execution_times.log"

    log(f"[REPO] ── Starting: {repo_name} ──────────────────────────────")

    run_id = create_repository_run(db_file, repo_id, github_url)

    # 1. Validate
    if not validate_repo(github_url):
        finalize_repository_run(db_file, run_id, "INVALID_REPOSITORY_URL",
                                "git ls-remote failed", elapsed_sec(start))
        return False

    # 2. Clone or pull
    if repo_dir.exists():
        log(f"[REPO] Repo already exists, pulling latest ...")
        subprocess.run(["git", "pull"], cwd=str(repo_dir),
                       stdout=open(log_file, "a"), stderr=subprocess.STDOUT)
    else:
        log(f"[REPO] Cloning into {repo_dir} ...")
        subprocess.run(
            ["git", "clone", "--depth", "1", github_url, str(repo_dir)],
            stdout=open(log_file, "a"), stderr=subprocess.STDOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

    if not repo_dir.is_dir():
        finalize_repository_run(db_file, run_id, "REPO_DIR_MISSING",
                                "Directory not found after clone", elapsed_sec(start))
        return False

    # 3. Discover notebooks
    notebook_paths = discover_notebooks(repo_dir, repo_id, db_file)
    if not notebook_paths:
        finalize_repository_run(db_file, run_id, "NO_NOTEBOOKS",
                                "No notebooks found in repo", elapsed_sec(start))
        return False

    # 4. Score (RRS)
    score_script = project_root / "pipeline" / "score.py"
    subprocess.run(
        [sys.executable, str(score_script),
         "--repo-dir", str(repo_dir),
         "--repo-id", str(repo_id),
         "--db", str(db_file)],
        stdout=open(log_file, "a"), stderr=subprocess.STDOUT,
    )
    log("[REPO] Scoring complete.")

    nb_count = len(notebook_paths.split(";"))
    log(f"[REPO] Notebooks found: {nb_count}")

    # 5. Process requirements
    requirement_paths = ""  # populated from DB if available
    requirements_file = process_requirements(repo_dir, notebook_paths, requirement_paths)

    # 6. Set up Python environment
    env_result: EnvResult = setup_pyenv_env(
        repo_dir=repo_dir,
        requirements_file=requirements_file,
        setup_paths="",
        repo_name=repo_name,
        log_file=log_file,
    )
    if not env_result.ok:
        finalize_repository_run(db_file, run_id,
                                env_result.error_type, env_result.error_message,
                                elapsed_sec(start))
        cleanup_pyenv_env(env_result.venv_dir)
        return False

    # 7. Run notebooks
    run_result: EnvResult = run_in_pyenv_env(
        repo_dir=repo_dir,
        notebook_paths=notebook_paths,
        venv_dir=env_result.venv_dir,
        repo_name=repo_name,
        log_file=log_file,
        exec_log=exec_log,
    )
    if not run_result.ok:
        error_type, error_message = analyze_env_error(log_file)
        finalize_repository_run(db_file, run_id, error_type, error_message, elapsed_sec(start))
        cleanup_pyenv_env(env_result.venv_dir)
        return False

    # 8. Compare outputs
    compare_notebook_outputs(
        repo_dir=repo_dir,
        repo_id=repo_id,
        github_url=github_url,
        notebook_paths=notebook_paths,
        comp_dir=comp_dir,
        db_file=db_file,
        project_root=project_root,
        run_id=run_id,
        log_dir=log_dir,
        log_file=log_file,
    )
    cleanup_pyenv_env(env_result.venv_dir)

    total = elapsed_sec(start)
    finalize_repository_run(db_file, run_id, "SUCCESS",
                            "Repository executed successfully", total)
    log(f"[REPO] ── Done: {repo_name} ({total}s) ──────────────────────")
    return True


# ---------------------------------------------------------------------------
# process_sqlite_flow()  — batch mode
# Mirrors bash process_sqlite_flow() in src/repo.sh
# ---------------------------------------------------------------------------

def process_sqlite_flow(target_count: int, paths: dict) -> None:
    """
    Read unprocessed repos from the repositories table and process up to
    *target_count* of them. Skips repos already in repository_runs.
    """
    db_file = paths["db_file"]
    repos_dir = paths["repos_dir"]

    processed_ids: list[int] = []
    processed_count = 0

    log(f"[BATCH] Processing next {target_count} unprocessed repositories.")

    while processed_count < target_count:
        not_in = ""
        if processed_ids:
            not_in = f"AND r.id NOT IN ({','.join(map(str, processed_ids))})"

        con = sqlite3.connect(db_file)
        try:
            row = con.execute(f"""
                SELECT r.id, r.repository
                FROM repositories r
                WHERE (r.host_type = 'github' OR r.host_type IS NULL)
                {not_in}
                ORDER BY r.id
                LIMIT 1
            """).fetchone()
        finally:
            con.close()

        if not row:
            log("[BATCH] No more repositories.")
            break

        input_repo_id, repo_path = row
        repo_path = repo_path.strip().strip('"')

        github_url = repo_path if repo_path.startswith("http") else f"https://github.com/{repo_path}"
        log(f"[BATCH] Repo {input_repo_id}: {github_url}")

        repo_id = get_or_create_repo_id(db_file, github_url)
        if not repo_id:
            log("[BATCH] Could not get repo ID, skipping.")
            processed_ids.append(input_repo_id)
            continue

        con = sqlite3.connect(db_file)
        try:
            already_run = con.execute(
                "SELECT COUNT(*) FROM repository_runs WHERE repository_id = ?",
                (repo_id,),
            ).fetchone()[0]
        finally:
            con.close()

        if already_run > 0:
            log(f"[BATCH] Repo {repo_id} already processed, skipping.")
            processed_ids.append(input_repo_id)
            continue

        processed_ids.append(input_repo_id)
        success = process_repo(github_url, paths, repo_id)

        repo_name = Path(repo_path).name
        if not success:
            clone_dir = repos_dir / repo_name
            if clone_dir.exists():
                import shutil
                shutil.rmtree(clone_dir)

        if success:
            processed_count += 1

    log(f"[BATCH] Finished. Processed {processed_count} repositories.")


# ---------------------------------------------------------------------------
# print_run_summary()  — mirrors bash print_run_summary in pipeline/main.sh
# ---------------------------------------------------------------------------

def print_run_summary(start: float, paths: dict) -> None:
    db_file = paths["db_file"]
    elapsed = round(elapsed_sec(start))

    con = sqlite3.connect(db_file)
    try:
        total   = con.execute("SELECT COUNT(*) FROM repository_runs").fetchone()[0]
        success = con.execute("SELECT COUNT(*) FROM repository_runs WHERE run_status = 'SUCCESS'").fetchone()[0]
        failed  = total - success
    finally:
        con.close()

    print()
    print("════════════════════════════════════════")
    print("        PIPELINE RUN SUMMARY            ")
    print("════════════════════════════════════════")
    print(f"  Total runs in DB  : {total}")
    print(f"  Successful        : {success}")
    print(f"  Failed/Skipped    : {failed}")
    print(f"  Elapsed time      : {elapsed}s")
    print(f"  Results stored in : {db_file}")
    print(f"  Logs directory    : {paths['log_dir']}")
    print("════════════════════════════════════════")
    print()


# ---------------------------------------------------------------------------
# run()  — public entry point called from main.py cmd_run
# ---------------------------------------------------------------------------

def run(target_count: int = 1, interactive: bool = False) -> int:
    """
    Main entry point for the pipeline run step.
    *interactive* mirrors the old option-1 (manual URL) mode.
    """
    paths = _get_paths()
    initialize_directories(paths)
    ensure_pipeline_tables(paths["db_file"])

    log("[MAIN] Starting pipeline ...")
    log(f"[MAIN] PROJECT_ROOT : {paths['project_root']}")
    log(f"[MAIN] DB           : {paths['db_file']}")
    log(f"[MAIN] Repos dir    : {paths['repos_dir']}")
    log(f"[MAIN] Logs dir     : {paths['log_dir']}")

    start = now_sec()

    if interactive:
        github_url      = input("Enter GitHub repo URL: ").strip()
        notebook_paths  = input("Enter notebook paths (semicolon-separated): ").strip()
        setup_paths     = input("Enter setup paths (optional, semicolon-separated): ").strip()
        requirement_paths = input("Enter requirements paths (optional, semicolon-separated): ").strip()

        repo_id = get_or_create_repo_id(
            paths["db_file"], github_url,
            notebook_paths, setup_paths, requirement_paths,
        )
        process_repo(github_url, paths, repo_id)
    else:
        process_sqlite_flow(target_count, paths)

    print_run_summary(start, paths)
    export_to_csv(paths["db_file"], paths["csv_dir"])
    return 0
