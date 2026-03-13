#!/usr/bin/env bash
# Initialize the recon-lab pipeline workspace.
#
# Creates the directory structure that clone.py, index.py,
# and gt_orchestrator.py expect.  The workspace path is read
# from lab.toml (workspace.path) or defaults to ~/.cpl-lab.
#
# Usage:
#   bash recon-lab/setup_workspace.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE=$(python3 -c "
import tomllib, pathlib, sys
lab = pathlib.Path('${SCRIPT_DIR}/lab.toml')
cfg = tomllib.loads(lab.read_text()) if lab.exists() else {}
print(pathlib.Path(cfg.get('workspace',{}).get('path','~/.cpl-lab')).expanduser())
")

mkdir -p \
    "$WORKSPACE/clones" \
    "$WORKSPACE/data/merged" \
    "$WORKSPACE/data/logs/sessions" \
    "$WORKSPACE/data/logs/errors"

echo "Lab workspace initialized at: $WORKSPACE"
