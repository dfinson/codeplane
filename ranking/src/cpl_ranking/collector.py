"""Data collection orchestrator — ground truth phase.

Drives the stable, run-once portion of §5.3:
  Phase 1: Solve — coding agent solves task with native tools
  Phase 2: Reflect — classify touched objects, author queries

Output: runs.jsonl, touched_objects.jsonl, queries.jsonl
These are ground truth — they never change once collected.

The retrieval signal collection (recon_raw_signals) is a separate
step in ``collect_signals.py`` because it depends on the current
state of codeplane's harvesters, which we iterate on.

See §5 of ranking-design.md.
"""

from __future__ import annotations


def collect_ground_truth() -> None:
    """Collect stable ground truth for a single task run.

    1. Solve the task (coding agent).
    2. Extract edited objects from diffs (deterministic).
    3. Classify read-necessary vs read-unnecessary objects (LLM judgment).
    4. Author 3 OK queries (L0/L1/L2).
    5. Author up to 3 non-OK queries (UNSAT/BROAD/AMBIG).
    6. Write runs.jsonl, touched_objects.jsonl, queries.jsonl.

    Does NOT call recon_raw_signals — that is a separate, re-runnable step.
    """
    raise NotImplementedError
