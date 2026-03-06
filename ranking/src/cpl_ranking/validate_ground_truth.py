"""Validate ground truth JSON files against expected schema.

Run after each repo's executor + reviewer pass to catch schema drift,
missing fields, wrong types, and kind vocabulary violations before
post-processing.

Usage:
    python -m cpl_ranking.validate_ground_truth data/python-fastapi
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

VALID_KINDS = frozenset({
    "function", "method", "class", "struct", "interface", "trait",
    "enum", "variable", "constant", "module", "property", "pair",
    "key", "table", "target", "heading",
})

VALID_QUERY_TYPES = frozenset({
    "Q_SEMANTIC", "Q_LEXICAL", "Q_IDENTIFIER", "Q_STRUCTURAL",
    "Q_NAVIGATIONAL", "Q_SEM_IDENT", "Q_IDENT_NAV", "Q_FULL",
})

VALID_COMPLEXITIES = frozenset({"narrow", "medium", "wide"})

VALID_CONFIDENCES = frozenset({"high", "medium", "low"})

NON_OK_TYPES = frozenset({"UNSAT", "BROAD", "AMBIG"})


def _check_def_entry(entry: dict, context: str) -> list[str]:
    """Validate a single def entry (minimum_sufficient / thrash_preventing / excluded)."""
    errors: list[str] = []
    for field in ("path", "name", "kind", "reason"):
        if field not in entry:
            errors.append(f"{context}: missing required field '{field}'")
    if "start_line" not in entry:
        errors.append(f"{context}: missing required field 'start_line'")
    elif not isinstance(entry["start_line"], int):
        errors.append(f"{context}: 'start_line' must be int, got {type(entry['start_line']).__name__}")
    if entry.get("kind") and entry["kind"] not in VALID_KINDS:
        errors.append(f"{context}: invalid kind '{entry['kind']}'")
    return errors


def _check_query(query: dict, context: str) -> list[str]:
    """Validate a single query entry."""
    errors: list[str] = []
    for field in ("query_type", "query_text", "seeds", "pins", "justification"):
        if field not in query:
            errors.append(f"{context}: missing required field '{field}'")
    qt = query.get("query_type", "")
    if qt not in VALID_QUERY_TYPES:
        errors.append(f"{context}: invalid query_type '{qt}'")
    if "expected_defs" not in query:
        errors.append(f"{context}: missing 'expected_defs'")
    elif not isinstance(query["expected_defs"], list):
        errors.append(f"{context}: 'expected_defs' must be a list")
    return errors


def validate_task(task: dict, file_path: str) -> list[str]:
    """Validate a single task ground truth JSON."""
    errors: list[str] = []
    ctx = file_path

    # Required top-level fields
    for field in (
        "task_id", "task_complexity", "task_text", "diff", "solve_notes",
        "exploration_log", "confidence",
        "minimum_sufficient_defs", "thrash_preventing_defs",
        "tier_difference_reasoning", "excluded_defs", "queries",
    ):
        if field not in task:
            errors.append(f"{ctx}: missing required field '{field}'")

    # task_complexity
    tc = task.get("task_complexity")
    if tc and tc not in VALID_COMPLEXITIES:
        errors.append(f"{ctx}: invalid task_complexity '{tc}'")

    # confidence
    conf = task.get("confidence")
    if conf and conf not in VALID_CONFIDENCES:
        errors.append(f"{ctx}: invalid confidence '{conf}'")

    # Def lists
    for tier_key in ("minimum_sufficient_defs", "thrash_preventing_defs", "excluded_defs"):
        defs = task.get(tier_key, [])
        if not isinstance(defs, list):
            errors.append(f"{ctx}: '{tier_key}' must be a list")
            continue
        for i, d in enumerate(defs):
            errors.extend(_check_def_entry(d, f"{ctx}/{tier_key}[{i}]"))

    # minimum_sufficient must have at least one edited def
    min_defs = task.get("minimum_sufficient_defs", [])
    has_edited = any(
        d.get("reason", "").startswith("edited:")
        for d in min_defs if isinstance(d, dict)
    )
    if min_defs and not has_edited:
        errors.append(f"{ctx}: no 'edited:' reason in minimum_sufficient_defs")

    # Queries
    queries = task.get("queries", [])
    if not isinstance(queries, list):
        errors.append(f"{ctx}: 'queries' must be a list")
    else:
        query_types_seen = set()
        for i, q in enumerate(queries):
            errors.extend(_check_query(q, f"{ctx}/queries[{i}]"))
            query_types_seen.add(q.get("query_type"))
        # At least 6 query types for narrow, 8 for medium/wide
        min_queries = 6 if tc == "narrow" else 8
        if len(queries) < min_queries:
            errors.append(f"{ctx}: expected >= {min_queries} queries, got {len(queries)}")

    # exploration_log structure
    elog = task.get("exploration_log")
    if isinstance(elog, dict):
        for key in ("search_sequence", "dead_ends", "key_decisions", "aha_moment", "hindsight"):
            if key not in elog:
                errors.append(f"{ctx}/exploration_log: missing '{key}'")
    elif elog is not None:
        errors.append(f"{ctx}: 'exploration_log' must be a dict")

    return errors


def validate_non_ok(data: dict, file_path: str) -> list[str]:
    """Validate non_ok_queries.json."""
    errors: list[str] = []
    ctx = file_path

    if "repo_id" not in data:
        errors.append(f"{ctx}: missing 'repo_id'")
    if "non_ok_queries" not in data:
        errors.append(f"{ctx}: missing 'non_ok_queries'")
        return errors

    queries = data["non_ok_queries"]
    if not isinstance(queries, list):
        errors.append(f"{ctx}: 'non_ok_queries' must be a list")
        return errors

    type_counts: dict[str, int] = {"UNSAT": 0, "BROAD": 0, "AMBIG": 0}

    for i, q in enumerate(queries):
        qctx = f"{ctx}/non_ok_queries[{i}]"
        qt = q.get("query_type", "")
        if qt not in NON_OK_TYPES:
            errors.append(f"{qctx}: invalid query_type '{qt}'")
            continue

        type_counts[qt] = type_counts.get(qt, 0) + 1

        for field in ("query_text", "seeds", "pins"):
            if field not in q:
                errors.append(f"{qctx}: missing '{field}'")

        if qt == "UNSAT":
            for field in ("false_assumption", "evidence_of_absence"):
                if field not in q:
                    errors.append(f"{qctx}: UNSAT missing '{field}'")
        elif qt == "BROAD":
            for field in ("why_no_cutoff", "dispersion_description"):
                if field not in q:
                    errors.append(f"{qctx}: BROAD missing '{field}'")
        elif qt == "AMBIG":
            for field in ("candidate_neighborhoods", "why_ambiguous"):
                if field not in q:
                    errors.append(f"{qctx}: AMBIG missing '{field}'")

    for qt, count in type_counts.items():
        if count < 2:
            errors.append(f"{ctx}: need >= 2 {qt} queries, got {count}")

    return errors


def validate_repo(data_dir: Path) -> list[str]:
    """Validate all ground truth files for a repo."""
    errors: list[str] = []

    gt_dir = data_dir / "ground_truth"
    if not gt_dir.exists():
        errors.append(f"Directory not found: {gt_dir}")
        return errors

    task_files = sorted(gt_dir.glob("*.json"))
    if not task_files:
        errors.append(f"No JSON files in {gt_dir}")
        return errors

    for tf in task_files:
        try:
            task = json.loads(tf.read_text())
        except json.JSONDecodeError as e:
            errors.append(f"{tf.name}: invalid JSON — {e}")
            continue
        errors.extend(validate_task(task, tf.name))

    # Non-OK queries
    non_ok_path = data_dir / "non_ok_queries.json"
    if non_ok_path.exists():
        try:
            non_ok = json.loads(non_ok_path.read_text())
        except json.JSONDecodeError as e:
            errors.append(f"non_ok_queries.json: invalid JSON — {e}")
        else:
            errors.extend(validate_non_ok(non_ok, "non_ok_queries.json"))
    else:
        errors.append("non_ok_queries.json not found")

    return errors


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m cpl_ranking.validate_ground_truth data/{repo_id}")
        sys.exit(1)

    data_dir = Path(sys.argv[1])
    errors = validate_repo(data_dir)

    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s):\n")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print(f"VALIDATION PASSED — {data_dir.name}")
        sys.exit(0)


if __name__ == "__main__":
    main()
