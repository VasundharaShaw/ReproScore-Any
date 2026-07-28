"""
pipeline/import_execution.py — bridge execution-pipeline outputs into ReproScore-Any.

Reads the execution results produced by Reproducibility_pipeline_updated
(its output/db/db.sqlite) and copies

    repository_runs
    notebook_executions
    notebook_reproducibility_metrics
    notebooks

into ReproScore-Any's DB, re-keying every foreign key from the SOURCE
`repositories` id-space into THIS DB's `repo_targets` id-space. Repos are
matched on the `repository` (owner/repo) path; a repo_targets row is created
if none exists yet.

The two id-spaces are independent, so rows cannot be copied verbatim — this
module rebuilds notebook / run / execution id maps per repo and rewrites
repository_id, repository_run_id, notebook_id and notebook_execution_id.

Import is idempotent per repo: existing execution rows for a matched repo are
deleted before the copy, so a re-import never leaves stale evidence behind.

Order of operations for a full ROS/RCS run:
    1. run the execution pipeline  -> writes its output/db/db.sqlite
    2. python3 pipeline/import_execution.py --exec-db <that db>   (this module)
    3. python3 pipeline/score.py ...  -> RRS + score_ros_rcs() consume evidence

Usage:
    python3 pipeline/import_execution.py --exec-db /path/to/exec/output/db/db.sqlite
    python3 pipeline/import_execution.py --exec-db <src> --db output/db/db.sqlite
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.config import DB_FILE as _DEFAULT_DB
from pipeline.db import ensure_pipeline_tables


def _norm(repo: str) -> str:
    """Normalise a repository reference to bare owner/repo form."""
    r = (repo or "").strip()
    for p in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if r.startswith(p):
            r = r[len(p):]
            break
    if r.endswith(".git"):
        r = r[:-4]
    return r.strip("/")


def _match_or_create_repo(con: sqlite3.Connection, repo_path: str) -> int:
    """Return repo_targets.id for repo_path, creating a minimal row if absent."""
    row = con.execute(
        "SELECT id FROM repo_targets WHERE repository = ? LIMIT 1", (repo_path,)
    ).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        """INSERT INTO repo_targets
               (repository, notebooks, setups, requirements,
                notebooks_count, setups_count, requirements_count)
           VALUES (?, '', '', '', 0, 0, 0)""",
        (repo_path,),
    )
    return cur.lastrowid


def _clear_execution_rows(con: sqlite3.Connection, tgt_rid: int) -> None:
    """Remove any existing execution evidence for this repo (clean re-import)."""
    con.execute("DELETE FROM notebook_reproducibility_metrics WHERE repository_id = ?", (tgt_rid,))
    con.execute("DELETE FROM notebook_executions             WHERE repository_id = ?", (tgt_rid,))
    con.execute("DELETE FROM repository_runs                 WHERE repository_id = ?", (tgt_rid,))
    con.execute("DELETE FROM notebooks                       WHERE repository_id = ?", (tgt_rid,))


def _import_repo(con: sqlite3.Connection, src_rid: int, tgt_rid: int) -> dict:
    """Copy all execution evidence for one source repo into the target repo_id."""
    _clear_execution_rows(con, tgt_rid)

    nb_map: dict[int, int] = {}
    run_map: dict[int, int] = {}
    exec_map: dict[int, int] = {}

    for r in con.execute(
        "SELECT id, name, language FROM ex.notebooks WHERE repository_id = ?", (src_rid,)
    ).fetchall():
        cur = con.execute(
            "INSERT INTO notebooks (repository_id, name, language) VALUES (?, ?, ?)",
            (tgt_rid, r[1], r[2]),
        )
        nb_map[r[0]] = cur.lastrowid

    def _ensure_nb(src_nb_id, name):
        if src_nb_id in nb_map:
            return nb_map[src_nb_id]
        cur = con.execute(
            "INSERT INTO notebooks (repository_id, name, language) VALUES (?, ?, ?)",
            (tgt_rid, name, None),
        )
        nb_map[src_nb_id] = cur.lastrowid
        return cur.lastrowid

    for r in con.execute(
        """SELECT id, url, run_status, error_message, started_at, finished_at,
                  duration_seconds
           FROM ex.repository_runs WHERE repository_id = ?""",
        (src_rid,),
    ).fetchall():
        cur = con.execute(
            """INSERT INTO repository_runs
                   (repository_id, url, run_status, error_message,
                    started_at, finished_at, duration_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tgt_rid, r[1], r[2], r[3], r[4], r[5], r[6]),
        )
        run_map[r[0]] = cur.lastrowid

    for r in con.execute(
        """SELECT id, repository_run_id, notebook_id, notebook_name, url,
                  execution_status, execution_duration, total_code_cells,
                  executed_cells, error_type, error_category, error_message,
                  error_cell_index, error_count
           FROM ex.notebook_executions WHERE repository_id = ?""",
        (src_rid,),
    ).fetchall():
        run_id = run_map.get(r[1])
        if run_id is None:
            continue
        nb_id = _ensure_nb(r[2], r[3])
        cur = con.execute(
            """INSERT INTO notebook_executions
                   (repository_run_id, repository_id, notebook_id, notebook_name,
                    url, execution_status, execution_duration, total_code_cells,
                    executed_cells, error_type, error_category, error_message,
                    error_cell_index, error_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, tgt_rid, nb_id, r[3], r[4], r[5], r[6], r[7],
             r[8], r[9], r[10], r[11], r[12], r[13]),
        )
        exec_map[r[0]] = cur.lastrowid

    for r in con.execute(
        """SELECT id, repository_run_id, notebook_execution_id, notebook_id,
                  total_code_cells, identical_cells_count, different_cells_count,
                  nondeterministic_cells_count, identical_cells, different_cells,
                  nondeterministic_cells, reproducibility_score
           FROM ex.notebook_reproducibility_metrics WHERE repository_id = ?""",
        (src_rid,),
    ).fetchall():
        run_id = run_map.get(r[1])
        exec_id = exec_map.get(r[2])
        if run_id is None or exec_id is None:
            continue
        nb_id = _ensure_nb(r[3], None)
        con.execute(
            """INSERT INTO notebook_reproducibility_metrics
                   (repository_run_id, notebook_execution_id, repository_id,
                    notebook_id, total_code_cells, identical_cells_count,
                    different_cells_count, nondeterministic_cells_count,
                    identical_cells, different_cells, nondeterministic_cells,
                    reproducibility_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, exec_id, tgt_rid, nb_id, r[4], r[5], r[6], r[7],
             r[8], r[9], r[10], r[11]),
        )

    return {
        "notebooks": len(nb_map),
        "runs": len(run_map),
        "executions": len(exec_map),
    }


def import_execution(exec_db: str, db: str) -> None:
    if not Path(exec_db).is_file():
        print(f"[IMPORT] ERROR: execution DB not found: {exec_db}", file=sys.stderr)
        sys.exit(1)

    ensure_pipeline_tables(db)

    con = sqlite3.connect(db)
    con.execute("ATTACH DATABASE ? AS ex", (exec_db,))
    con.execute("PRAGMA foreign_keys = ON")

    src_repos = con.execute("SELECT id, repository FROM ex.repositories").fetchall()
    if not src_repos:
        print("[IMPORT] no repositories in execution DB — nothing to import")
        con.close()
        return

    total = 0
    for src_rid, src_repo in src_repos:
        repo_path = _norm(src_repo)
        tgt_rid = _match_or_create_repo(con, repo_path)
        counts = _import_repo(con, src_rid, tgt_rid)
        con.commit()
        total += counts["executions"]
        print(
            f"[IMPORT] {repo_path}: src_id={src_rid} -> repo_targets.id={tgt_rid}  "
            f"notebooks={counts['notebooks']} runs={counts['runs']} "
            f"executions={counts['executions']}"
        )

    con.close()
    print(f"[IMPORT] done — {len(src_repos)} repo(s), {total} execution row(s) imported")
    print("[IMPORT] next: run pipeline/score.py per repo to populate ROS/RCS")


def main():
    ap = argparse.ArgumentParser(description="Import execution evidence into ReproScore-Any.")
    ap.add_argument("--exec-db", required=True,
                    help="Path to the execution pipeline output/db/db.sqlite")
    ap.add_argument("--db", default=str(_DEFAULT_DB),
                    help="Target ReproScore-Any DB (default: config DB_FILE)")
    args = ap.parse_args()
    import_execution(args.exec_db, args.db)


if __name__ == "__main__":
    main()
