#!/bin/bash
###############################################################################
# config/config.sh — Central configuration for the pipeline
###############################################################################
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INPUT_DIR="$PROJECT_ROOT/input"
OUTPUT_DIR="$PROJECT_ROOT/output"
REPOS_DIR="$OUTPUT_DIR/cloned_repos"
COMP_DIR="$OUTPUT_DIR/comparisons"
LOG_DIR="$OUTPUT_DIR/logs"

# Single DB — stores article collection data and all execution results
DB_DIR="$PROJECT_ROOT/output/db"
DB_FILE="$DB_DIR/db.sqlite"

TARGET_COUNT="${TARGET_COUNT:-10}"

export GIT_TERMINAL_PROMPT=0

initialize_directories() {
    mkdir -p "$INPUT_DIR"
    mkdir -p "$REPOS_DIR" "$COMP_DIR" "$LOG_DIR"
    mkdir -p "$DB_DIR"
    log "[INIT] Initialized directory structure"
}

export PROJECT_ROOT INPUT_DIR OUTPUT_DIR REPOS_DIR COMP_DIR LOG_DIR \
       DB_DIR DB_FILE TARGET_COUNT
