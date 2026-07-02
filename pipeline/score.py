"""
pipeline/score.py — Reproducibility Readiness Score (RRS) scoring.

Usage:
    python3 pipeline/score.py --repo-dir <path> --repo-id <int> --db <path>

Scores a cloned repository using the 26-sub-metric RRS framework (ReproScore)
and writes results to the repo_targets table in the single output DB.

Columns written (all REAL, 0–100):
    rrs      — overall Reproducibility Readiness Score
    score_E  — Environment specification
    score_A  — Data accessibility
    score_D  — Documentation
    score_C  — Code portability
    score_S  — Reproducibility signals
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.config import DB_FILE as _DEFAULT_DB
from reproscore.src.scoring.rrs import RRSScorer
from reproscore.src.scoring.ros import ROSScorer, ExecutionEvidence
from reproscore.src.scoring.rcs import RCSScorer


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def write_scores(db_path: str, repo_id: int, result) -> None:
    """Write RRS category and overall scores to repo_targets table."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """UPDATE repo_targets
           SET rrs=?, score_E=?, score_A=?, score_D=?, score_C=?, score_S=?
           WHERE id=?""",
        (
            result.rrs,
            result.category_scores["E"].raw_score,
            result.category_scores["A"].raw_score,
            result.category_scores["D"].raw_score,
            result.category_scores["C"].raw_score,
            result.category_scores["S"].raw_score,
            repo_id,
        ),
    )
    conn.commit()
    conn.close()


def build_evidence(conn, repo_id):
    """Build ExecutionEvidence from notebook_executions for this repo."""
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT execution_status, total_code_cells, executed_cells, error_category
        FROM notebook_executions WHERE repository_id = ?
        """,
        (repo_id,)
    ).fetchall()

    if not rows:
        return ExecutionEvidence()

    total = len(rows)
    install_success = total > 0
    execution_success = all(r[0] == "SUCCESS" for r in rows)
    fully_executed = sum(1 for r in rows if r[1] and r[2] and r[1] == r[2] and r[1] > 0)
    notebook_exec_rate = fully_executed / total
    dependency_errors = sum(1 for r in rows if r[3] == "DEPENDENCY_ERROR")
    import_success_rate = (total - dependency_errors) / total

    scores = cur.execute(
        """
        SELECT reproducibility_score FROM notebook_reproducibility_metrics
        WHERE repository_id = ? AND reproducibility_score IS NOT NULL
        """,
        (repo_id,)
    ).fetchall()

    output_determinism = None
    if scores:
        output_determinism = sum(r[0] for r in scores) / len(scores) * 100.0

    return ExecutionEvidence(
        install_success=install_success,
        execution_success=execution_success,
        output_determinism=output_determinism,
        notebook_exec_rate=notebook_exec_rate,
        import_success_rate=import_success_rate,
    )


def write_ros_rcs(db_path: str, repo_id: int, ros, rcs) -> None:
    """Write ROS and RCS scores to repo_targets table."""
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE repo_targets SET ros=?, rcs=? WHERE id=?", (ros, rcs, repo_id))
    conn.commit()
    conn.close()


def score_ros_rcs(db_path: str, repo_id: int, rrs: float) -> None:
    """Compute and write ROS and RCS for a repo using execution evidence."""
    conn = sqlite3.connect(db_path)
    ev = build_evidence(conn, repo_id)
    conn.close()

    ros_result = ROSScorer().score(ev)
    rcs_result = RCSScorer().score(rrs, ros_result.ros, ros_result.coverage_weight_sum)

    print(f"[ROS] ROS={ros_result.ros}  RCS={rcs_result.rcs}")
    print(f"[ROS] Components: {ros_result.component_scores}")
    print(f"[ROS] Coverage: {rcs_result.coverage_level}  alpha={rcs_result.alpha}")

    write_ros_rcs(db_path, repo_id, ros_result.ros, rcs_result.rcs)
    print(f"[ROS] Written to DB (repo_id={repo_id})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RRS: score a cloned repository.")
    parser.add_argument("--repo-dir", required=True, help="Path to cloned repo root")
    parser.add_argument("--repo-id", required=True, type=int,
                        help="Row ID in repo_targets table")
    parser.add_argument("--db", required=True, help="Path to SQLite DB")
    args = parser.parse_args()

    if not Path(args.repo_dir).is_dir():
        print(f"[SCORE] ERROR: repo dir not found: {args.repo_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"[SCORE] Scoring repo_id={args.repo_id} at {args.repo_dir}")

    scorer = RRSScorer()
    result = scorer.score(args.repo_dir)

    cats = result.category_scores
    print(
        f"[SCORE] RRS={result.rrs:.1f}  "
        f"E={cats['E'].raw_score:.0f}  A={cats['A'].raw_score:.0f}  "
        f"D={cats['D'].raw_score:.0f}  C={cats['C'].raw_score:.0f}  "
        f"S={cats['S'].raw_score:.0f}"
    )
    if result.penalty_environment or result.penalty_data or result.penalty_seed:
        print(
            f"[SCORE] Penalties applied: "
            f"env={result.penalty_environment}  "
            f"data={result.penalty_data}  "
            f"seed={result.penalty_seed}"
        )

    write_scores(args.db, args.repo_id, result)
    print(f"[SCORE] Written to DB (repo_id={args.repo_id})")

    # Now compute ROS and RCS from execution evidence
    score_ros_rcs(args.db, args.repo_id, result.rrs)


if __name__ == "__main__":
    main()
