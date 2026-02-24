"""Response assembly — budget trimming and summary generation.

Single Responsibility: Shape the final response dict.
No I/O, no database access, no async.  Pure functions on dicts.

Contribution-aware trimming (Section 2): instead of fixed priority order,
estimates value-per-byte and trims the lowest-value content first.
"""

from __future__ import annotations

import json
from typing import Any


def _estimate_bytes(obj: Any) -> int:
    """Rough byte estimate of a JSON-serializable object."""
    return len(json.dumps(obj, default=str).encode("utf-8"))


def _trim_to_budget(result: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim response to fit within budget, using contribution-aware strategy.

    Supports both v6 (tier-based) and v5 (bucket-based) response formats.

    Tier-based: summary_only trimmed first, then min_scaffold content,
    then full_file content.

    Bucket-based: supplementary trimmed first, then context, then edit_targets.

    Within each tier/bucket, items are removed from the back (lowest-scored
    first, since results are already sorted by relevance).
    """
    current = _estimate_bytes(result)
    if current <= budget:
        return result

    # ── v6 tier-based format ──
    if "summary_only" in result:
        # Tier 0: Drop summary_only entries from back
        while result.get("summary_only") and _estimate_bytes(result) > budget:
            removed = result["summary_only"].pop()
            if "files" in result:
                result["files"] = [
                    f for f in result["files"] if f.get("path") != removed.get("path")
                ]
        if _estimate_bytes(result) <= budget:
            return result

        # Tier 1: Strip scaffold content from min_scaffold entries
        if result.get("min_scaffold"):
            for entry in result["min_scaffold"]:
                if "scaffold" in entry:
                    del entry["scaffold"]
                if "scaffold_preview" in entry:
                    del entry["scaffold_preview"]
                # Update in flat files list too
                if "files" in result:
                    for f in result["files"]:
                        if f.get("path") == entry.get("path"):
                            f.pop("scaffold", None)
                            f.pop("scaffold_preview", None)
        if _estimate_bytes(result) <= budget:
            return result

        # Tier 2: Drop min_scaffold entries from back
        while result.get("min_scaffold") and _estimate_bytes(result) > budget:
            removed = result["min_scaffold"].pop()
            if "files" in result:
                result["files"] = [
                    f for f in result["files"] if f.get("path") != removed.get("path")
                ]
        if _estimate_bytes(result) <= budget:
            return result

        # Tier 3: Truncate full_file content
        if result.get("full_file"):
            for entry in reversed(result["full_file"]):
                content = entry.get("content", "")
                if len(content) > 10_000:
                    entry["content"] = content[:10_000] + "\n... (truncated)"
                    if "files" in result:
                        for f in result["files"]:
                            if f.get("path") == entry.get("path"):
                                f["content"] = entry["content"]
                if _estimate_bytes(result) <= budget:
                    return result

        return result

    # ── v5 bucket-based format (legacy) ──

    # Tier 0: Slim supplementary seeds to path-only stubs (keep file awareness)
    if "supplementary" in result:
        for i, seed in enumerate(result["supplementary"]):
            # Strip source/callers/callees/import_defs — keep path+bucket+evidence
            stub = {
                k: seed[k]
                for k in (
                    "def_uid",
                    "path",
                    "symbol",
                    "kind",
                    "span",
                    "bucket",
                    "bucket_rank",
                    "score",
                    "evidence",
                    "edit_score",
                    "context_score",
                )
                if k in seed
            }
            result["supplementary"][i] = stub
            # Also slim the flat seeds list counterpart
            if "seeds" in result:
                for j, s in enumerate(result["seeds"]):
                    if s.get("def_uid") == seed.get("def_uid") and s.get("path") == seed.get(
                        "path"
                    ):
                        result["seeds"][j] = stub
                        break

        # If still over budget, drop supplementary seeds from back
        while result["supplementary"] and _estimate_bytes(result) > budget:
            removed = result["supplementary"].pop()
            if "seeds" in result:
                result["seeds"] = [
                    s
                    for s in result["seeds"]
                    if s.get("def_uid") != removed.get("def_uid")
                    or s.get("path") != removed.get("path")
                    or s is not removed
                ]
        if _estimate_bytes(result) <= budget:
            return result

    # Tier 1: Trim import scaffolds (lowest information density)
    if "import_scaffolds" in result:
        while result["import_scaffolds"] and _estimate_bytes(result) > budget:
            result["import_scaffolds"].pop()
        if not result["import_scaffolds"]:
            del result["import_scaffolds"]
        if _estimate_bytes(result) <= budget:
            return result

    # Tier 2: Trim callers within each seed (context snippets are large)
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "callers" in seed_data:
                while seed_data["callers"] and _estimate_bytes(result) > budget:
                    seed_data["callers"].pop()
                if not seed_data["callers"]:
                    del seed_data["callers"]

    if _estimate_bytes(result) <= budget:
        return result

    # Tier 3: Trim import_defs within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "import_defs" in seed_data:
                while seed_data["import_defs"] and _estimate_bytes(result) > budget:
                    seed_data["import_defs"].pop()
                if not seed_data["import_defs"]:
                    del seed_data["import_defs"]

    if _estimate_bytes(result) <= budget:
        return result

    # Tier 4: Trim callees within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "callees" in seed_data:
                while seed_data["callees"] and _estimate_bytes(result) > budget:
                    seed_data["callees"].pop()
                if not seed_data["callees"]:
                    del seed_data["callees"]

    if _estimate_bytes(result) <= budget:
        return result

    # Tier 5: Trim siblings within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "siblings" in seed_data:
                while seed_data["siblings"] and _estimate_bytes(result) > budget:
                    seed_data["siblings"].pop()
                if not seed_data["siblings"]:
                    del seed_data["siblings"]

    return result


def _build_failure_actions(
    parsed_terms: list[str], explicit_paths: list[str]
) -> list[dict[str, str]]:
    """Build failure-mode next actions for empty results (Section 7).

    Provides concrete, actionable suggestions instead of generic text.
    """
    actions: list[dict[str, str]] = []

    if parsed_terms:
        actions.append(
            {
                "action": "search",
                "params": f"mode='lexical', query='{' '.join(parsed_terms[:3])}'",
                "reason": "lexical search may find text patterns that semantic search missed",
            }
        )

    actions.append(
        {
            "action": "map_repo",
            "params": "include=['structure']",
            "reason": "browse repo structure to identify relevant directories",
        }
    )

    if explicit_paths:
        for p in explicit_paths[:2]:
            actions.append(
                {
                    "action": "read_source",
                    "params": f"path='{p}'",
                    "reason": f"directly read mentioned path '{p}'",
                }
            )

    actions.append(
        {
            "action": "recon",
            "params": "task='<rephrased with specific symbol names>'",
            "reason": "retry with more specific symbol names or file paths",
        }
    )

    return actions
