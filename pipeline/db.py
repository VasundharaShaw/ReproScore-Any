"""
pipeline/db.py — All SQLite interactions for the pipeline.

Replaces src/db.sh (ensure_pipeline_tables, get_or_create_repo_id,
get_notebook_id_from_db, get_repo_id_from_db, column_exists).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline.logger import log


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repo_targets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    repository          TEXT,
    notebooks           TEXT,
    setups              TEXT,
    requirements        TEXT,
    notebooks_count     INTEGER,
    setups_count        INTEGER,
    requirements_count  INTEGER,
    host_type           TEXT,
    rrs                 REAL,
    score_E             REAL,
    score_A             REAL,
    score_D             REAL,
    score_C             REAL,
    score_S             REAL,
    paper_doi           TEXT,
    ros                 REAL,
    rcs                 REAL
);

CREATE TABLE IF NOT EXISTS notebooks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id   INTEGER,
    name            TEXT,
    language        TEXT,
    code_cells      INTEGER,
    FOREIGN KEY (repository_id) REFERENCES repo_targets(id)
);

CREATE TABLE IF NOT EXISTS repository_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id    INTEGER NOT NULL,
    url              TEXT,
    run_status       TEXT NOT NULL,
    error_message    TEXT,
    started_at       TEXT,
    finished_at      TEXT,
    duration_seconds FLOAT,
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (repository_id) REFERENCES repo_targets(id)
);

CREATE TABLE IF NOT EXISTS notebook_executions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_run_id    INTEGER NOT NULL,
    repository_id        INTEGER NOT NULL,
    notebook_id          INTEGER NOT NULL,
    notebook_name        TEXT,
    url                  TEXT,
    execution_status     TEXT,
    execution_duration   FLOAT,
    total_code_cells     INTEGER,
    executed_cells       INTEGER,
    error_type           TEXT,
    error_category       TEXT,
    error_message        TEXT,
    error_cell_index     INTEGER,
    error_count          INTEGER,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repository_run_id, notebook_id),
    FOREIGN KEY (repository_run_id) REFERENCES repository_runs(id),
    FOREIGN KEY (repository_id)     REFERENCES repo_targets(id),
    FOREIGN KEY (notebook_id)       REFERENCES notebooks(id)
);

CREATE TABLE IF NOT EXISTS notebook_reproducibility_metrics (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_run_id            INTEGER NOT NULL,
    notebook_execution_id        INTEGER NOT NULL,
    repository_id                INTEGER NOT NULL,
    notebook_id                  INTEGER NOT NULL,
    total_code_cells             INTEGER,
    identical_cells_count        INTEGER,
    different_cells_count        INTEGER,
    nondeterministic_cells_count INTEGER,
    identical_cells              TEXT,
    different_cells              TEXT,
    nondeterministic_cells       TEXT,
    reproducibility_score        REAL,
    created_at                   TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repository_run_id, notebook_id),
    FOREIGN KEY (repository_run_id)     REFERENCES repository_runs(id),
    FOREIGN KEY (notebook_execution_id) REFERENCES notebook_executions(id),
    FOREIGN KEY (repository_id)         REFERENCES repo_targets(id),
    FOREIGN KEY (notebook_id)           REFERENCES notebooks(id)
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def connect(db_file: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------

def ensure_pipeline_tables(db_file: str | Path) -> None:
    """Create all execution-side tables if they don't exist. Replaces bash ensure_pipeline_tables."""
    Path(db_file).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_file) as con:
        con.executescript(_SCHEMA)
    log(f"[DB] Pipeline tables ready in {db_file}")


def column_exists(db_file: str | Path, table: str, column: str) -> bool:
    """Return True if *column* exists in *table*. Replaces bash column_exists."""
    with connect(db_file) as con:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


# ---------------------------------------------------------------------------
# repo_targets helpers
# ---------------------------------------------------------------------------

def get_or_create_repo_id(
    db_file: str | Path,
    github_url: str,
    notebook_paths: str = "",
    setup_paths: str = "",
    requirement_paths: str = "",
) -> int:
    """
    Look up or insert a record in repo_targets. Return the row id.
    Replaces bash get_or_create_repo_id().
    """
    repo_path = github_url.removeprefix("https://github.com/")
    with connect(db_file) as con:
        row = con.execute(
            "SELECT id FROM repo_targets WHERE repository = ? LIMIT 1",
            (repo_path,),
        ).fetchone()
        if row:
            return row["id"]
        cur = con.execute(
            """
            INSERT INTO repo_targets
                (repository, notebooks, setups, requirements,
                 notebooks_count, setups_count, requirements_count)
            VALUES (?, ?, ?, ?, 0, 0, 0)
            """,
            (repo_path, notebook_paths, setup_paths, requirement_paths),
        )
        return cur.lastrowid


def get_repo_id_from_db(db_file: str | Path, github_url: str) -> int | None:
    """Return repo_targets.id for a GitHub URL, or None. Replaces bash get_repo_id_from_db."""
    repo_path = github_url.removeprefix("https://github.com/").removesuffix(".git")
    with connect(db_file) as con:
        row = con.execute(
            "SELECT id FROM repo_targets WHERE repository = ? LIMIT 1",
            (repo_path,),
        ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# notebooks helpers
# ---------------------------------------------------------------------------

def get_notebook_id_from_db(db_file: str | Path, name: str) -> int | None:
    """Return notebooks.id for a notebook name, or None. Replaces bash get_notebook_id_from_db."""
    with connect(db_file) as con:
        row = con.execute(
            "SELECT id FROM notebooks WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
    return row["id"] if row else None


def get_notebook_language_stats(db_file: str | Path, repo_id: int) -> tuple[int, int]:
    """Return (total_notebooks, python_notebooks) for a repo. Replaces bash get_notebook_language_stats."""
    with connect(db_file) as con:
        row = con.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN LOWER(language) = 'python' THEN 1 ELSE 0 END)
            FROM notebooks WHERE repository_id = ?
            """,
            (repo_id,),
        ).fetchone()
    total = row[0] or 0
    python = row[1] or 0
    return total, python


# ---------------------------------------------------------------------------
# repository_runs helpers
# ---------------------------------------------------------------------------

def create_repository_run(db_file: str | Path, repo_id: int, url: str) -> int:
    """
    Insert a new RUNNING record into repository_runs. Return the run id.
    Replaces bash create_repository_run().
    """
    with connect(db_file) as con:
        cur = con.execute(
            """
            INSERT INTO repository_runs (repository_id, url, run_status, started_at)
            VALUES (?, ?, 'RUNNING', datetime('now'))
            """,
            (repo_id, url),
        )
        return cur.lastrowid


def finalize_repository_run(
    db_file: str | Path,
    run_id: int,
    status: str,
    error_message: str,
    duration_seconds: float,
) -> None:
    """
    Update a run record with final status and duration.
    Replaces bash finalize_repository_run().
    """
    log(f"[REPO] Finalizing run {run_id} — status: {status}")
    with connect(db_file) as con:
        con.execute(
            """
            UPDATE repository_runs
            SET run_status = ?,
                error_message = ?,
                finished_at = datetime('now'),
                duration_seconds = ?
            WHERE id = ?
            """,
            (status, error_message, duration_seconds, run_id),
        )


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_to_csv(db_file: str | Path, csv_dir: Path) -> None:
    """Export all execution-side tables to CSV files in *csv_dir*."""
    import csv

    csv_dir.mkdir(parents=True, exist_ok=True)
    tables = [
        "repo_targets",
        "repository_runs",
        "notebooks",
        "notebook_executions",
        "notebook_reproducibility_metrics",
    ]
    with connect(db_file) as con:
        for table in tables:
            try:
                rows = con.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    log(f"[CSV] {table}: empty, skipping")
                    continue
                out = csv_dir / f"{table}.csv"
                with open(out, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows([dict(r) for r in rows])
                log(f"[CSV] {table}: {len(rows)} rows → {out}")
            except Exception as e:
                log(f"[CSV] {table}: ERROR - {e}")
