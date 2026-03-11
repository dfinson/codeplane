#!/usr/bin/env bash
# Initialize the ranking pipeline workspace.
#
# Creates the directory structure that clone_repos.sh, index_all.sh,
# and gt_orchestrator.py expect under $CPL_RANKING_WORKSPACE.
#
# Usage:
#   bash ranking/setup_workspace.sh
#
#   # Or with a custom location:
#   export CPL_RANKING_WORKSPACE=/mnt/data/ranking
#   bash ranking/setup_workspace.sh

set -euo pipefail

WORKSPACE="${CPL_RANKING_WORKSPACE:-$HOME/.codeplane/ranking}"

mkdir -p \
    "$WORKSPACE/clones" \
    "$WORKSPACE/data/merged" \
    "$WORKSPACE/data/logs/sessions" \
    "$WORKSPACE/data/logs/errors"

echo "Ranking workspace initialized at: $WORKSPACE"
echo ""
echo "To persist, add to your shell profile:"
echo "  export CPL_RANKING_WORKSPACE=$WORKSPACE"
