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
        commonly_followed_by=["write_files"],
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
                    "ranges": [{"path": "src/main.py", "start_line": 10, "end_line": 50}],
                },
            },
        ],
    ),
    "write_files": ToolDocumentation(
        name="write_files",
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
        description="Search code, symbols, or references with configurable context.",
        category=ToolCategory.SEARCH,
        when_to_use=[
            "Finding where a function is defined",
            "Finding usages of a symbol",
            "Text search across codebase",
            "Getting edit-ready code snippets with context=function/class",
        ],
        when_not_to_use=[
            "When you know the exact file - use read_files",
        ],
        hints_before=None,
        hints_after="Use context='function' or 'class' for edit-ready results. Use context='rich' for 20 lines. Only use read_files if you need more context than search provides.",
        alternatives=["map_repo (for structure overview)"],
        commonly_preceded_by=["map_repo"],
        commonly_followed_by=["write_files", "read_files"],
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
                "params": {"query": "handle_request", "context": "function"},
            },
            {
                "description": "Search with enclosing class body",
                "params": {"query": "UserService", "mode": "symbol", "context": "class"},
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
        description="Create a commit from already-staged changes.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Committing when files are already staged via git_stage",
            "Low-level control over staging and committing separately",
        ],
        when_not_to_use=[
            "Nothing is staged - stage files first or use git_stage_and_commit",
            "Typical commit workflow - prefer git_stage_and_commit",
        ],
        hints_before="Prefer git_stage_and_commit for most workflows. Use git_commit only when you need granular staging control.",
        hints_after="Use git_push to share the commit.",
        alternatives=["git_stage_and_commit (preferred for most workflows)"],
        commonly_preceded_by=["git_stage", "write_files"],
        commonly_followed_by=["git_push"],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=["DIRTY_WORKING_TREE", "HOOK_FAILED", "HOOK_FAILED_AFTER_RETRY"],
    ),
    "git_stage_and_commit": ToolDocumentation(
        name="git_stage_and_commit",
        description="Stage files and create a commit in one step. Handles pre-commit hook auto-fixes automatically.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Standard commit workflow — stage and commit in one tool call",
            "After completing a logical unit of work",
            "When pre-commit hooks may auto-fix files (formatters, linters)",
        ],
        when_not_to_use=[
            "When you need to stage selectively (add some, remove others) — use git_stage + git_commit",
            "When nothing should be staged (committing already-staged changes) — use git_commit",
        ],
        hints_before="Use git_status to verify which files will be staged.",
        hints_after="Use git_push to share the commit.",
        alternatives=["git_stage + git_commit (for granular staging control)"],
        commonly_preceded_by=["write_files", "lint_check"],
        commonly_followed_by=["git_push"],
        behavior=BehaviorFlags(has_side_effects=True, atomic=True),
        possible_errors=["HOOK_FAILED", "HOOK_FAILED_AFTER_RETRY"],
    ),
    "git_stage": ToolDocumentation(
        name="git_stage",
        description="Stage or unstage files. Use for granular staging control.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Selectively staging or unstaging files",
            "Discarding changes to specific files",
            "Staging all changes at once",
        ],
        when_not_to_use=[
            "Standard commit workflow — prefer git_stage_and_commit",
        ],
        alternatives=["git_stage_and_commit (preferred for stage + commit workflow)"],
        commonly_preceded_by=["write_files"],
        commonly_followed_by=["git_commit"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["FILE_NOT_FOUND"],
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
            "When you need file contents - use read_files",
            "When you need repository overview - use map_repo",
        ],
        hints_before=None,
        hints_after="Use read_files to examine specific files of interest.",
        alternatives=["map_repo (for tree structure)"],
        commonly_preceded_by=["map_repo"],
        commonly_followed_by=["read_files"],
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
    # Git Tools
    # =========================================================================
    "git_diff": ToolDocumentation(
        name="git_diff",
        description="Get diff between refs or working tree.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Reviewing changes before commit",
            "Comparing branches",
            "Seeing staged changes",
        ],
        when_not_to_use=[
            "When you need current status - use git_status",
        ],
        hints_before=None,
        hints_after="Use git_stage if changes look correct.",
        commonly_preceded_by=["git_status"],
        commonly_followed_by=["git_stage", "git_commit"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=["REF_NOT_FOUND"],
        examples=[
            {
                "description": "Show staged changes",
                "params": {"staged": True},
            },
            {
                "description": "Compare branches",
                "params": {"base": "main", "target": "feature-branch"},
            },
        ],
    ),
    "git_log": ToolDocumentation(
        name="git_log",
        description="Get commit history.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Viewing commit history",
            "Finding when a change was made",
            "Reviewing recent commits",
        ],
        when_not_to_use=[
            "When you need blame info - use git_inspect with action='blame'",
        ],
        hints_before=None,
        hints_after="Use git_inspect with action='show' for full commit details.",
        commonly_preceded_by=["git_status"],
        commonly_followed_by=["git_inspect"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=["REF_NOT_FOUND"],
        examples=[
            {
                "description": "Get last 10 commits",
                "params": {"limit": 10},
            },
            {
                "description": "Filter by path",
                "params": {"paths": ["src/main.py"], "limit": 20},
            },
        ],
    ),
    "git_push": ToolDocumentation(
        name="git_push",
        description="Push to remote.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Sharing commits with remote",
            "After completing a feature",
        ],
        when_not_to_use=[
            "Before committing - commit first",
            "When remote is ahead - pull first",
        ],
        hints_before="Ensure all tests pass and commits are ready.",
        hints_after=None,
        commonly_preceded_by=["git_commit"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["REF_NOT_FOUND"],
        examples=[
            {
                "description": "Push to origin",
                "params": {},
            },
        ],
    ),
    "git_pull": ToolDocumentation(
        name="git_pull",
        description="Pull from remote.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Getting latest changes",
            "Before starting new work",
        ],
        when_not_to_use=[
            "When you have uncommitted changes - stash or commit first",
        ],
        hints_before="Check git_status to ensure clean working tree.",
        hints_after=None,
        commonly_preceded_by=["git_status"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["MERGE_CONFLICT"],
        examples=[
            {
                "description": "Pull from origin",
                "params": {},
            },
        ],
    ),
    "git_checkout": ToolDocumentation(
        name="git_checkout",
        description="Checkout a ref.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Switching branches",
            "Creating new branch",
            "Checking out a commit",
        ],
        when_not_to_use=[
            "When you have uncommitted changes - stash or commit first",
        ],
        hints_before="Check git_status to ensure clean working tree.",
        hints_after=None,
        commonly_preceded_by=["git_status", "git_branch"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["REF_NOT_FOUND", "DIRTY_WORKING_TREE"],
        examples=[
            {
                "description": "Switch to existing branch",
                "params": {"ref": "main"},
            },
            {
                "description": "Create and switch to new branch",
                "params": {"ref": "feature/new-feature", "create": True},
            },
        ],
    ),
    "git_merge": ToolDocumentation(
        name="git_merge",
        description="Merge a branch.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Incorporating changes from another branch",
            "Completing a feature branch",
        ],
        when_not_to_use=[
            "When you have uncommitted changes - commit first",
            "When rebasing is preferred",
        ],
        hints_before="Ensure target branch is up to date with remote.",
        hints_after="Push the merge commit to share.",
        commonly_preceded_by=["git_checkout", "git_pull"],
        commonly_followed_by=["git_push"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["MERGE_CONFLICT", "REF_NOT_FOUND"],
        examples=[
            {
                "description": "Merge feature branch",
                "params": {"ref": "feature/my-feature"},
            },
        ],
    ),
    "git_reset": ToolDocumentation(
        name="git_reset",
        description="Reset HEAD to a ref. Hard reset requires two-phase confirmation.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Undoing commits (soft/mixed)",
            "Discarding changes (hard) - requires explicit user approval",
            "Unstaging files (mixed)",
        ],
        when_not_to_use=[
            "When changes are pushed - use revert instead",
            "When unsure - hard reset loses changes permanently",
            "Without explicit user approval for hard reset",
        ],
        hints_before="For hard reset: MUST obtain user approval. First call returns confirmation token; second call with token executes.",
        hints_after=None,
        commonly_preceded_by=["git_log", "git_status"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["REF_NOT_FOUND", "INVALID_CONFIRMATION", "TOKEN_MISMATCH"],
        examples=[
            {
                "description": "Soft reset to undo last commit",
                "params": {"ref": "HEAD~1", "mode": "soft"},
            },
            {
                "description": "Hard reset phase 1 - get confirmation token",
                "params": {"ref": "HEAD~1", "mode": "hard"},
            },
            {
                "description": "Hard reset phase 2 - execute with token",
                "params": {
                    "ref": "HEAD~1",
                    "mode": "hard",
                    "confirmation_token": "<token_from_phase_1>",
                },
            },
        ],
    ),
    "git_branch": ToolDocumentation(
        name="git_branch",
        description="Manage branches.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Listing branches",
            "Creating new branches",
            "Deleting old branches",
        ],
        when_not_to_use=[
            "When switching branches - use git_checkout",
        ],
        hints_before=None,
        hints_after="Use git_checkout to switch to a created branch.",
        commonly_preceded_by=[],
        commonly_followed_by=["git_checkout"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["REF_NOT_FOUND"],
        examples=[
            {
                "description": "List all branches",
                "params": {"action": "list"},
            },
            {
                "description": "Create a new branch",
                "params": {"action": "create", "name": "feature/new"},
            },
        ],
    ),
    "git_remote": ToolDocumentation(
        name="git_remote",
        description="Manage remotes.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Listing remotes",
            "Fetching updates from remote",
            "Listing tags",
        ],
        when_not_to_use=[
            "When pushing/pulling - use git_push/git_pull",
        ],
        hints_before=None,
        hints_after=None,
        commonly_preceded_by=[],
        commonly_followed_by=["git_pull", "git_checkout"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=[],
        examples=[
            {
                "description": "Fetch from origin",
                "params": {"action": "fetch"},
            },
        ],
    ),
    "git_stash": ToolDocumentation(
        name="git_stash",
        description="Manage stash.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Temporarily saving uncommitted changes",
            "Switching branches with changes",
        ],
        when_not_to_use=[
            "When changes should be committed",
        ],
        hints_before=None,
        hints_after="Remember to pop the stash when ready.",
        commonly_preceded_by=["git_status"],
        commonly_followed_by=["git_checkout"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=[],
        examples=[
            {
                "description": "Save changes to stash",
                "params": {"action": "push", "message": "WIP: feature work"},
            },
            {
                "description": "Restore stashed changes",
                "params": {"action": "pop"},
            },
        ],
    ),
    "git_rebase": ToolDocumentation(
        name="git_rebase",
        description="Manage rebase.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Updating feature branch with latest main",
            "Cleaning up commit history",
        ],
        when_not_to_use=[
            "When branch is shared/pushed - use merge instead",
            "When merge conflicts are complex",
        ],
        hints_before="Ensure working tree is clean before rebasing.",
        hints_after="Force push may be required after rebase.",
        commonly_preceded_by=["git_status", "git_remote"],
        commonly_followed_by=["git_push"],
        behavior=BehaviorFlags(has_side_effects=True, may_be_slow=True),
        possible_errors=["MERGE_CONFLICT", "REF_NOT_FOUND"],
        examples=[
            {
                "description": "Rebase onto main",
                "params": {"action": "plan", "upstream": "main"},
            },
        ],
    ),
    "git_inspect": ToolDocumentation(
        name="git_inspect",
        description="Inspect commits or blame.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Viewing full commit details",
            "Finding who changed a line (blame)",
        ],
        when_not_to_use=[
            "When you need commit list - use git_log",
        ],
        hints_before=None,
        hints_after=None,
        commonly_preceded_by=["git_log"],
        commonly_followed_by=["read_files"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=["REF_NOT_FOUND", "FILE_NOT_FOUND"],
        examples=[
            {
                "description": "Show commit details",
                "params": {"action": "show", "ref": "abc1234"},
            },
            {
                "description": "Get blame for lines",
                "params": {
                    "action": "blame",
                    "path": "src/main.py",
                    "start_line": 10,
                    "end_line": 20,
                },
            },
        ],
    ),
    "git_history": ToolDocumentation(
        name="git_history",
        description="Amend, cherry-pick, or revert commits.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Amending the last commit",
            "Cherry-picking specific commits",
            "Reverting pushed commits",
        ],
        when_not_to_use=[
            "When commit is already pushed - amend requires force push",
        ],
        hints_before="For amend, stage your changes first.",
        hints_after=None,
        commonly_preceded_by=["git_stage"],
        commonly_followed_by=["git_push"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["REF_NOT_FOUND", "MERGE_CONFLICT"],
        examples=[
            {
                "description": "Amend last commit message",
                "params": {"action": "amend", "message": "Better commit message"},
            },
            {
                "description": "Cherry-pick a commit",
                "params": {"action": "cherrypick", "commit": "abc1234"},
            },
        ],
    ),
    "git_submodule": ToolDocumentation(
        name="git_submodule",
        description="Manage submodules.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Adding external dependencies as submodules",
            "Updating submodule references",
            "Initializing submodules after clone",
        ],
        when_not_to_use=[
            "When package managers can handle dependency",
        ],
        hints_before=None,
        hints_after=None,
        commonly_preceded_by=[],
        commonly_followed_by=["git_commit"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["FILE_NOT_FOUND"],
        examples=[
            {
                "description": "List submodules",
                "params": {"action": "list"},
            },
            {
                "description": "Update all submodules",
                "params": {"action": "update", "recursive": True},
            },
        ],
    ),
    "git_worktree": ToolDocumentation(
        name="git_worktree",
        description="Manage worktrees.",
        category=ToolCategory.GIT,
        when_to_use=[
            "Working on multiple branches simultaneously",
            "Testing changes without affecting main worktree",
        ],
        when_not_to_use=[
            "Simple branch switching - use git_checkout",
        ],
        hints_before=None,
        hints_after=None,
        commonly_preceded_by=[],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=["REF_NOT_FOUND"],
        examples=[
            {
                "description": "Add worktree for branch",
                "params": {"action": "add", "path": "../feature-work", "ref": "feature/branch"},
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
            "Simple find/replace in one file - use write_files",
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
            {
                "description": "Rename using file:line:col locator",
                "params": {"symbol": "src/utils.py:42:5", "new_name": "better_name"},
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
    "refactor_delete": ToolDocumentation(
        name="refactor_delete",
        description="Find all references to a symbol/file for manual cleanup.",
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
        commonly_followed_by=["write_files"],
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
        hints_before="Run refactor_rename/move/delete first. Check low_certainty_files in response to know which files need inspection.",
        hints_after="If matches look correct, use refactor_apply. If false positives found, use refactor_cancel and handle manually with write_files.",
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
        hints_after="Run lint_check and run_test_targets to verify the changes compile and tests pass.",
        commonly_preceded_by=["refactor_inspect", "refactor_rename"],
        commonly_followed_by=["lint_check", "run_test_targets"],
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
        hints_after="If false positives exist, use write_files to make changes manually.",
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
    # Lint Tools
    # =========================================================================
    "lint_check": ToolDocumentation(
        name="lint_check",
        description="Run linters, formatters, and type checkers.",
        category=ToolCategory.LINT,
        when_to_use=[
            "Before committing changes",
            "After making code modifications",
            "Checking code quality",
        ],
        when_not_to_use=[
            "When you only want to see available tools - use lint_tools",
        ],
        hints_before=None,
        hints_after="Fix reported issues before committing.",
        commonly_preceded_by=["write_files"],
        commonly_followed_by=["run_test_targets", "git_commit"],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=[],
        examples=[
            {
                "description": "Lint entire repo",
                "params": {},
            },
            {
                "description": "Lint specific files (dry run)",
                "params": {"paths": ["src/main.py"], "dry_run": True},
            },
        ],
    ),
    "lint_tools": ToolDocumentation(
        name="lint_tools",
        description="List available lint tools and their detection status.",
        category=ToolCategory.LINT,
        when_to_use=[
            "Checking which linters are available",
            "Verifying tool configuration",
        ],
        when_not_to_use=[
            "When you want to run checks - use lint_check",
        ],
        hints_before=None,
        hints_after="Use lint_check with specific tools if needed.",
        commonly_preceded_by=[],
        commonly_followed_by=["lint_check"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "List all detected tools",
                "params": {},
            },
            {
                "description": "Filter by language",
                "params": {"language": "python"},
            },
        ],
    ),
    # =========================================================================
    # Testing Tools
    # =========================================================================
    "discover_test_targets": ToolDocumentation(
        name="discover_test_targets",
        description="Find test targets in the repository.",
        category=ToolCategory.TESTING,
        when_to_use=[
            "Finding available tests before running",
            "Understanding test organization",
        ],
        when_not_to_use=[
            "When you already know the test target IDs",
        ],
        hints_before=None,
        hints_after="Use run_test_targets with specific target IDs.",
        commonly_preceded_by=["map_repo"],
        commonly_followed_by=["run_test_targets"],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Discover all tests",
                "params": {},
            },
            {
                "description": "Discover tests in specific path",
                "params": {"paths": ["tests/unit"]},
            },
        ],
    ),
    "run_test_targets": ToolDocumentation(
        name="run_test_targets",
        description="Execute tests.",
        category=ToolCategory.TESTING,
        when_to_use=[
            "Running tests after code changes",
            "Verifying fixes",
            "Running specific test subsets",
        ],
        when_not_to_use=[
            "When you need to find tests first - use discover_test_targets",
        ],
        hints_before="Use discover_test_targets to get target IDs.",
        hints_after="Use get_test_run_status to monitor progress.",
        commonly_preceded_by=["discover_test_targets", "lint_check"],
        commonly_followed_by=["get_test_run_status"],
        behavior=BehaviorFlags(has_side_effects=True, may_be_slow=True),
        possible_errors=[],
        examples=[
            {
                "description": "Run all tests",
                "params": {},
            },
            {
                "description": "Run specific targets with coverage",
                "params": {"targets": ["test:tests/unit/test_main.py"], "coverage": True},
            },
            {
                "description": "Run tests matching path pattern",
                "params": {"target_filter": "test_api"},
            },
        ],
    ),
    "get_test_run_status": ToolDocumentation(
        name="get_test_run_status",
        description="Check progress of a running test.",
        category=ToolCategory.TESTING,
        when_to_use=[
            "Monitoring test progress",
            "Getting final test results",
        ],
        when_not_to_use=[],
        hints_before="Get run_id from run_test_targets response.",
        hints_after=None,
        commonly_preceded_by=["run_test_targets"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(idempotent=True, has_side_effects=False),
        possible_errors=[],
        examples=[
            {
                "description": "Check test status",
                "params": {"run_id": "run_abc123"},
            },
        ],
    ),
    "cancel_test_run": ToolDocumentation(
        name="cancel_test_run",
        description="Abort a running test execution.",
        category=ToolCategory.TESTING,
        when_to_use=[
            "Stopping long-running tests",
            "Cancelling after finding a critical failure",
        ],
        when_not_to_use=[
            "When tests have already completed",
        ],
        hints_before=None,
        hints_after=None,
        commonly_preceded_by=["get_test_run_status"],
        commonly_followed_by=[],
        behavior=BehaviorFlags(has_side_effects=True),
        possible_errors=[],
        examples=[
            {
                "description": "Cancel test run",
                "params": {"run_id": "run_abc123"},
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
                "params": {"action": "tool", "name": "write_files"},
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
            "tools": ["map_repo", "search", "read_files"],
        },
        {
            "name": "modification",
            "description": "Making code changes",
            "tools": ["read_files", "write_files", "git_stage_and_commit"],
        },
        {
            "name": "refactoring",
            "description": "Renaming and restructuring",
            "tools": ["search", "refactor_rename", "git_diff", "git_stage_and_commit"],
        },
        {
            "name": "review",
            "description": "Reviewing changes",
            "tools": ["git_status", "git_diff", "git_log"],
        },
    ]
