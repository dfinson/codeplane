"""Data collection orchestrator — ground truth phase.

One agent session per repo. The agent receives a single prompt pointing
it at the repo's MD file (ranking/repos/{name}.md). It reads the file,
solves each task sequentially, and writes a JSON file per task to
data/{repo_id}/ground_truth/{task_id}.json.

After all tasks are complete, a post-processing step maps edited/read
files to DefFacts via the codeplane index and assembles the JSONL
output files.

Exact prompt is defined in docs/ranking-design.md §5.3.
"""

from __future__ import annotations


def collect_ground_truth() -> None:
    """Orchestrate ground truth collection for one repo.

    1. Send the agent prompt (from §5.3) pointing at the repo's MD file.
    2. Agent iterates all tasks: solve → reflect → write JSON → stash.
    3. Post-process per-task JSON files:
       a. Map edited_files to DefFacts via codeplane index.
       b. Map read_necessary to DefFacts.
       c. Write runs.jsonl, touched_objects.jsonl, queries.jsonl.
    """
    raise NotImplementedError
