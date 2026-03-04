"""Tiered documentation system for MCP tools.

Provides on-demand documentation without bloating ListTools response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolCategory(StrEnum):
    """Categories for grouping tools."""

    MUTATION = "mutation"
    REFACTOR = "refactor"
    SESSION = "session"
    INTROSPECTION = "introspection"


@dataclass
class BehaviorFlags:
    """Behavioral characteristics of a tool."""

    idempotent: bool = False
    has_side_effects: bool = True
    atomic: bool = False
    may_be_slow: bool = False


@dataclass
class ToolDocumentation:
    """Full documentation for a tool (served on demand)."""

    name: str
    description: str
    category: ToolCategory

    # Usage guidance
    when_to_use: list[str] = field(default_factory=list)
    when_not_to_use: list[str] = field(default_factory=list)
    hints_before: str | None = None
    hints_after: str | None = None

    # Related tools
    alternatives: list[str] = field(default_factory=list)
    commonly_preceded_by: list[str] = field(default_factory=list)
    commonly_followed_by: list[str] = field(default_factory=list)

    # Behavior
    behavior: BehaviorFlags = field(default_factory=BehaviorFlags)

    # Errors
    possible_errors: list[str] = field(default_factory=list)

    # Examples
    examples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "when_to_use": self.when_to_use,
            "when_not_to_use": self.when_not_to_use,
            "hints": {
                "before_calling": self.hints_before,
                "after_calling": self.hints_after,
            },
            "related_tools": {
                "alternatives": self.alternatives,
                "commonly_preceded_by": self.commonly_preceded_by,
                "commonly_followed_by": self.commonly_followed_by,
            },
            "behavior": {
                "idempotent": self.behavior.idempotent,
                "has_side_effects": self.behavior.has_side_effects,
                "atomic": self.behavior.atomic,
                "may_be_slow": self.behavior.may_be_slow,
            },
            "possible_errors": self.possible_errors,
            "examples": self.examples,
        }


# =============================================================================
# Tool Documentation Registry
# =============================================================================


TOOL_DOCS: dict[str, ToolDocumentation] = {
    "recon": ToolDocumentation(
        name="recon",
        description="Task-aware file discovery. Returns scaffolds, lite descriptions, and repo_map.",
        category=ToolCategory.INTROSPECTION,
        when_to_use=[
            "Starting any task — the primary entry point",
            "Understanding what files are relevant to a task",
            "Getting scaffolds (imports + signatures) for key files",
        ],
        when_not_to_use=[
            "Already called recon this task (hard-gated to 1 call per task)",
        ],
        hints_before=None,
        hints_after="Read files via terminal (cat, head) using paths from scaffolds.",
        commonly_preceded_by=[],
        commonly_followed_by=["refactor_plan", "refactor_rename"],
        behavior=BehaviorFlags(idempotent=False, has_side_effects=True),
        possible_errors=[],
        examples=[
            {
                "description": "Research a task",
                "params": {"task": "Fix the broken import in coordinator.py", "read_only": True},
            },
            {
                "description": "Prepare for edits",
                "params": {
                    "task": "Rename UserService to AccountService",
                    "seeds": ["UserService"],
                    "read_only": False,
                },
            },
        ],
    ),
    "refactor_plan": ToolDocumentation(
        name="refactor_plan",
        description="Declare edit targets, get plan_id + edit_tickets with SHA256 locks.",
        category=ToolCategory.MUTATION,
        when_to_use=[
            "Before editing files — declare what you intend to change",
            "Getting SHA256-locked edit tickets for safe concurrent editing",
        ],
        when_not_to_use=[
            "For symbol renames across files — use refactor_rename",
            "For file moves — use refactor_move",
        ],
        hints_before="Call recon first to discover candidate files.",
        hints_after="Use refactor_edit with the returned edit_tickets.",
        commonly_preceded_by=["recon"],
        commonly_followed_by=["refactor_edit"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=[],
        examples=[
            {
                "description": "Plan edits to specific files",
                "params": {"edit_targets": ["candidate_id_1", "candidate_id_2"]},
            },
        ],
    ),
    "refactor_edit": ToolDocumentation(
        name="refactor_edit",
        description="Find-and-replace edits with SHA256 locking. One call can edit multiple files.",
        category=ToolCategory.MUTATION,
        when_to_use=[
            "Making precise code changes after refactor_plan",
            "Batch editing multiple files in one call",
        ],
        when_not_to_use=[
            "Renaming symbols across files — use refactor_rename",
            "Without a plan — call refactor_plan first",
        ],
        hints_before="Get edit_tickets from refactor_plan first.",
        hints_after="Call checkpoint to lint, test, and commit your changes.",
        commonly_preceded_by=["refactor_plan"],
        commonly_followed_by=["checkpoint"],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=["HASH_MISMATCH"],
        examples=[
            {
                "description": "Edit a file",
                "params": {
                    "plan_id": "...",
                    "edits": [
                        {
                            "edit_ticket": "...",
                            "path": "src/foo.py",
                            "old_content": "old code",
                            "new_content": "new code",
                            "expected_file_sha256": "...",
                        }
                    ],
                },
            },
        ],
    ),
    "checkpoint": ToolDocumentation(
        name="checkpoint",
        description=(
            "Lint, test, and optionally commit+push in one call. "
            "BLOCKING: acquires exclusive session lock. "
            "You MUST fully process the result before doing any other work."
        ),
        category=ToolCategory.SESSION,
        when_to_use=[
            "After making code changes — validates and optionally saves",
            "One-shot lint → test → commit → push workflow",
            "Resetting session state after read-only flows",
        ],
        when_not_to_use=[],
        hints_before=None,
        hints_after="On failure, budget resets and fix_plan with pre-minted tickets is returned.",
        commonly_preceded_by=["refactor_edit"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True, may_be_slow=True),
        possible_errors=["HOOK_FAILED", "HOOK_FAILED_AFTER_RETRY"],
        examples=[
            {
                "description": "Lint, test, commit, push",
                "params": {
                    "changed_files": ["src/foo.py"],
                    "commit_message": "feat: add feature",
                    "push": True,
                },
            },
        ],
    ),
    "semantic_diff": ToolDocumentation(
        name="semantic_diff",
        description="Structural change summary with blast-radius enrichment.",
        category=ToolCategory.INTROSPECTION,
        when_to_use=[
            "Reviewing changes before committing",
            "Understanding structural impact of edits",
        ],
        when_not_to_use=[],
        hints_before=None,
        hints_after="Read changed files via terminal for full context.",
        commonly_preceded_by=["refactor_edit"],
        commonly_followed_by=["checkpoint"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Diff against main",
                "params": {"base": "main"},
            },
        ],
    ),
    "refactor_rename": ToolDocumentation(
        name="refactor_rename",
        description="Rename a symbol across the codebase with certainty-leveled preview.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "Renaming functions, classes, or variables across multiple files",
        ],
        when_not_to_use=[
            "File moves — use refactor_move",
        ],
        hints_before="Call recon first. Justification is required.",
        hints_after="Use refactor_commit to apply or refactor_cancel to discard.",
        commonly_preceded_by=["recon"],
        commonly_followed_by=["refactor_commit"],
        behavior=BehaviorFlags(has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Rename a function",
                "params": {
                    "symbol": "old_name",
                    "new_name": "new_name",
                    "justification": "Clarify intent",
                },
            },
        ],
    ),
    "refactor_move": ToolDocumentation(
        name="refactor_move",
        description="Move a file/module, updating imports.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "Reorganizing project structure",
            "Moving modules to different packages",
        ],
        when_not_to_use=[
            "Symbol renames — use refactor_rename",
        ],
        hints_before="Call recon first.",
        hints_after="Use refactor_commit to apply or refactor_cancel to discard.",
        commonly_preceded_by=["recon"],
        commonly_followed_by=["refactor_commit"],
        behavior=BehaviorFlags(has_side_effects=False),
        possible_errors=["FILE_NOT_FOUND"],
        examples=[
            {
                "description": "Move a module",
                "params": {"from_path": "src/old/module.py", "to_path": "src/new/module.py"},
            },
        ],
    ),
    "recon_impact": ToolDocumentation(
        name="recon_impact",
        description="Find all references to a symbol/file for read-only impact analysis.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "Auditing all usages of a symbol before changes",
            "Finding dependents before file deletion",
        ],
        when_not_to_use=[
            "When grep/scaffold iteration would suffice — this is for symbol-level analysis",
        ],
        hints_before=None,
        hints_after="Read affected files via terminal.",
        commonly_preceded_by=["recon"],
        commonly_followed_by=["refactor_plan"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Find all usages of a symbol",
                "params": {"target": "deprecated_function"},
            },
        ],
    ),
    "refactor_commit": ToolDocumentation(
        name="refactor_commit",
        description="Apply or inspect a previewed refactoring. With inspect_path: reviews matches. Without: applies all changes.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "After reviewing a refactor_rename/move preview",
            "Inspecting low-certainty matches in a specific file",
        ],
        when_not_to_use=[
            "Without first calling refactor_rename or refactor_move",
        ],
        hints_before="Check verification_required in the preview response.",
        hints_after="Run checkpoint to validate and commit.",
        commonly_preceded_by=["refactor_rename", "refactor_move"],
        commonly_followed_by=["checkpoint"],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=[],
        examples=[
            {
                "description": "Apply refactoring",
                "params": {"refactor_id": "abc123"},
            },
            {
                "description": "Inspect matches in a file first",
                "params": {"refactor_id": "abc123", "inspect_path": "src/module.py"},
            },
        ],
    ),
    "refactor_cancel": ToolDocumentation(
        name="refactor_cancel",
        description="Cancel a pending refactoring and discard the preview.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "After finding false positives in preview",
            "When you want to try different parameters",
        ],
        when_not_to_use=[],
        hints_before=None,
        hints_after=None,
        commonly_preceded_by=["refactor_commit"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=[],
        examples=[
            {
                "description": "Cancel refactoring",
                "params": {"refactor_id": "ref_abc123"},
            },
        ],
    ),
    "describe": ToolDocumentation(
        name="describe",
        description="Introspection: describe tools, errors, capabilities, workflows, or operations.",
        category=ToolCategory.INTROSPECTION,
        when_to_use=[
            "Learning how to use a specific tool",
            "Understanding error codes",
            "Discovering available capabilities",
        ],
        when_not_to_use=[],
        hints_before=None,
        hints_after=None,
        commonly_preceded_by=[],
        commonly_followed_by=[],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Get tool documentation",
                "params": {"action": "tool", "name": "recon"},
            },
            {
                "description": "Understand an error code",
                "params": {"action": "error", "code": "CONTENT_NOT_FOUND"},
            },
        ],
    ),
}


def get_tool_documentation(name: str) -> ToolDocumentation | None:
    """Get documentation for a specific tool."""
    return TOOL_DOCS.get(name)


def get_tools_by_category() -> dict[str, list[str]]:
    """Get tool names grouped by category."""
    by_category: dict[str, list[str]] = {}
    for name, doc in TOOL_DOCS.items():
        category = doc.category.value
        if category not in by_category:
            by_category[category] = []
        by_category[category].append(name)
    return by_category


def get_common_workflows() -> list[dict[str, Any]]:
    """Get common workflow patterns."""
    return [
        {
            "name": "exploration",
            "description": "Understanding a codebase",
            "tools": ["recon", "describe"],
        },
        {
            "name": "modification",
            "description": "Making code changes",
            "tools": ["recon", "refactor_plan", "refactor_edit", "checkpoint"],
        },
        {
            "name": "refactoring",
            "description": "Renaming and restructuring",
            "tools": ["recon", "refactor_rename", "refactor_commit", "checkpoint"],
        },
        {
            "name": "review",
            "description": "Reviewing changes",
            "tools": ["semantic_diff", "checkpoint"],
        },
    ]


def build_tool_description(tool_name: str, base_description: str) -> str:
    """Build enriched tool description with inline examples.

    Appends examples from TOOL_DOCS to the base description for inclusion
    in the MCP ListTools response. This makes examples visible to agents
    without requiring a separate describe() call.

    Args:
        tool_name: Name of the tool (key in TOOL_DOCS)
        base_description: The tool's base docstring description

    Returns:
        Description with appended examples, or base_description if no examples.
    """
    import json

    doc = TOOL_DOCS.get(tool_name)
    if not doc or not doc.examples:
        return base_description

    lines = [base_description.rstrip(), "", "Examples:"]
    for ex in doc.examples:
        # Format: description → JSON params
        params_str = json.dumps(ex["params"], separators=(", ", ": "))
        lines.append(f"  {ex['description']}: {tool_name}({params_str})")

    return "\n".join(lines)
