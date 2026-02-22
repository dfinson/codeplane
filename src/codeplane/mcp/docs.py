"""Tiered documentation system for MCP tools.

Provides on-demand documentation without bloating ListTools response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolCategory(StrEnum):
    """Categories for grouping tools."""

    GIT = "git"
    FILES = "files"
    SEARCH = "search"
    MUTATION = "mutation"
    REFACTOR = "refactor"
    TESTING = "testing"
    LINT = "lint"
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
    "read_source": ToolDocumentation(
        name="read_source",
        description="Read file contents with optional line ranges.",
        category=ToolCategory.FILES,
        when_to_use=[
            "Reading source code for analysis",
            "Fetching config files",
            "Getting partial file content with line ranges",
        ],
        when_not_to_use=[
            "Large binary files",
            "Files outside the repository",
            "When you need to find content - use 'search' instead",
        ],
        hints_before="Use map_repo first if you're unsure which files exist.",
        hints_after=None,
        alternatives=["search"],
        commonly_preceded_by=["map_repo", "search"],
        commonly_followed_by=["write_source"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False, atomic=True),
        possible_errors=["FILE_NOT_FOUND", "ENCODING_ERROR", "INVALID_RANGE"],
        examples=[
            {
                "description": "Read specific lines (10-50)",
                "params": {"targets": [{"path": "src/main.py", "start_line": 10, "end_line": 50}]},
            },
            {
                "description": "Read multiple spans in one call",
                "params": {
                    "targets": [
                        {"path": "src/foo.py", "start_line": 1, "end_line": 30},
                        {"path": "src/bar.py", "start_line": 100, "end_line": 150},
                    ]
                },
            },
        ],
    ),
    "write_source": ToolDocumentation(
        name="write_source",
        description="Atomic file edits with structured delta response.",
        category=ToolCategory.MUTATION,
        when_to_use=[
            "Making precise code changes",
            "Creating new files",
            "Deleting files",
        ],
        when_not_to_use=[
            "Renaming symbols across files - use refactor_rename",
            "Large-scale refactoring",
            "When you haven't read the file recently",
        ],
        hints_before="Read the target file first to ensure you have current content.",
        hints_after="Use commit to commit the change.",
        alternatives=["refactor_rename (for symbol renames)"],
        commonly_preceded_by=["read_source"],
        commonly_followed_by=["commit"],
        possible_errors=[
            "FILE_NOT_FOUND",
            "FILE_EXISTS",
            "HASH_MISMATCH",
        ],
        examples=[
            {
                "description": "Span-based update (replace lines 10-15)",
                "params": {
                    "edits": [
                        {
                            "path": "src/foo.py",
                            "action": "update",
                            "start_line": 10,
                            "end_line": 15,
                            "expected_file_sha256": "<sha256 from read_source>",
                            "new_content": "def new_name():\n    pass\n",
                        }
                    ]
                },
            },
        ],
    ),
    "map_repo": ToolDocumentation(
        name="map_repo",
        description="Build repository mental model from indexed data.",
        category=ToolCategory.INTROSPECTION,
        when_to_use=[
            "Starting work on unfamiliar codebase",
            "Understanding project structure",
            "Finding entry points and key files",
        ],
        when_not_to_use=[
            "Looking for specific content - use 'search'",
            "Already familiar with the codebase structure",
        ],
        hints_before=None,
        hints_after="Use read_source to dive into specific files of interest.",
        alternatives=["search"],
        commonly_preceded_by=[],
        commonly_followed_by=["read_source", "search"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Get repository overview",
                "params": {"include": ["structure", "languages", "entry_points"]},
            },
        ],
    ),
    "search": ToolDocumentation(
        name="search",
        description="Search code, symbols, or references with configurable context.",
        category=ToolCategory.SEARCH,
        when_to_use=[
            "Finding where a function is defined",
            "Finding usages of a symbol",
            "Text search across codebase",
            "Getting edit-ready code snippets with context=function/class",
        ],
        when_not_to_use=[
            "When you know the exact file - use read_source",
        ],
        hints_before=None,
        hints_after="Use context='function' or 'class' for edit-ready results. Use context='rich' for 20 lines. Only use read_source if you need more context than search provides.",
        alternatives=["map_repo (for structure overview)"],
        commonly_preceded_by=["map_repo"],
        commonly_followed_by=["write_source", "read_source"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Find symbol definition",
                "params": {"query": "UserService", "mode": "symbol"},
            },
            {
                "description": "Find all references",
                "params": {"query": "handle_request", "mode": "references"},
            },
            {
                "description": "Search with enclosing function body (edit-ready)",
                "params": {"query": "handle_request", "enrichment": "function"},
            },
            {
                "description": "Search with enclosing class body",
                "params": {"query": "UserService", "mode": "symbol", "enrichment": "class"},
            },
        ],
    ),
    "commit": ToolDocumentation(
        name="commit",
        description="Stage, lint, run pre-commit hooks, commit, and optionally push.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Committing changes after edits",
            "When pre-commit hooks may auto-fix files (formatters, linters)",
            "Single-step stage + commit + push workflow",
        ],
        when_not_to_use=[
            "When you only need git status/log/diff/branch — use terminal commands",
        ],
        hints_before="Verify your changes look correct before committing.",
        hints_after="For other git operations (status, log, diff, push, pull, branch, checkout, merge, reset, stash, rebase), use terminal commands directly.",
        alternatives=[],
        commonly_preceded_by=["write_source", "verify"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=["HOOK_FAILED", "HOOK_FAILED_AFTER_RETRY"],
        examples=[
            {
                "description": "Stage and commit specific files",
                "params": {"paths": ["src/foo.py", "src/bar.py"], "message": "feat: add feature"},
            },
            {
                "description": "Stage all and commit",
                "params": {"all": True, "message": "chore: update files"},
            },
            {
                "description": "Commit and push in one call",
                "params": {"paths": ["src/foo.py"], "message": "fix: resolve bug", "push": True},
            },
        ],
    ),
    "verify": ToolDocumentation(
        name="verify",
        description="Run lint + affected tests in one call. The 'did I break anything?' check.",
        category=ToolCategory.TESTING,
        when_to_use=[
            "After making code changes, before committing",
            "Quick validation that nothing is broken",
        ],
        when_not_to_use=[
            "When you need fine-grained control over lint or test options",
        ],
        hints_before=None,
        hints_after="If verify passes, use commit to save your work.",
        alternatives=[],
        commonly_preceded_by=["write_source"],
        commonly_followed_by=["commit"],
        behavior=BehaviorFlags(has_side_effects=True, may_be_slow=True),
        possible_errors=[],
        examples=[
            {
                "description": "Verify after editing files",
                "params": {"changed_files": ["src/foo.py", "src/bar.py"]},
            },
            {
                "description": "Lint only, skip tests",
                "params": {"changed_files": ["src/foo.py"], "tests": False},
            },
        ],
    ),
    # =========================================================================
    # Files Tools
    # =========================================================================
    "list_files": ToolDocumentation(
        name="list_files",
        description="List files and directories with optional filtering.",
        category=ToolCategory.FILES,
        when_to_use=[
            "Exploring directory contents",
            "Finding files matching a pattern",
            "Checking if a path exists",
        ],
        when_not_to_use=[
            "When you need file contents - use read_source",
            "When you need repository overview - use map_repo",
        ],
        hints_before=None,
        hints_after="Use read_source to examine specific files of interest.",
        alternatives=["map_repo (for tree structure)"],
        commonly_preceded_by=["map_repo"],
        commonly_followed_by=["read_source"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=["FILE_NOT_FOUND"],
        examples=[
            {
                "description": "List Python files recursively",
                "params": {"path": "src", "pattern": "*.py", "recursive": True},
            },
        ],
    ),
    # =========================================================================
    # Refactor Tools
    # =========================================================================
    "refactor_rename": ToolDocumentation(
        name="refactor_rename",
        description="Rename a symbol across the codebase. Returns a preview with certainty levels: high (definition proven by index), medium (comments/docstrings), low (lexical matches). Use refactor_inspect to review low-certainty matches before refactor_apply.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "Renaming functions, classes, or variables across multiple files",
            "Ensuring all references (imports, usages, comments) are updated",
        ],
        when_not_to_use=[
            "Simple find/replace in one file - use write_source",
            "File renames - use refactor_move",
        ],
        hints_before="Ensure the codebase is indexed. For unique identifiers (MyClassName), low-certainty matches are usually safe. For common words (data, result), inspect before applying.",
        hints_after="Check verification_required and low_certainty_files in response. If true, use refactor_inspect(refactor_id, path) to review matches with context, then refactor_apply or refactor_cancel.",
        commonly_preceded_by=["search"],
        commonly_followed_by=["refactor_inspect", "refactor_apply"],
        behavior=BehaviorFlags(has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Rename a function",
                "params": {"symbol": "old_function_name", "new_name": "new_function_name"},
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
            "Simple symbol renames - use refactor_rename",
        ],
        hints_before="Ensure the codebase is indexed.",
        hints_after="Review with refactor_inspect, then apply with refactor_apply.",
        commonly_preceded_by=["map_repo"],
        commonly_followed_by=["refactor_inspect", "refactor_apply"],
        behavior=BehaviorFlags(has_side_effects=False),
        possible_errors=["FILE_NOT_FOUND"],
        examples=[
            {
                "description": "Move a module",
                "params": {"from_path": "src/old/module.py", "to_path": "src/new/module.py"},
            },
        ],
    ),
    "refactor_impact": ToolDocumentation(
        name="refactor_impact",
        description="Find all references to a symbol/file for impact analysis before removal.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "Removing deprecated code",
            "Finding all usages before deletion",
        ],
        when_not_to_use=[
            "When you want automatic deletion - this just finds references",
        ],
        hints_before=None,
        hints_after="Review references in the preview before manual cleanup.",
        commonly_preceded_by=["search"],
        commonly_followed_by=["write_source"],
        behavior=BehaviorFlags(has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Find references to deprecated function",
                "params": {"target": "deprecated_function"},
            },
        ],
    ),
    "refactor_inspect": ToolDocumentation(
        name="refactor_inspect",
        description="Inspect low-certainty matches in a file with surrounding context. Returns line numbers, snippets, and context_before/context_after for each match. Use to verify lexical matches are actual symbol references before applying.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "Reviewing low-certainty matches before applying refactor",
            "Verifying matches in files listed in low_certainty_files",
            "When verification_required is true in refactor preview",
        ],
        when_not_to_use=[
            "For high-certainty matches (definitions proven by index)",
        ],
        hints_before="Run refactor_rename/move/impact first. Check low_certainty_files in response to know which files need inspection.",
        hints_after="If matches look correct, use refactor_apply. If false positives found, use refactor_cancel and handle manually with write_source.",
        commonly_preceded_by=["refactor_rename", "refactor_move"],
        commonly_followed_by=["refactor_apply", "refactor_cancel"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Inspect matches in a file",
                "params": {"refactor_id": "abc123", "path": "src/module.py"},
            },
            {
                "description": "Inspect with more context lines",
                "params": {"refactor_id": "abc123", "path": "src/module.py", "context_lines": 5},
            },
        ],
    ),
    "refactor_apply": ToolDocumentation(
        name="refactor_apply",
        description="Apply a previewed refactoring atomically. All edits are applied or none. The refactor_id expires after apply or cancel.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "After reviewing preview and confirming matches are correct",
            "For unique identifiers where low-certainty matches are safe",
        ],
        when_not_to_use=[
            "Before checking verification_required in preview response",
            "If low_certainty_files contains files with potential false positives",
        ],
        hints_before="For common words (data, result, value), use refactor_inspect first. For unique identifiers, usually safe to apply directly.",
        hints_after="Run verify to confirm the changes compile and tests pass.",
        commonly_preceded_by=["refactor_inspect", "refactor_rename"],
        commonly_followed_by=["verify"],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=[],
        examples=[
            {
                "description": "Apply refactoring",
                "params": {"refactor_id": "abc123"},
            },
        ],
    ),
    "refactor_cancel": ToolDocumentation(
        name="refactor_cancel",
        description="Cancel a pending refactoring and discard the preview. Use when false positives are detected or you want to start over.",
        category=ToolCategory.REFACTOR,
        when_to_use=[
            "After finding false positives in refactor_inspect",
            "When you want to try different refactor parameters",
            "Cleaning up unused previews",
        ],
        when_not_to_use=[],
        hints_before=None,
        hints_after="If false positives exist, use write_source to make changes manually.",
        commonly_preceded_by=["refactor_inspect"],
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
    # =========================================================================
    # Introspection Tools
    # =========================================================================
    "describe": ToolDocumentation(
        name="describe",
        description="Introspection: describe tools, errors, capabilities, workflows, or operations.",
        category=ToolCategory.INTROSPECTION,
        when_to_use=[
            "Learning how to use a specific tool",
            "Understanding error codes",
            "Discovering available capabilities",
            "Debugging recent operations",
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
                "params": {"action": "tool", "name": "write_source"},
            },
            {
                "description": "Understand an error code",
                "params": {"action": "error", "code": "CONTENT_NOT_FOUND"},
            },
            {
                "description": "List all capabilities",
                "params": {"action": "capabilities"},
            },
            {
                "description": "View recent operations",
                "params": {"action": "operations", "limit": 10},
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
            "tools": ["map_repo", "search", "read_source"],
        },
        {
            "name": "modification",
            "description": "Making code changes",
            "tools": ["read_source", "write_source", "verify", "commit"],
        },
        {
            "name": "refactoring",
            "description": "Renaming and restructuring",
            "tools": ["search", "refactor_rename", "semantic_diff", "commit"],
        },
        {
            "name": "review",
            "description": "Reviewing changes",
            "tools": ["semantic_diff", "verify"],
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
