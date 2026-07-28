#!/bin/bash
###############################################################################
# notebooks.sh — Notebook comparison and output processing
###############################################################################

compare_notebook_outputs_json() {
    local notebook1="$1" notebook2="$2" comparison_file="$3"
    mkdir -p "$(dirname "$comparison_file")"
    if [ ! -f "$notebook2" ]; then
        log "[NOTEBOOK] Executed notebook missing: $notebook2 — skipping"
        return 0
    fi
    python3 -u "$PROJECT_ROOT/analysis/compare_notebook.py" \
        "$notebook1" "$notebook2" "$NOTEBOOK_PATH" "$REPO_ID" \
        --json "$comparison_file" 2>&1 | tee -a "$LOG_FILE"
}

compare_notebook_outputs() {
    log "[NOTEBOOK] Comparing outputs for: $REPO_NAME"
    IFS=";" read -ra NOTEBOOK_ARRAY <<< "$NOTEBOOK_PATHS"
    for NOTEBOOK_PATH in "${NOTEBOOK_ARRAY[@]}"; do
        if [ ! -f "$REPO_DIR/$NOTEBOOK_PATH" ]; then
            log "[NOTEBOOK] Not found: $REPO_DIR/$NOTEBOOK_PATH — skipping"
            continue
        fi
        local notebook_dir base_name original_notebook executed_notebook comparison_result_file
        notebook_dir=$(dirname "$NOTEBOOK_PATH")
        base_name=$(basename "$NOTEBOOK_PATH" .ipynb)
        original_notebook="$REPO_DIR/$NOTEBOOK_PATH"
        executed_notebook="$REPO_DIR/${notebook_dir}/${base_name}_output.ipynb"
        comparison_result_file="${COMP_DIR}/${base_name}_comparison.json"
        NOTEBOOK_ID=$(get_notebook_id_from_db "$NOTEBOOK_PATH")
        REPO_ID=$(get_repo_id_from_db "$GITHUB_REPO")
        log "[NOTEBOOK] ID=$NOTEBOOK_ID  REPO_ID=$REPO_ID  path=$NOTEBOOK_PATH"
        if [ -z "$NOTEBOOK_ID" ]; then
            log "[NOTEBOOK] No DB record for $NOTEBOOK_PATH — skipping"
            continue
        fi
        if [ ! -f "$executed_notebook" ]; then
            log "[NOTEBOOK] Output missing for $NOTEBOOK_PATH — recording failure"
            cat <<EOF > "$comparison_result_file"
{
  "notebook": "$NOTEBOOK_PATH",
  "NOTEBOOK_ID": "$NOTEBOOK_ID",
  "REPO_ID": "$REPO_ID",
  "status": "failed",
  "reason": "output_notebook_not_created"
}
EOF
            continue
        fi
        compare_notebook_outputs_json "$original_notebook" "$executed_notebook" "$comparison_result_file"
    done
}
