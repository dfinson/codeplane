"""Ground truth post-processing — converts per-task JSON to JSONL tables.

After the agent completes all tasks for a repo, this module reads the
per-task JSON files from ``data/{repo_id}/ground_truth/`` and assembles
``runs.jsonl``, ``touched_objects.jsonl``, and ``queries.jsonl``.

Each ``relevant_defs`` entry is resolved against the codeplane index
to get ``def_uid``, ``start_line``, ``end_line``.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def _resolve_def(
    cursor: sqlite3.Cursor,
    path: str,
    name: str,
    kind: str,
    start_line: int,
) -> dict[str, Any] | None:
    """Look up a DefFact in the index by (path, name, kind, start_line).

    Uses start_line for exact match first. If the exact line doesn't
    match (index may have shifted slightly), falls back to nearest
    match within 5 lines.

    Returns dict with def_uid, start_line, end_line or None if not found.
    """
    # Exact match
    row = cursor.execute(
        """
        SELECT d.def_uid, d.start_line, d.end_line
        FROM def_facts d
        JOIN files f ON d.file_id = f.id
        WHERE f.path = ? AND d.name = ? AND d.kind = ?
          AND d.start_line = ?
        LIMIT 1
        """,
        (path, name, kind, start_line),
    ).fetchone()
    if row is not None:
        return {"def_uid": row[0], "start_line": row[1], "end_line": row[2]}

    # Nearest match within 5 lines (handles minor index drift)
    row = cursor.execute(
        """
        SELECT d.def_uid, d.start_line, d.end_line
        FROM def_facts d
        JOIN files f ON d.file_id = f.id
        WHERE f.path = ? AND d.name = ? AND d.kind = ?
          AND ABS(d.start_line - ?) <= 5
        ORDER BY ABS(d.start_line - ?)
        LIMIT 1
        """,
        (path, name, kind, start_line, start_line),
    ).fetchone()
    if row is not None:
        return {"def_uid": row[0], "start_line": row[1], "end_line": row[2]}

    return None


def collect_ground_truth(
    repo_id: str,
    data_dir: Path,
    index_db: Path,
) -> dict[str, Any]:
    """Post-process agent JSON output into JSONL tables.

    Args:
        repo_id: Repository identifier.
        data_dir: Path to ``data/{repo_id}/`` directory containing
            ``ground_truth/*.json`` files from the agent.
        index_db: Path to the repo's ``.codeplane/index.db``.

    Returns:
        Summary dict with counts and unmatched defs.
    """
    gt_dir = data_dir / "ground_truth"
    task_files = sorted(gt_dir.glob("*.json"))
    if not task_files:
        raise FileNotFoundError(f"No ground truth JSON files in {gt_dir}")

    con = sqlite3.connect(str(index_db))
    cur = con.cursor()

    runs: list[dict[str, Any]] = []
    touched: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []
    unmatched: list[dict[str, str]] = []

    for tf in task_files:
        task = json.loads(tf.read_text())
        task_id = task["task_id"]
        run_id = f"{repo_id}_{task_id}"

        runs.append({
            "run_id": run_id,
            "repo_id": repo_id,
            "task_id": task_id,
            "task_text": task["task_text"],
        })

        # Resolve two-tier ground truth
        for tier_key, tier_label in [
            ("minimum_sufficient_defs", "minimum"),
            ("thrash_preventing_defs", "thrash_preventing"),
        ]:
            for rd in task.get(tier_key, []):
                resolved = _resolve_def(
                    cur, rd["path"], rd["name"], rd["kind"],
                    start_line=rd["start_line"],
                )
                if resolved is None:
                    unmatched.append({
                        "task_id": task_id,
                        "tier": tier_label,
                        "path": rd["path"],
                        "name": rd["name"],
                        "kind": rd["kind"],
                    })
                    continue
                touched.append({
                    "run_id": run_id,
                    "def_uid": resolved["def_uid"],
                    "path": rd["path"],
                    "kind": rd["kind"],
                    "name": rd["name"],
                    "start_line": resolved["start_line"],
                    "end_line": resolved["end_line"],
                    "tier": tier_label,
                })

        # Collect audit record
        audit_records.append({
            "run_id": run_id,
            "task_id": task_id,
            "diff": task.get("diff", ""),
            "solve_notes": task.get("solve_notes", ""),
            "confidence": task.get("confidence", "unknown"),
            "excluded_defs": task.get("excluded_defs", []),
            "justifications": {
                tier_key: [
                    {"path": d["path"], "name": d["name"], "reason": d.get("reason", "")}
                    for d in task.get(tier_key, [])
                ]
                for tier_key in ("minimum_sufficient_defs", "thrash_preventing_defs")
            },
        })

        # Process queries
        for qi, q in enumerate(task.get("queries", [])):
            query_type = q["query_type"]
            label = "OK" if query_type.startswith("Q_") else query_type
            queries.append({
                "run_id": run_id,
                "query_id": f"{run_id}_q{qi}",
                "query_text": q["query_text"],
                "query_type": query_type,
                "seeds": q.get("seeds", []),
                "pins": q.get("pins", []),
                "label_gate": label,
            })

    con.close()

    # Write JSONL files
    out_dir = data_dir / "ground_truth"
    _write_jsonl(out_dir / "runs.jsonl", runs)
    _write_jsonl(out_dir / "touched_objects.jsonl", touched)
    _write_jsonl(out_dir / "queries.jsonl", queries)

    # Write audit records for third-agent auditing
    audit_dir = data_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(audit_dir / "audit_records.jsonl", audit_records)

    # Tier breakdown
    n_minimum = sum(1 for t in touched if t["tier"] == "minimum")
    n_thrash = sum(1 for t in touched if t["tier"] == "thrash_preventing")

    summary = {
        "repo_id": repo_id,
        "tasks": len(runs),
        "relevant_defs_total": len(touched),
        "minimum_sufficient": n_minimum,
        "thrash_preventing": n_thrash,
        "queries": len(queries),
        "unmatched": len(unmatched),
        "unmatched_rate": len(unmatched) / max(len(touched) + len(unmatched), 1),
        "unmatched_details": unmatched,
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows as newline-delimited JSON."""
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
