"""Response assembly — budget trimming and summary generation.

Single Responsibility: Shape the final response dict.
No I/O, no database access, no async.  Pure functions on dicts.

Tier-based trimming: summary_only → scaffold content → scaffold entries → full_file content.
"""

from __future__ import annotations

import json
from typing import Any


def _estimate_bytes(obj: Any) -> int:
    """Rough byte estimate of a JSON-serializable object."""
    return len(json.dumps(obj, default=str).encode("utf-8"))


def _trim_to_budget(result: dict[str, Any], budget: int) -> dict[str, Any]:
    """Trim response to fit within budget using tier-based strategy.

    Tier-based: summary_only trimmed first, then min_scaffold content,
    then full_file content.

    Within each tier, items are removed from the back (lowest-scored
    first, since results are already sorted by relevance).
    """
    current = _estimate_bytes(result)
    if current <= budget:
        return result

    # Tier 0: Drop summary_only entries from back
    while result.get("summary_only") and _estimate_bytes(result) > budget:
        removed = result["summary_only"].pop()
        if "files" in result:
            result["files"] = [f for f in result["files"] if f.get("path") != removed.get("path")]
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
            result["files"] = [f for f in result["files"] if f.get("path") != removed.get("path")]
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
