"""Response assembly — failure actions, hint templates, and summary generation.

Single Responsibility: Shape the final response dict.
No I/O, no database access, no async.  Pure functions on dicts.
"""

from __future__ import annotations

from typing import Any


def _build_failure_actions(
    parsed_terms: list[str], explicit_paths: list[str]
) -> list[dict[str, str]]:
    """Build failure-mode next actions for empty results.

    Provides concrete, actionable suggestions instead of generic text.
    """
    actions: list[dict[str, str]] = []

    actions.append(
        {
            "action": "recon",
            "params": "task='<rephrased with specific symbol names>', seeds=['SymA', 'SymB']",
            "reason": "retry with more specific symbol names or file paths as seeds",
        }
    )

    if explicit_paths:
        for p in explicit_paths[:2]:
            actions.append(
                {
                    "action": "terminal",
                    "params": f"cat {p}",
                    "reason": f"directly read mentioned path '{p}' via terminal",
                }
            )

    actions.append(
        {
            "action": "describe",
            "params": "action='capabilities'",
            "reason": "discover available tools and recovery options",
        }
    )

    return actions


# ===================================================================
# Gate hint templates — extracted from pipeline.py for maintainability
# ===================================================================

_GATE_HARD_BLOCK_HINT = (
    "⛔ RECON HARD GATE — READ THIS COMPLETELY ⛔\n\n"
    "Your first recon call already returned scaffolds, "
    "lites, and repo_map. You have all the file paths and "
    "code structure you need.\n\n"
    "WHAT TO DO INSTEAD:\n"
    "  1. Read files via terminal: cat, head, sed -n\n"
    "  2. Use paths from your recon scaffolds\n"
    "  3. Proceed to refactor_plan → refactor_edit → checkpoint\n\n"
    "IF YOU GENUINELY NEED ANOTHER RECON (different task, "
    "completely different seeds), here is the unlock flow:\n"
    "  Step 1: Copy the gate_token from the 'gate' object below\n"
    "  Step 2: Write a gate_reason (≥{min_chars} chars) that explains:\n"
    "    - What your first recon returned\n"
    "    - What specific context is STILL MISSING\n"
    "    - Why terminal reads (cat/head) cannot fill the gap\n"
    "    - What different seeds/task you need\n"
    "  Step 3: Include pinned_paths listing specific files you need\n"
    "  Step 4: Call recon again with gate_token + gate_reason + pinned_paths\n\n"
    "WARNING: If your gate_reason is vague, generic, or "
    "does not address ALL four points above, the gate will reject it."
)

_GATE_EXCESSIVE_HINT = (
    "⛔ RECON BLOCKED — CALL #{call_num} ⛔\n\n"
    "You have called recon multiple times without making "
    "any edits. STOP calling recon.\n\n"
    "WHAT TO DO INSTEAD:\n"
    "  1. Read files via terminal (cat/head/sed -n)\n"
    "  2. Proceed to refactor_plan → refactor_edit → checkpoint\n\n"
    "TO UNLOCK (only if genuinely necessary):\n"
    "  Step 1: Copy gate_token from the 'gate' object below\n"
    "  Step 2: Write gate_reason (≥{min_chars} chars) explaining:\n"
    "    - What each previous recon returned\n"
    "    - What is STILL MISSING\n"
    "    - Why terminal reads cannot fill the gap\n"
    "    - What different seeds/task you need\n"
    "  Step 3: Include pinned_paths\n"
    "  Step 4: Call recon with all three params"
)

_GATE_MISSING_PINS_HINT = (
    "⛔ RECON BLOCKED — CALL #{call_num} ⛔\n\n"
    "You must provide pinned_paths (specific file paths) "
    "along with gate_token and gate_reason.\n\n"
    "Get file paths from your previous recon scaffolds, "
    "then include them in pinned_paths on your next call."
)


def build_gate_hint(
    kind: str,
    *,
    call_num: int = 2,
    min_chars: int = 500,
) -> str:
    """Build an agentic hint string for a gate block."""
    templates = {
        "hard_block": _GATE_HARD_BLOCK_HINT,
        "excessive": _GATE_EXCESSIVE_HINT,
        "missing_pins": _GATE_MISSING_PINS_HINT,
    }
    template = templates.get(kind, _GATE_EXCESSIVE_HINT)
    return template.format(call_num=call_num, min_chars=min_chars)


# ===================================================================
# Response assembly helpers
# ===================================================================


def build_agentic_hint(
    *,
    n_files: int,
    intent_value: str,
    scaffold_files: list[dict[str, Any]],
    lite_count: int,
    read_only: bool,
    convention_test_paths: set[str],
    tracked_paths: set[str],
    pinned_paths: list[str] | None,
    explicit_paths: list[str] | None,
) -> str:
    """Build the agentic_hint string for a successful recon response."""
    top_paths = [f["path"] for f in scaffold_files[:5]]
    top_paths_str = ", ".join(top_paths) if top_paths else "(none)"

    hint_parts = [
        f"Recon found {n_files} file(s) (intent: {intent_value})."
        f" Scaffolds ({len(scaffold_files)}): {top_paths_str}."
        f" Lite ({lite_count}).",
        "",
        "HOW TO READ SCAFFOLDS: Each file has a header line:"
        " '# path/to/file.py | candidate_id=XXXX | N lines'."
        " Below the header: imports and symbol signatures"
        " (functions, classes, methods with line numbers)."
        " Use these to decide which files you need full content for.",
        "",
        "NEXT: Read the files you need via terminal (cat/head)"
        " using paths from the scaffold headers."
        " Then call refactor_plan with candidate_id values"
        " to declare your edit set.",
    ]

    if not read_only:
        conv_in_scaffold = [f["path"] for f in scaffold_files if f["path"] in convention_test_paths]
        if conv_in_scaffold:
            hint_parts.append("")
            hint_parts.append(
                "TEST CO-EVOLUTION: Test counterparts included in scaffolds."
                " When editing source files, also update or create their"
                " test counterparts. Include BOTH source and test files in"
                " your checkpoint changed_files."
            )

    if tracked_paths:
        requested_paths = set(pinned_paths or []) | set(explicit_paths or [])
        missing_from_repo = sorted(requested_paths - tracked_paths)
        if missing_from_repo:
            hint_parts.append("")
            hint_parts.append(
                "WARNING: These paths do not exist in the repository: "
                + ", ".join(missing_from_repo)
                + ". Do NOT search for them — they are confirmed absent from repo_map."
            )

    return "\n".join(hint_parts)
