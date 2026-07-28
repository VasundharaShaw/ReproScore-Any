#!/bin/bash

process_requirements() {
    log "[REQUIREMENT] Processing requirements for repository..."

    # Prepare temporary files
    TEMP_FILE_REQS="temp_file_reqs.txt"
    TEMP_NB_REQS="temp_nb_reqs.txt"
    TEMP_COMBINED="temp_combined_reqs.txt"
    > "$TEMP_FILE_REQS"
    > "$TEMP_NB_REQS"
    > "$TEMP_COMBINED"

    REQUIREMENTS_FILE="$REPO_DIR/requirements.txt"

    # ====== PART 1: Process requirements files if provided ======
    if [ -n "$REQUIREMENT_PATHS" ]; then
        log "[REQUIREMENT] Processing requirements files from REQUIREMENT_PATHS..."

        IFS=';' read -ra REQUIREMENT_ARRAY <<< "$REQUIREMENT_PATHS"

        for REQUIREMENT_PATH in "${REQUIREMENT_ARRAY[@]}"; do
            REQUIREMENT_PATH=$(echo "$REQUIREMENT_PATH" | xargs)
            FULL_REQUIREMENT_PATH="$REPO_DIR/$REQUIREMENT_PATH"

            if [ -f "$FULL_REQUIREMENT_PATH" ]; then
                log "[REQUIREMENT] Adding $FULL_REQUIREMENT_PATH to combined requirements."
                cat "$FULL_REQUIREMENT_PATH" >> "$TEMP_FILE_REQS"
                echo >> "$TEMP_FILE_REQS"
            else
                log "[WARNING] Requirements file '$FULL_REQUIREMENT_PATH' not found, skipping..."
            fi
        done
    else
        log "[REQUIREMENT] No REQUIREMENT_PATHS provided."
    fi

    # ====== PART 2: Extract imports from notebooks ======
    log "[REQUIREMENT] Extracting imports from notebooks..."

    if [ -z "$NOTEBOOK_PATHS" ]; then
        log "[WARNING] No NOTEBOOK_PATHS provided, skipping import extraction."
    else
        IFS=';' read -ra NOTEBOOK_ARRAY <<< "$NOTEBOOK_PATHS"

        # Define standard libraries
        standard_libs=(
            abc argparse array ast asynchat asyncio asyncore base64 binascii bisect builtins calendar collections
            concurrent contextlib copy copyreg csv ctypes datetime decimal difflib dis distutils doctest email encodings
            enum errno filecmp fileinput fnmatch fractions functools gc getopt getpass gettext glob gzip hashlib heapq
            hmac html http imaplib imp importlib inspect io ipaddress itertools json keyword linecache locale logging lzma
            mailbox math mmap modulefinder multiprocessing numbers operator optparse os pathlib pdb pickle pkgutil platform
            plistlib poplib pprint profile pstats pty pwd py_compile queue quopri random re readline reprlib sched selectors
            shelve shlex shutil signal site smtpd smtplib socket socketserver sqlite3 ssl stat string stringprep struct
            subprocess sys sysconfig tarfile telnetlib tempfile termios textwrap threading time timeit tokenize traceback
            types typing unicodedata unittest urllib uuid warnings wave weakref webbrowser xml xmlrpc zipfile zipimport zlib
        )

        is_standard_lib() {
            local module_name=$1
            for std_lib in "${standard_libs[@]}"; do
                if [[ "$module_name" == "$std_lib" ]]; then
                    return 0
                fi
            done
            return 1
        }

        is_local_module() {
            local module_name=$1
            local repo_path=$2
            if find "$repo_path" -type f -name "${module_name}.py" | grep -q .; then
                return 0
            else
                return 1
            fi
        }

        for NOTEBOOK_PATH in "${NOTEBOOK_ARRAY[@]}"; do
            NOTEBOOK_PATH=$(echo "$NOTEBOOK_PATH" | xargs)
            NOTEBOOK_NAME="$REPO_DIR/$NOTEBOOK_PATH"
            PYTHON_FILE="$REPO_DIR/${NOTEBOOK_PATH%.ipynb}.py"

            if [ ! -f "$NOTEBOOK_NAME" ]; then
                log "[WARNING] Notebook file '$NOTEBOOK_NAME' not found, skipping..."
                continue
            fi

            log "[REQUIREMENT] Converting $NOTEBOOK_NAME to Python script..."
            if ! jupyter nbconvert --to python "$NOTEBOOK_NAME" >> "$LOG_FILE" 2>&1; then
                log "[WARNING] Failed to convert $NOTEBOOK_NAME, skipping..."
                continue
            fi

            if [ ! -f "$PYTHON_FILE" ]; then
                log "[WARNING] Converted Python file '$PYTHON_FILE' not found, skipping..."
                continue
            fi

            log "[REQUIREMENT] Extracting imports from $PYTHON_FILE..."
            grep -E "^(import|from) " "$PYTHON_FILE" | awk '{print $2}' | cut -d '.' -f 1 | sort -u | while read module_name; do
                if [ -z "$module_name" ]; then
                    continue
                fi

                if is_standard_lib "$module_name"; then
                    log "[REQUIREMENT] Skipping standard library: $module_name"
                elif is_local_module "$module_name" "$REPO_DIR"; then
                    log "[REQUIREMENT] Skipping local module: $module_name"
                else
                    echo "$module_name" >> "$TEMP_NB_REQS"
                    log "[REQUIREMENT] Added external library from notebook: $module_name"
                fi
            done

            rm -f "$PYTHON_FILE"
        done
    fi

    # ====== PART 3: Combine and deduplicate ======
    log "[REQUIREMENT] Combining requirements from files and notebooks..."

    cat "$TEMP_FILE_REQS" "$TEMP_NB_REQS" > "$TEMP_COMBINED"

    grep -v '^\s*$' "$TEMP_COMBINED" | \
    grep -v '^\s*#' | \
    sort -u > "$REQUIREMENTS_FILE"

    rm -f "$TEMP_FILE_REQS" "$TEMP_NB_REQS" "$TEMP_COMBINED"

    if [ -s "$REQUIREMENTS_FILE" ]; then
        log "[REQUIREMENT] Final requirements.txt created with $(wc -l < "$REQUIREMENTS_FILE") packages:"
        cat "$REQUIREMENTS_FILE" | head -20
        if [ $(wc -l < "$REQUIREMENTS_FILE") -gt 20 ]; then
            log "[REQUIREMENT] ... (showing first 20 of $(wc -l < "$REQUIREMENTS_FILE") packages)"
        fi
    else
        log "[WARNING] No requirements found. Creating empty requirements.txt."
        touch "$REQUIREMENTS_FILE"
    fi
}
