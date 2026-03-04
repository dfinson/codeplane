"""Data collection orchestrator.

Drives the §5.3 pipeline per repo per task:
  Phase 1: Solve — coding agent solves task with native tools
  Phase 2: Reflect — classify touched objects, author queries,
           call recon_raw_signals(), assemble dataset rows

See §5 of ranking-design.md.
"""

from __future__ import annotations


def collect_task_data() -> None:
    """Collect training data for a single task run.

    1. Solve the task (coding agent).
    2. Extract edited objects from diffs.
    3. Classify read-necessary vs read-unnecessary objects.
    4. Author 3 OK queries (L0/L1/L2).
    5. Author up to 3 non-OK queries (UNSAT/BROAD/AMBIG).
    6. Call recon_raw_signals() per query.
    7. Assemble and validate dataset rows.
    """
    raise NotImplementedError
