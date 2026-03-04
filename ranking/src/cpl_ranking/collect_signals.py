"""Signal collection — recon_raw_signals phase.

Re-runnable step that calls ``recon_raw_signals()`` for every query
in the ground truth and writes the candidate pool with per-retriever
scores.

This is separated from ground truth collection (``collector.py``)
because retrieval signals change as we iterate on harvesters,
embeddings, and scoring. Ground truth stays fixed; signals get
re-collected whenever the retrieval pipeline changes.

Output: candidates_rank.jsonl (per query, keyed by run_id + query_id)

See §5.3 step 2d and §6 of ranking-design.md.
"""

from __future__ import annotations


def collect_signals() -> None:
    """Collect retrieval signals for all queries in ground truth.

    For each query in queries.jsonl:
      1. Call recon_raw_signals(query_text) against the indexed repo.
      2. Join candidate pool with touched_objects to compute label_rank.
      3. Write candidates_rank.jsonl rows.

    This step is idempotent — re-running overwrites previous signal
    data for the same (run_id, query_id) pairs.
    """
    raise NotImplementedError
