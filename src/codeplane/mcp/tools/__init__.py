"""MCP tool handlers.

Edit Budget Model
=================

Two budgets constrain mutation calls:

**Plan budget** (``expected_edit_calls``, set by agent in ``refactor_plan``):
    Number of ``refactor_edit`` calls the agent commits to.  Default 1.
    If >1, the agent must provide ``batch_justification`` (100+ chars)
    explaining why a single batched call is impossible.  This forces the
    agent to consider batching BEFORE it starts editing.

**Session budget** (``_MAX_EDIT_BATCHES``, hard limit, currently 2):
    Total mutation batches allowed before ``checkpoint`` is required.
    Resets on ANY checkpoint call (pass or fail).  The plan budget must
    never exceed this — ``refactor_plan`` should clamp or reject.

Checkpoint Failure Recovery
---------------------------
When ``checkpoint`` fails (lint or test errors), it does NOT leave the
agent stuck.  It:

1. Clears all mutation state (``mutation_ctx.clear()``)
2. Re-reads changed files from disk (current content + sha256)
3. Pre-mints ``EditTicket``s with ``issued_by="checkpoint_fix"``
4. Creates a ``fix_plan`` (``RefactorPlan`` with ``expected_edit_calls=1``)
5. Returns structured error data: lint errors, test failures, scaffolds,
   plus the fix_plan with ready-to-use edit tickets
6. Caches refreshed file content in sidecar for jq access

The agent's next step after a failed checkpoint is always:
``refactor_edit(plan_id=fix_plan.plan_id, edits=[...])`` → then retry
``checkpoint``.  No new ``refactor_plan`` is needed.

Multi-File Batching
-------------------
Each ``refactor_edit`` call accepts a list of ``FindReplaceEdit`` objects.
Each edit has its own ``path`` field — **one call can modify many files**.
Agents should group all related changes (source + tests) into a single
call whenever possible.  „One call with 5 edits across 3 files" is
better than „3 calls with 1-2 edits each".
"""

from codeplane.mcp.tools import (
    checkpoint,
    diff,
    edit,
    introspection,
    recon,
    refactor,
    resolve,
)

__all__ = [
    "checkpoint",
    "diff",
    "edit",
    "introspection",
    "recon",
    "refactor",
    "resolve",
]
