"""Tiered documentation system for MCP tools.

Provides on-demand documentation without bloating ListTools response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolCategory(str, Enum):
    """Categories for grouping tools."""

    GIT = "git"
    FILES = "files"
    SEARCH = "search"
    MUTATION = "mutation"
    REFACTOR = "refactor"
    TESTING = "testing"
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
    "read_files": ToolDocumentation(
        name="read_files",
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
        commonly_followed_by=["mutate"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False, atomic=True),
        possible_errors=["FILE_NOT_FOUND", "ENCODING_ERROR", "INVALID_RANGE"],
        examples=[
            {
                "description": "Read a single file",
                "params": {"paths": ["src/main.py"]},
            },
            {
                "description": "Read lines 10-50 of a file",
                "params": {
                    "paths": ["src/main.py"],
                    "ranges": [{"start": 10, "end": 50}],
                },
            },
        ],
    ),
    "mutate": ToolDocumentation(
        name="mutate",
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
        hints_after="Consider git_stage if you want to commit the change.",
        alternatives=["refactor_rename (for symbol renames)"],
        commonly_preceded_by=["read_files"],
        commonly_followed_by=["git_stage", "git_commit"],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=[
            "CONTENT_NOT_FOUND",
            "MULTIPLE_MATCHES",
            "FILE_NOT_FOUND",
            "FILE_EXISTS",
            "HASH_MISMATCH",
        ],
        examples=[
            {
                "description": "Replace content exactly",
                "params": {
                    "edits": [
                        {
                            "path": "src/foo.py",
                            "action": "update",
                            "mode": "exact",
                            "old_content": "def old_name():\n    pass",
                            "new_content": "def new_name():\n    pass",
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
        hints_after="Use read_files to dive into specific files of interest.",
        alternatives=["search"],
        commonly_preceded_by=[],
        commonly_followed_by=["read_files", "search"],
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
        description="Search code, symbols, or references.",
        category=ToolCategory.SEARCH,
        when_to_use=[
            "Finding where a function is defined",
            "Finding usages of a symbol",
            "Text search across codebase",
        ],
        when_not_to_use=[
            "When you know the exact file - use read_files",
        ],
        hints_before=None,
        hints_after="Use read_files to get full context around matches.",
        alternatives=["map_repo (for structure overview)"],
        commonly_preceded_by=["map_repo"],
        commonly_followed_by=["read_files"],
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
        ],
    ),
    "git_status": ToolDocumentation(
        name="git_status",
        description="Get repository status.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Checking for uncommitted changes",
            "Seeing which branch you're on",
            "Before making commits",
        ],
        when_not_to_use=[],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
    ),
    "git_commit": ToolDocumentation(
        name="git_commit",
        description="Create a commit.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Saving changes to version control",
            "After completing a logical unit of work",
        ],
        when_not_to_use=[
            "Nothing is staged - stage files first or pass paths",
        ],
        hints_before="Use git_status to see what will be committed.",
        hints_after="Use git_push to share the commit.",
        commonly_preceded_by=["git_stage", "mutate"],
        commonly_followed_by=["git_push"],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=["DIRTY_WORKING_TREE"],
    ),
    "git_stage": ToolDocumentation(
        name="git_stage",
        description="Stage files for commit.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Preparing files for commit",
            "After making changes with mutate",
        ],
        when_not_to_use=[],
        commonly_preceded_by=["mutate"],
        commonly_followed_by=["git_commit"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["FILE_NOT_FOUND"],
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
            "tools": ["map_repo", "search", "read_files"],
        },
        {
            "name": "modification",
            "description": "Making code changes",
            "tools": ["read_files", "mutate", "git_stage", "git_commit"],
        },
        {
            "name": "refactoring",
            "description": "Renaming and restructuring",
            "tools": ["search", "refactor_rename", "git_diff", "git_commit"],
        },
        {
            "name": "review",
            "description": "Reviewing changes",
            "tools": ["git_status", "git_diff", "git_log"],
        },
    ]
