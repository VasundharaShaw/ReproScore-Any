#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export DB_FILE="$PROJECT_ROOT/data/output/db/test.sqlite"

source "$PROJECT_ROOT/config/config.sh"
source "$PROJECT_ROOT/src/logging.sh"
source "$PROJECT_ROOT/src/checks.sh"
source "$PROJECT_ROOT/src/db.sh"
source "$PROJECT_ROOT/src/pyenv.sh"
source "$PROJECT_ROOT/src/requirements.sh"
source "$PROJECT_ROOT/src/notebooks.sh"
source "$PROJECT_ROOT/src/repo.sh"

initialize_directories
ensure_pipeline_tables

TEST_REPO="https://github.com/theislab/scanpy-tutorials"
TEST_NOTEBOOKS="tutorials/basics/clustering-2017.ipynb"

REPO_ID=$(get_or_create_repo_id "$TEST_REPO")
export REPO_ID
process_repo "$TEST_REPO" "$TEST_NOTEBOOKS" "" ""

RUN_COUNT=$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM repository_runs;")
echo "[TEST] Run records: $RUN_COUNT"

if [ "$RUN_COUNT" -gt 0 ]; then
    echo "[TEST] PASSED"
else
    echo "[TEST] FAILED"; exit 1
fi

rm -f "$DB_FILE"
