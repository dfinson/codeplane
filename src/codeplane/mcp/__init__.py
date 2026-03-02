"""MCP server module — FastMCP tool registration and wiring.

Mutation Workflow
=================

The mutation pipeline has **three** gates that agents must understand:

1. **Plan gate** — ``refactor_plan`` declares an edit set and mints edit
   tickets.  ``expected_edit_calls`` is an *agent promise*: it must be
   justified (100+ chars) if >1, and the plan-level budget is enforced.

2. **Session gate** — ``_MAX_EDIT_BATCHES`` (currently 2) caps the total
   number of mutation batches (``refactor_edit`` or ``refactor_commit``)
   before ``checkpoint`` is required.  This is the *hard* limit.  The
   plan-level budget must never exceed the remaining session budget.

3. **Checkpoint gate** — ``checkpoint`` runs lint + affected tests.
   On **success** it commits, clears all mutation state, and resets
   the session budget.  On **failure** it ALSO resets the session
   budget, pre-mints fresh ``EditTicket``s for changed files, and
   returns a ``fix_plan`` with edit tickets — so the agent can
   immediately call ``refactor_edit`` to fix issues without needing a
   new ``refactor_plan``.  Checkpoint failure is a *recovery point*,
   never a dead end.

Batching Rule
-------------
``refactor_edit`` accepts a list of ``FindReplaceEdit`` objects, each
with its own ``path``.  **One call can edit multiple files.**  Agents
should batch ALL edits (source + tests) into the fewest calls possible.
One call editing 5 files across source and tests is preferred over
5 single-file calls.
"""

from codeplane.mcp.context import AppContext
from codeplane.mcp.server import create_mcp_server

__all__ = ["AppContext", "create_mcp_server"]
