#!/usr/bin/env bash
# Index all cloned repos with codeplane. 30-minute timeout per repo.
# Usage: bash index_all.sh [--reindex]
#
# Logs: recon-lab/infra/index_all.log
# Flagged repos (>30min or error): recon-lab/infra/index_flagged.txt

set -euo pipefail

LAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CLONES_DIR=$(python3 -c "
import tomllib, pathlib
lab = pathlib.Path('${LAB_DIR}/lab.toml')
cfg = tomllib.loads(lab.read_text()) if lab.exists() else {}
print(pathlib.Path(cfg.get('workspace',{}).get('path','~/.cpl-lab')).expanduser() / 'clones')
")
CPL="$(cd "$(dirname "$0")/../../" && pwd)/.venv/bin/cpl"
LOG="$(dirname "$0")/index_all.log"
FLAGGED="$(dirname "$0")/index_flagged.txt"
TIMEOUT=1800  # 30 minutes

REINDEX_FLAG=""
if [[ "${1:-}" == "--reindex" ]]; then
    REINDEX_FLAG="-r"
fi

: > "$LOG"
: > "$FLAGGED"

TOTAL=0
OK=0
SKIPPED=0
FAILED=0

for REPO_DIR in "$CLONES_DIR"/*/*; do
    [[ -d "$REPO_DIR/.git" ]] || continue
    TOTAL=$((TOTAL + 1))
    REPO_NAME="$(basename "$(dirname "$REPO_DIR")")/$(basename "$REPO_DIR")"

    # Skip if already indexed and not forcing reindex
    if [[ -z "$REINDEX_FLAG" && -d "$REPO_DIR/.codeplane" ]]; then
        echo "SKIP $REPO_NAME (already indexed)" | tee -a "$LOG"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    echo -n "INDEX $REPO_NAME ... " | tee -a "$LOG"
    START=$(date +%s)

    if timeout "$TIMEOUT" "$CPL" init $REINDEX_FLAG "$REPO_DIR" >> "$LOG" 2>&1; then
        ELAPSED=$(( $(date +%s) - START ))
        echo "OK (${ELAPSED}s)" | tee -a "$LOG"
        OK=$((OK + 1))
    else
        EXIT_CODE=$?
        ELAPSED=$(( $(date +%s) - START ))
        if [[ $EXIT_CODE -eq 124 ]]; then
            echo "TIMEOUT (${ELAPSED}s)" | tee -a "$LOG"
            echo "TIMEOUT $REPO_NAME (${ELAPSED}s)" >> "$FLAGGED"
        else
            echo "FAILED exit=$EXIT_CODE (${ELAPSED}s)" | tee -a "$LOG"
            echo "FAILED $REPO_NAME exit=$EXIT_CODE (${ELAPSED}s)" >> "$FLAGGED"
        fi
        FAILED=$((FAILED + 1))
    fi
done

echo "" | tee -a "$LOG"
echo "========================================" | tee -a "$LOG"
echo "Total: $TOTAL | OK: $OK | Skipped: $SKIPPED | Failed: $FAILED" | tee -a "$LOG"

if [[ -s "$FLAGGED" ]]; then
    echo "" | tee -a "$LOG"
    echo "FLAGGED REPOS:" | tee -a "$LOG"
    cat "$FLAGGED" | tee -a "$LOG"
fi
