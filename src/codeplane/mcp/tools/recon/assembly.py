"""Response assembly — budget trimming and summary generation.

Single Responsibility: Shape the final response dict.
No I/O, no database access, no async.  Pure functions on dicts.
"""

from __future__ import annotations

import json
from typing import Any


def _estimate_bytes(obj: Any) -> int:
    """Rough byte estimate of a JSON-serializable object."""
    return len(json.dumps(obj, default=str).encode("utf-8"))


def _trim_to_budget(
    result: dict[str, Any], budget: int
) -> dict[str, Any]:
    """Trim response to fit within budget, removing lowest-priority content.

    Priority (keep order): seeds > callees > import_defs > callers > scaffolds
    """
    current = _estimate_bytes(result)
    if current <= budget:
        return result

    # Trim P4: import scaffolds
    if "import_scaffolds" in result:
        while result["import_scaffolds"] and _estimate_bytes(result) > budget:
            result["import_scaffolds"].pop()
        if not result["import_scaffolds"]:
            del result["import_scaffolds"]
        if _estimate_bytes(result) <= budget:
            return result

    # Trim P3: callers within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "callers" in seed_data:
                while (
                    seed_data["callers"]
                    and _estimate_bytes(result) > budget
                ):
                    seed_data["callers"].pop()
                if not seed_data["callers"]:
                    del seed_data["callers"]

    if _estimate_bytes(result) <= budget:
        return result

    # Trim P2.5: import_defs within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "import_defs" in seed_data:
                while (
                    seed_data["import_defs"]
                    and _estimate_bytes(result) > budget
                ):
                    seed_data["import_defs"].pop()
                if not seed_data["import_defs"]:
                    del seed_data["import_defs"]

    if _estimate_bytes(result) <= budget:
        return result

    # Trim P2: callees within each seed
    if "seeds" in result:
        for seed_data in result["seeds"]:
            if "callees" in seed_data:
                while (
                    seed_data["callees"]
                    and _estimate_bytes(result) > budget
                ):
                    seed_data["callees"].pop()
                if not seed_data["callees"]:
                    del seed_data["callees"]

    return result


def _summarize_recon(
    seed_count: int,
    callee_count: int,
    caller_count: int,
    import_def_count: int,
    scaffold_count: int,
    task_preview: str,
) -> str:
    """Generate summary for recon response."""
    parts = [f'{seed_count} seeds for "{task_preview}"']
    if callee_count:
        parts.append(f"{callee_count} callees")
    if import_def_count:
        parts.append(f"{import_def_count} import defs")
    if caller_count:
        parts.append(f"{caller_count} callers")
    if scaffold_count:
        parts.append(f"{scaffold_count} scaffolds")
    return ", ".join(parts)
