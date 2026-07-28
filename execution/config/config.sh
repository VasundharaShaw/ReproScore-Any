#!/bin/bash
###############################################################################
# config.sh — Central configuration for the CPRPMC pipeline
###############################################################################
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT_DIR="$PROJECT_ROOT/input"
OUTPUT_DIR="$PROJECT_ROOT/output"
REPOS_DIR="$OUTPUT_DIR/cloned_repos"
COMP_DIR="$OUTPUT_DIR/comparisons"
LOG_DIR="$OUTPUT_DIR/logs"

# Input DB — Sheeba's original repo list, READ ONLY
DB_DIR="$PROJECT_ROOT/data"
DB_FILE="$DB_DIR/db.sqlite"

# Output DB — created fresh by pipeline, stores all execution results
OUTPUT_DB_DIR="$PROJECT_ROOT/output/db"
OUTPUT_DB_FILE="$OUTPUT_DB_DIR/db.sqlite"

TARGET_COUNT="${TARGET_COUNT:-10}"
export GIT_TERMINAL_PROMPT=0

initialize_directories() {
    mkdir -p "$INPUT_DIR"
    mkdir -p "$REPOS_DIR" "$COMP_DIR" "$LOG_DIR"
    mkdir -p "$OUTPUT_DB_DIR"
    log "[INIT] Initialized directory structure"
}

export PROJECT_ROOT INPUT_DIR OUTPUT_DIR REPOS_DIR COMP_DIR LOG_DIR \
       DB_DIR DB_FILE OUTPUT_DB_DIR OUTPUT_DB_FILE TARGET_COUNT
