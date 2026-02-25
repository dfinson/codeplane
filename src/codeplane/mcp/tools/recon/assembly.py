"""Response assembly — failure actions and summary generation.

Single Responsibility: Shape the final response dict.
No I/O, no database access, no async.  Pure functions on dicts.
"""

from __future__ import annotations


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
