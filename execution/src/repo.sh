#!/bin/bash
###############################################################################
# repo.sh — Per-repository orchestration and batch SQLite flow
###############################################################################

export GITHUB_REPO

create_repository_run() {
    RUN_ID=$(sqlite3 "$DB_FILE" <<EOF
INSERT INTO repository_runs (repository_id, url, run_status, started_at)
VALUES ($1, '$2', 'RUNNING', datetime('now'));
SELECT last_insert_rowid();
EOF
)
    export RUN_ID
}

finalize_repository_run() {
    log "[REPO] Finalizing run $1 — status: $2"
    sqlite3 "$DB_FILE" <<EOF
UPDATE repository_runs
SET run_status='$2', error_message='$3', finished_at=datetime('now'), duration_seconds=$4
WHERE id=$1;
EOF
}

get_notebook_language_stats() {
    sqlite3 "$DB_FILE" <<EOF
SELECT COUNT(*), SUM(CASE WHEN LOWER(language)='python' THEN 1 ELSE 0 END)
FROM notebooks WHERE repository_id=$1;
EOF
}

get_or_create_repo_id() {
    local repo_path="${1#https://github.com/}"
    local existing_id
    existing_id=$(sqlite3 "$DB_FILE" "SELECT id FROM repositories WHERE repository='$repo_path' LIMIT 1;")
    if [ -n "$existing_id" ]; then echo "$existing_id"; return 0; fi
    sqlite3 "$DB_FILE" <<EOF
INSERT INTO repositories (repository, notebooks, setups, requirements, notebooks_count, setups_count, requirements_count)
VALUES ('$repo_path', '$NOTEBOOK_PATHS', '$SETUP_PATHS', '$REQUIREMENT_PATHS', 1, 0, 0);
SELECT last_insert_rowid();
EOF
}

# Populates the notebooks table from user-supplied paths (single-repo mode).
# Accepts GitHub blob URLs or plain relative paths.
# In batch mode the notebooks are already in the DB, so this is a safe no-op.
insert_notebooks_from_paths() {
    local repo_id="$1"
    local notebook_paths="$2"

    [ -z "$notebook_paths" ] && return 0

    IFS=';' read -ra nb_array <<< "$notebook_paths"
    for nb_path in "${nb_array[@]}"; do
        # Trim whitespace
        nb_path=$(echo "$nb_path" | xargs)
        [ -z "$nb_path" ] && continue

        # Strip GitHub blob URL down to relative path
        # handles: https://github.com/owner/repo/blob/<branch>/path/to/file.ipynb
        if [[ "$nb_path" == https://github.com/* ]]; then
            nb_path=$(echo "$nb_path" | sed 's|https://github.com/[^/]*/[^/]*/blob/[^/]*/||')
        fi

        # Only register .ipynb files
        [[ "$nb_path" != *.ipynb ]] && continue

        local existing
        existing=$(sqlite3 "$DB_FILE" \
            "SELECT id FROM notebooks WHERE repository_id=$repo_id AND name='$nb_path' LIMIT 1;")
        if [ -z "$existing" ]; then
            sqlite3 "$DB_FILE" \
                "INSERT INTO notebooks (repository_id, name, language) VALUES ($repo_id, '$nb_path', 'python');"
            log "[REPO] Registered notebook: $nb_path" >&2
        fi
    done
}

process_repo() {
    REPO_START_TIME=$(now_sec)
    GITHUB_REPO="$1"; NOTEBOOK_PATHS="$2"; SETUP_PATHS="$3"; REQUIREMENT_PATHS="$4"
    REPO_NAME=$(basename "$GITHUB_REPO" .git)
    REPO_DIR="$REPOS_DIR/$REPO_NAME"
    log "[REPO] ── Starting: $REPO_NAME ──────────────────────────────"
    LOG_FILE="${LOG_DIR}/${REPO_NAME}.log"; > "$LOG_FILE"; export LOG_FILE
    create_repository_run "$REPO_ID" "$GITHUB_REPO"

    # Single-repo mode: populate notebooks table from user-supplied URLs/paths.
    # Batch mode: notebooks already in DB — this becomes a no-op.
    insert_notebooks_from_paths "$REPO_ID" "$NOTEBOOK_PATHS"

    # Normalize NOTEBOOK_PATHS: strip GitHub blob URLs to relative paths
    # so all downstream code (notebooks.sh etc.) gets plain relative paths
    local normalized=""
    IFS=';' read -ra _nb_array <<< "$NOTEBOOK_PATHS"
    for _nb in "${_nb_array[@]}"; do
        _nb=$(echo "$_nb" | xargs)
        [[ "$_nb" == https://github.com/* ]] && _nb=$(echo "$_nb" | sed 's|https://github.com/[^/]*/[^/]*/blob/[^/]*/||')
        [ -n "$normalized" ] && normalized="${normalized};${_nb}" || normalized="$_nb"
    done
    NOTEBOOK_PATHS="$normalized"

    if ! validate_repo "$GITHUB_REPO"; then
        finalize_repository_run "$RUN_ID" "INVALID_REPOSITORY_URL" "git ls-remote failed" "$(elapsed_sec "$REPO_START_TIME")"
        return 0
    fi

    stats=$(get_notebook_language_stats "$REPO_ID")
    total_notebooks=$(echo "$stats" | cut -d'|' -f1)
    python_notebooks=$(echo "$stats" | cut -d'|' -f2)
    log "[REPO] Notebooks: total=$total_notebooks python=$python_notebooks"

    if [ "$total_notebooks" -eq 0 ]; then
        finalize_repository_run "$RUN_ID" "NO_NOTEBOOKS" "No notebooks found" "$(elapsed_sec "$REPO_START_TIME")"
        return 0
    fi
    if [ "$python_notebooks" -eq 0 ]; then
        finalize_repository_run "$RUN_ID" "NO_PYTHON_NOTEBOOKS" "No Python notebooks found" "$(elapsed_sec "$REPO_START_TIME")"
        return 0
    fi

    # Clone into REPOS_DIR first, before any file operations
    if [ -d "$REPO_DIR" ]; then
        log "[REPO] Repo already exists, pulling latest..."
        cd "$REPO_DIR" && git pull >> "$LOG_FILE" 2>&1 && cd - > /dev/null
    else
        log "[REPO] Cloning into $REPO_DIR..."
        git clone --depth 1 "$GITHUB_REPO" "$REPO_DIR" >> "$LOG_FILE" 2>&1
    fi

    if [ ! -d "$REPO_DIR" ]; then
        finalize_repository_run "$RUN_ID" "REPO_DIR_MISSING" "Directory not found after clone" "$(elapsed_sec "$REPO_START_TIME")"
        return 0
    fi

    # Auto-discover notebooks if none provided
    if [ -z "$NOTEBOOK_PATHS" ]; then
        log "[REPO] No notebook paths provided — auto-discovering .ipynb files..."
        discovered=""
        while IFS= read -r nb; do
            rel="${nb#$REPO_DIR/}"
            [ -n "$discovered" ] && discovered="${discovered};${rel}" || discovered="$rel"
        done < <(find "$REPO_DIR" -name "*.ipynb" | sort)

        if [ -z "$discovered" ]; then
            finalize_repository_run "$RUN_ID" "NO_NOTEBOOKS" "No .ipynb files found in repo" "$(elapsed_sec "$REPO_START_TIME")"
            return 0
        fi

        log "[REPO] Discovered notebooks: $discovered"
        NOTEBOOK_PATHS="$discovered"
        insert_notebooks_from_paths "$REPO_ID" "$NOTEBOOK_PATHS"
    fi

    process_requirements

    REQUIREMENTS_FILE="$REPO_DIR/requirements.txt"
    if ! setup_pyenv_env "$REPO_DIR" "$REQUIREMENTS_FILE" "$SETUP_PATHS"; then
        finalize_repository_run "$RUN_ID" "$ENV_ERROR_TYPE" "$ENV_ERROR_MESSAGE" "$(elapsed_sec "$REPO_START_TIME")"
        cleanup_pyenv_env; return 0
    fi

    if ! run_in_pyenv_env "$REPO_DIR"; then
        analyze_env_error "$LOG_FILE"
        finalize_repository_run "$RUN_ID" "$ENV_ERROR_TYPE" "$ENV_ERROR_MESSAGE" "$(elapsed_sec "$REPO_START_TIME")"
        cleanup_pyenv_env; return 0
    fi

    NOTEBOOKS_COUNT=$(echo "$NOTEBOOK_PATHS" | awk -F';' '{print NF}')
    export NOTEBOOKS_COUNT
    compare_notebook_outputs
    cleanup_pyenv_env

    local total_time
    total_time=$(elapsed_sec "$REPO_START_TIME")
    finalize_repository_run "$RUN_ID" "SUCCESS" "Repository executed successfully" "$total_time"
    log "[REPO] ── Done: $REPO_NAME (${total_time}s) ──────────────────────"
    isExecutedSuccessfully="true"
    export RUN_ID NOTEBOOKS_COUNT
}

process_sqlite_flow() {
    processed_repo_ids=()
    local processed_count=0
    log "[BATCH] Processing next $TARGET_COUNT unexecuted repositories."

    while [ $processed_count -lt "$TARGET_COUNT" ]; do
        local not_in_clause=""
        if [ ${#processed_repo_ids[@]} -gt 0 ]; then
            not_in_clause="AND r.id NOT IN ($(IFS=,; echo "${processed_repo_ids[*]}"))"
        fi

        repo_data=$(sqlite3 "$DB_FILE" <<EOF
.mode csv
.headers off
SELECT r.id, r.repository, r.notebooks, r.setups, r.requirements
FROM repositories r
WHERE r.notebooks IS NOT NULL AND TRIM(r.notebooks) != ''
AND r.notebooks_count != 0
AND r.id NOT IN (SELECT DISTINCT repository_id FROM repository_runs)
$not_in_clause
ORDER BY r.id LIMIT 1;
EOF
)
        if [ -z "$repo_data" ]; then log "[BATCH] No more repositories."; break; fi

        IFS=',' read -r REPO_ID REPO_PATH NOTEBOOK_PATHS SETUP_PATHS REQUIREMENT_PATHS <<< "$repo_data"
        GITHUB_REPO="https://github.com/${REPO_PATH}"
        NOTEBOOK_PATHS=$(echo "$NOTEBOOK_PATHS" | tr -d '\r\n"')
        REQUIREMENT_PATHS=$(echo "$REQUIREMENT_PATHS" | tr -d '\r\n"')
        SETUP_PATHS=$(echo "$SETUP_PATHS" | tr -d '\r\n"')
        log "[BATCH] Repo $REPO_ID: $GITHUB_REPO"

        processed_repo_ids+=("$REPO_ID")
        isExecutedSuccessfully="false"

        if ! process_repo "$GITHUB_REPO" "$NOTEBOOK_PATHS" "$SETUP_PATHS" "$REQUIREMENT_PATHS"; then
            REPO_NAME=$(basename "$REPO_PATH")
            [ -d "$REPOS_DIR/$REPO_NAME" ] && rm -rf "$REPOS_DIR/$REPO_NAME"
            processed_count=$((processed_count + 1))
            continue
        fi
        [[ "$isExecutedSuccessfully" == "true" ]] && processed_count=$((processed_count + 1))
    done
    log "[BATCH] Finished. Processed $processed_count repositories."
}
