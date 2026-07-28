#!/bin/bash

command_exists () {
    command -v "$1" >/dev/null 2>&1
}

validate_repo() {
    local repo_url="$1"
    log "[REPO] Validating repository URL: $repo_url"
    if git ls-remote "$repo_url" &>/dev/null; then
        log "[REPO] Repository URL is valid."
        return 0
    else
        log "[ERROR] Invalid repository URL - $repo_url"
        return 1
    fi
}
