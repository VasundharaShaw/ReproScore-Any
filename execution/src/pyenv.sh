#!/bin/bash

# =============================================================================
# pyenv.sh — Python version isolation via pyenv + venv
# Replaces the Docker build/run cycle from docker.sh
#
# Assumes pyenv is installed and available at $PYENV_ROOT (default: ~/.pyenv)
# Each repo gets its own venv under $VENV_BASE_DIR/<REPO_NAME>
# =============================================================================

PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
export PYENV_ROOT
export PATH="$PYENV_ROOT/bin:$PYENV_ROOT/shims:$PATH"
eval "$(pyenv init -)"
VENV_BASE_DIR="${VENV_BASE_DIR:-$HOME/.repo_venvs}"

# Error state — mirrors DOCKER_ERROR_TYPE / DOCKER_ERROR_MESSAGE in docker.sh
ENV_ERROR_TYPE=""
ENV_ERROR_MESSAGE=""


# -----------------------------------------------------------------------------
# detect_python_version()
# Looks for Python version hints in a cloned repo, in priority order:
#   1. binder/runtime.txt  (e.g. "python-3.8")
#   2. runtime.txt         (same format)
#   3. .python-version     (plain version string, e.g. "3.8.12")
#   4. setup.py / setup.cfg python_requires field
#   5. Fallback: 3.10
# -----------------------------------------------------------------------------
detect_python_version() {
    local repo_dir="$1"
    local version=""

    # 1. binder/runtime.txt
    if [ -f "$repo_dir/binder/runtime.txt" ]; then
        version=$(grep -i "^python-" "$repo_dir/binder/runtime.txt" | head -1 | sed 's/python-//')
        if [ -n "$version" ]; then
            log "[PYENV] Python version from binder/runtime.txt: $version" >&2
            echo "$version"
            return
        fi
    fi

    # 2. runtime.txt at root
    if [ -f "$repo_dir/runtime.txt" ]; then
        version=$(grep -i "^python-" "$repo_dir/runtime.txt" | head -1 | sed 's/python-//')
        if [ -n "$version" ]; then
            log "[PYENV] Python version from runtime.txt: $version" >&2
            echo "$version"
            return
        fi
    fi

    # 3. .python-version
    if [ -f "$repo_dir/.python-version" ]; then
        version=$(head -1 "$repo_dir/.python-version" | xargs)
        if [ -n "$version" ]; then
            log "[PYENV] Python version from .python-version: $version" >&2
            echo "$version"
            return
        fi
    fi

    # 4. setup.py / setup.cfg — extract python_requires lower bound
    #    e.g. python_requires=">=3.7" -> 3.7
    if [ -f "$repo_dir/setup.py" ]; then
        version=$(grep -oP "python_requires\s*=\s*['\"]>=\s*\K[0-9]+\.[0-9]+" "$repo_dir/setup.py" | head -1)
        if [ -n "$version" ]; then
            log "[PYENV] Python version from setup.py python_requires: $version" >&2
            echo "$version"
            return
        fi
    fi

    if [ -f "$repo_dir/setup.cfg" ]; then
        version=$(grep -oP "python_requires\s*=\s*>=\s*\K[0-9]+\.[0-9]+" "$repo_dir/setup.cfg" | head -1)
        if [ -n "$version" ]; then
            log "[PYENV] Python version from setup.cfg python_requires: $version" >&2
            echo "$version"
            return
        fi
    fi

    # 5. Fallback
    log "[PYENV] No Python version hint found in repo. Defaulting to 3.10" >&2
    echo "3.10"
}

# -----------------------------------------------------------------------------
# ensure_pyenv_version()
# Installs the requested Python version via pyenv if not already present.
# Accepts partial versions like "3.8" — resolves to latest patch.
# -----------------------------------------------------------------------------
ensure_pyenv_version() {
    local requested="$1"

    if pyenv versions --bare | grep -qx "$requested"; then
        log "[PYENV] Python $requested already installed." >&2
        echo "$requested"
        return 0
    fi

    local resolved
    resolved=$(pyenv install --list 2>/dev/null \
        | grep -E "^\s+${requested}\.[0-9]+$" \
        | grep -v -E "(dev|a|b|rc)" \
        | tail -1 \
        | xargs)

    if [ -z "$resolved" ]; then
        log "[ERROR] [PYENV] No installable Python version matches: $requested" >&2
        return 1
    fi

    if pyenv versions --bare | grep -qx "$resolved"; then
        log "[PYENV] Python $resolved already installed (resolved from $requested)." >&2
        echo "$resolved"
        return 0
    fi

    log "[PYENV] Installing Python $resolved (resolved from $requested)..." >&2
    if ! pyenv install "$resolved" >> "$LOG_FILE" 2>&1; then
        log "[ERROR] [PYENV] Failed to install Python $resolved" >&2
        return 1
    fi

    log "[PYENV] Python $resolved installed successfully." >&2
    echo "$resolved"
}

# -----------------------------------------------------------------------------
# setup_pyenv_env()
# Creates a fresh venv for the repo using the correct Python version,
# installs requirements and any setup.py packages.
#
# Sets globals:
#   REPO_VENV_DIR  — path to the venv for this repo
#   REPO_PYTHON    — path to the python binary inside the venv
#   REPO_PIP       — path to pip inside the venv
# -----------------------------------------------------------------------------
setup_pyenv_env() {
    local repo_dir="$1"         # e.g. ./MyRepo
    local requirements_file="$2"  # e.g. ./MyRepo/requirements.txt
    local setup_paths="$3"       # semicolon-separated, relative to repo_dir

    ENV_ERROR_TYPE=""
    ENV_ERROR_MESSAGE=""

    # --- 1. Detect + install Python version ---
    local requested_version
    requested_version=$(detect_python_version "$repo_dir")

    local python_version
    python_version=$(ensure_pyenv_version "$requested_version")
    if [ $? -ne 0 ] || [ -z "$python_version" ]; then
        ENV_ERROR_TYPE="PYTHON_INSTALL_FAIL"
        ENV_ERROR_MESSAGE="Failed to install Python $requested_version via pyenv"
        return 1
    fi

    local python_bin
    python_bin="$PYENV_ROOT/versions/$python_version/bin/python"

    if [ ! -x "$python_bin" ]; then
        log "[ERROR] [PYENV] Python binary not found at: $python_bin"
        ENV_ERROR_TYPE="PYTHON_BINARY_MISSING"
        ENV_ERROR_MESSAGE="Python binary missing: $python_bin"
        return 1
    fi

    # --- 2. Create fresh venv ---
    mkdir -p "$VENV_BASE_DIR"
    REPO_VENV_DIR="$VENV_BASE_DIR/$REPO_NAME"

    # Always start clean
    if [ -d "$REPO_VENV_DIR" ]; then
        log "[PYENV] Removing existing venv at $REPO_VENV_DIR"
        rm -rf "$REPO_VENV_DIR"
    fi

    log "[PYENV] Creating venv at $REPO_VENV_DIR using Python $python_version"
    if ! "$python_bin" -m venv "$REPO_VENV_DIR" >> "$LOG_FILE" 2>&1; then
        log "[ERROR] [PYENV] Failed to create venv"
        ENV_ERROR_TYPE="VENV_CREATE_FAIL"
        ENV_ERROR_MESSAGE="Failed to create venv using Python $python_version"
        return 1
    fi

    REPO_PYTHON="$REPO_VENV_DIR/bin/python"
    REPO_PIP="$REPO_VENV_DIR/bin/pip"
    export REPO_VENV_DIR REPO_PYTHON REPO_PIP

    # --- 3. Upgrade pip + install jupyter/nbconvert inside venv ---
    log "[PYENV] Upgrading pip and installing jupyter in venv..."
    "$REPO_PIP" install --upgrade pip setuptools wheel >> "$LOG_FILE" 2>&1
    if ! "$REPO_PIP" install jupyter nbconvert >> "$LOG_FILE" 2>&1; then
        log "[ERROR] [PYENV] Failed to install jupyter in venv"
        ENV_ERROR_TYPE="JUPYTER_INSTALL_FAIL"
        ENV_ERROR_MESSAGE="Failed to install jupyter/nbconvert into venv"
        return 1
    fi

    # --- 4. Install repo requirements one by one (mirrors entrypoint.sh behaviour) ---
    if [ -f "$requirements_file" ]; then
        log "[PYENV] Installing requirements from $requirements_file..."
        while IFS= read -r package || [ -n "$package" ]; do
            [[ -z "$package" ]] && continue
            [[ "$package" =~ ^[[:space:]]*# ]] && continue
            log "[PYENV] Installing: $package"
            if "$REPO_PIP" install --no-cache-dir "$package" >> "$LOG_FILE" 2>&1; then
                log "[PYENV] ✓ $package"
            else
                log "[PYENV] ✗ $package (skipping, non-fatal)"
            fi
        done < "$requirements_file"
    else
        log "[PYENV] No requirements.txt found at $requirements_file"
    fi

    # --- 5. Install setup.py packages if any ---
    if [ -n "$setup_paths" ]; then
        log "[PYENV] Processing setup.py paths..."
        IFS=';' read -ra SETUP_FILES <<< "$setup_paths"
        for setup_file in "${SETUP_FILES[@]}"; do
            setup_file=$(echo "$setup_file" | xargs)
            [ -z "$setup_file" ] && continue
            local setup_dir="$repo_dir/$(dirname "$setup_file")"
            if [ -f "$setup_dir/setup.py" ]; then
                log "[PYENV] Installing from $setup_dir"
                (cd "$setup_dir" && "$REPO_PIP" install --no-cache-dir . >> "$LOG_FILE" 2>&1) \
                    || log "[PYENV] Failed to install from $setup_dir (non-fatal)"
            else
                log "[PYENV] No setup.py found in $setup_dir, skipping"
            fi
        done
    fi

    log "[PYENV] Environment ready. Python: $("$REPO_PYTHON" --version 2>&1)"
    return 0
}


# -----------------------------------------------------------------------------
# run_in_pyenv_env()
# Executes all notebooks in NOTEBOOK_PATHS using the venv set up by
# setup_pyenv_env(). Writes results to the exec log in the same format
# as entrypoint.sh so that notebooks.sh / compare_notebook_outputs is unaffected.
# -----------------------------------------------------------------------------
run_in_pyenv_env() {
    local repo_dir="$1"   # e.g. ./MyRepo

    ENV_ERROR_TYPE=""
    ENV_ERROR_MESSAGE=""

    local EXEC_LOG="$LOG_DIR/notebook_execution_times.log"
    local nbconvert="$REPO_VENV_DIR/bin/jupyter-nbconvert"

    if [ ! -x "$nbconvert" ]; then
        # Fallback: invoke via python -m
        nbconvert="$REPO_PYTHON -m nbconvert"
    fi

    if [ -z "$NOTEBOOK_PATHS" ]; then
        log "[PYENV] No NOTEBOOK_PATHS provided, nothing to execute."
        ENV_ERROR_TYPE="NO_NOTEBOOK_PATHS"
        ENV_ERROR_MESSAGE="NOTEBOOK_PATHS was empty"
        return 1
    fi

    local any_executed=false

    IFS=';' read -ra NOTEBOOKS <<< "$NOTEBOOK_PATHS"
    for NOTEBOOK_PATH in "${NOTEBOOKS[@]}"; do
        NOTEBOOK_PATH=$(echo "$NOTEBOOK_PATH" | xargs)
        local full_path="$repo_dir/$NOTEBOOK_PATH"

        if [ ! -f "$full_path" ]; then
            log "[PYENV] Notebook not found: $full_path"
            echo "EXEC_FAIL|$REPO_NAME|$NOTEBOOK_PATH|0|NOTEBOOK_NOT_FOUND" | tee -a "$EXEC_LOG"
            continue
        fi

        local notebook_dir
        notebook_dir=$(dirname "$full_path")
        local base_name
        base_name=$(basename "$NOTEBOOK_PATH" .ipynb)
        local output_nb="$notebook_dir/${base_name}_output.ipynb"

        log "[PYENV] Executing notebook: $NOTEBOOK_PATH"
        local start_ts
        start_ts=$(date +%s)

        # Run nbconvert from within the notebook's directory so relative
        # file paths inside the notebook resolve correctly
        (
            cd "$notebook_dir"
            "$REPO_VENV_DIR/bin/jupyter" nbconvert \
                --to notebook \
                --execute \
                --allow-errors \
                --ExecutePreprocessor.kernel_name=python3 \
                "$(basename "$NOTEBOOK_PATH")" \
                --output "${base_name}_output.ipynb" \
                >> "$LOG_FILE" 2>&1
        )
        local exit_code=$?
        local end_ts
        end_ts=$(date +%s)
        local duration=$(( end_ts - start_ts ))

        if [ ! -f "$output_nb" ]; then
            log "[PYENV] Output notebook not created for $NOTEBOOK_PATH (exit $exit_code)"
            echo "EXEC_FAIL|$REPO_NAME|$NOTEBOOK_PATH|$duration" | tee -a "$EXEC_LOG"
            continue
        fi

        any_executed=true

        if grep -q '"output_type": "error"' "$output_nb"; then
            log "[PYENV] Notebook executed with errors: $NOTEBOOK_PATH"
            echo "SUCCESS_WITH_ERRORS|$REPO_NAME|$NOTEBOOK_PATH|$duration" | tee -a "$EXEC_LOG"
        else
            log "[PYENV] Notebook executed successfully: $NOTEBOOK_PATH"
            echo "SUCCESS|$REPO_NAME|$NOTEBOOK_PATH|$duration" | tee -a "$EXEC_LOG"
        fi
    done

    if [ "$any_executed" = false ]; then
        ENV_ERROR_TYPE="NOTEBOOK_EXECUTION_ERROR"
        ENV_ERROR_MESSAGE="No notebooks were successfully executed"
        return 1
    fi

    return 0
}


# -----------------------------------------------------------------------------
# cleanup_pyenv_env()
# Removes the repo's venv. Call after compare_notebook_outputs to keep
# disk usage in check.
# -----------------------------------------------------------------------------
cleanup_pyenv_env() {
    if [ -n "$REPO_VENV_DIR" ] && [ -d "$REPO_VENV_DIR" ]; then
        log "[PYENV] Cleaning up venv: $REPO_VENV_DIR"
        rm -rf "$REPO_VENV_DIR"
    fi
}


# -----------------------------------------------------------------------------
# analyze_env_error()  — mirrors analyze_container_error() in docker.sh
# Scans LOG_FILE for known error patterns and populates
# ENV_ERROR_TYPE / ENV_ERROR_MESSAGE.
# -----------------------------------------------------------------------------
analyze_env_error() {
    local log_file="$1"
    local error_type="ENV_RUN_FAIL"
    local error_message="Environment execution failed"

    if grep -qi "NoSuchKernel\|No such kernel" "$log_file"; then
        error_type="KERNEL_NOT_FOUND"
        error_message="Jupyter kernel not found"
    elif grep -qi "nbconvert.*failed\|Error executing notebook" "$log_file"; then
        error_type="NOTEBOOK_EXECUTION_ERROR"
        error_message="Failed to execute notebook"
    elif grep -qi "ModuleNotFoundError" "$log_file"; then
        error_type="MODULE_NOT_FOUND"
        missing=$(grep -oP "ModuleNotFoundError: No module named ['\"]?\K[^'\" \n]+" "$log_file" | head -1)
        error_message="Missing Python module: ${missing:-unknown}"
    elif grep -qi "ImportError" "$log_file"; then
        error_type="IMPORT_ERROR"
        error_message=$(grep -oP "ImportError.*" "$log_file" | head -1 | cut -c1-200)
    elif grep -qi "SyntaxError" "$log_file"; then
        error_type="SYNTAX_ERROR"
        error_message="Python syntax error in notebook"
    elif grep -qi "MemoryError" "$log_file"; then
        error_type="MEMORY_ERROR"
        error_message="Out of memory error"
    elif grep -qi "TimeoutError\|timeout" "$log_file"; then
        error_type="TIMEOUT_ERROR"
        error_message="Execution timed out"
    elif grep -qi "ConnectionError\|URLError" "$log_file"; then
        error_type="NETWORK_ERROR"
        error_message="Network connection error"
    fi

    ENV_ERROR_TYPE="$error_type"
    ENV_ERROR_MESSAGE="$error_message"
}
