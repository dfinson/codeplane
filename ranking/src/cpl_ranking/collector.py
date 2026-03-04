"""Data collection orchestrator — ground truth phase.

Drives the stable, run-once portion of §5.3:
  Phase 1: Solve — coding agent solves task
  Phase 2: Reflect — agent produces ground truth

Exact prompts are defined in docs/ranking-design.md §5.3.

Output per task: one Run + N TouchedObjects + 3-6 Queries, written as
JSONL to data/{repo_id}/ground_truth/.

The retrieval signal collection (recon_raw_signals) is a separate step
in ``collect_signals.py`` — it depends on the current state of
codeplane's harvesters and will be re-collected as we iterate.
"""

from __future__ import annotations


def collect_ground_truth() -> None:
    """Collect stable ground truth for a single task run.

    Phase 1 (Solve):
      1. Send SOLVE_PROMPT with task text to the agent.
      2. Agent reads files, makes edits, verifies solution.
      3. Capture git diff → map changed lines to DefFacts via codeplane
         index → these are the "edited" TouchedObjects (deterministic).

    Phase 2 (Reflect):
      4. Send REFLECT_PROMPT in the same session (agent retains context).
      5. Agent returns JSON matching REFLECT_OUTPUT_SCHEMA:
         - read_necessary: file paths read that were needed
         - queries: 3 OK (L0/L1/L2) + up to 3 non-OK
      6. Map read_necessary file paths to DefFacts → "read_necessary"
         TouchedObjects.
      7. Write runs.jsonl, touched_objects.jsonl, queries.jsonl.
    """
    raise NotImplementedError
