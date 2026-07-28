#!/bin/bash
###############################################################################
# CPRPMC Reproducibility Pipeline — Main Orchestrator
#
# Author:      Sheeba Samuel <sheeba.samuel@informatik.tu-chemnitz.de>
# Co-authors:  Vasundhara Shaw
# Institution: Chemnitz University of Technology
# License:     GPL-3.0
#
# Usage:
#   From repo root:  ./run.sh
#   Directly:        bash pipeline/main.sh
###############################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$PROJECT_ROOT/config/config.sh"
source "$PROJECT_ROOT/src/logging.sh"
source "$PROJECT_ROOT/src/checks.sh"
source "$PROJECT_ROOT/src/db.sh"
source "$PROJECT_ROOT/src/pyenv.sh"
source "$PROJECT_ROOT/src/requirements.sh"
source "$PROJECT_ROOT/src/notebooks.sh"
source "$PROJECT_ROOT/src/repo.sh"

export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

initialize_directories

log "[MAIN] Starting pipeline..."
log "[MAIN] PROJECT_ROOT : $PROJECT_ROOT"
log "[MAIN] Database     : $DB_FILE"
log "[MAIN] Logs         : $LOG_DIR"

ensure_pipeline_tables

prompt_for_input() {
    read -p "Enter GitHub repo URL: " GITHUB_REPO
    read -p "Enter notebook paths (semicolon-separated): " NOTEBOOK_PATHS
    read -p "Enter setup paths (semicolon-separated, optional): " SETUP_PATHS
    read -p "Enter requirements paths (semicolon-separated, optional): " REQUIREMENT_PATHS
}

print_run_summary() {
    local elapsed=$(( $(date +%s) - $1 ))
    local total success failed
    total=$(sqlite3   "$OUTPUT_DB_FILE" "SELECT COUNT(*) FROM repository_runs;")
    success=$(sqlite3 "$OUTPUT_DB_FILE" "SELECT COUNT(*) FROM repository_runs WHERE run_status = 'SUCCESS';")
    failed=$(sqlite3  "$OUTPUT_DB_FILE" "SELECT COUNT(*) FROM repository_runs WHERE run_status NOT IN ('SUCCESS');")
    echo ""
    echo "════════════════════════════════════════"
    echo "        PIPELINE RUN SUMMARY            "
    echo "════════════════════════════════════════"
    echo "  Total runs in DB  : $total"
    echo "  Successful        : $success"
    echo "  Failed/Skipped    : $failed"
    echo "  Elapsed time      : ${elapsed}s"
    echo "  Results stored in : $OUTPUT_DB_FILE"
    echo "  Logs directory    : $LOG_DIR"
    echo "════════════════════════════════════════"
    echo ""
}

RUN_START=$(date +%s)

echo ""
echo "How would you like to run the pipeline?"
echo "  1. Single repo  — enter a GitHub URL interactively"
echo "  2. Batch mode   — process repos from the SQLite database"
echo ""
read -p "Enter your choice (1 or 2): " choice

if [ "$choice" -eq 1 ]; then
    prompt_for_input
    REPO_ID=$(get_or_create_repo_id "$GITHUB_REPO")
    export REPO_ID
    process_repo "$GITHUB_REPO" "$NOTEBOOK_PATHS" "$SETUP_PATHS" "$REQUIREMENT_PATHS"
elif [ "$choice" -eq 2 ]; then
    process_sqlite_flow
else
    echo "[ERROR] Invalid choice. Please enter 1 or 2."
    exit 1
fi

print_run_summary "$RUN_START"
