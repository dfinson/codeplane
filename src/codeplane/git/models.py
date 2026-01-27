"""Serializable data models for git operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import pygit2

DeltaStatus = Literal["added", "deleted", "modified", "renamed", "copied", "unknown"]

_DELTA_STATUS_MAP: dict[int, DeltaStatus] = {
    pygit2.GIT_DELTA_ADDED: "added",
    pygit2.GIT_DELTA_DELETED: "deleted",
    pygit2.GIT_DELTA_MODIFIED: "modified",
    pygit2.GIT_DELTA_RENAMED: "renamed",
    pygit2.GIT_DELTA_COPIED: "copied",
}


@dataclass(frozen=True, slots=True)
class Signature:
    """Git author/committer signature."""

    name: str
    email: str
    time: datetime

    @classmethod
    def from_pygit2(cls, sig: pygit2.Signature) -> Signature:
        return cls(sig.name, sig.email, datetime.fromtimestamp(sig.time, tz=UTC))


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """Git commit information."""

    sha: str
    short_sha: str
    message: str
    author: Signature
    committer: Signature
    parent_shas: tuple[str, ...]

    @classmethod
    def from_pygit2(cls, commit: pygit2.Commit) -> CommitInfo:
        sha = str(commit.id)
        return cls(
            sha=sha,
            short_sha=sha[:7],
            message=commit.message,
            author=Signature.from_pygit2(commit.author),
            committer=Signature.from_pygit2(commit.committer),
            parent_shas=tuple(str(p) for p in commit.parent_ids),
        )


@dataclass(frozen=True, slots=True)
class BranchInfo:
    """Git branch information."""

    name: str
    short_name: str
    target_sha: str
    is_remote: bool
    upstream: str | None = None

    @classmethod
    def from_pygit2(cls, branch: pygit2.Branch) -> BranchInfo:
        upstream = None
        try:
            if branch.upstream:
                upstream = branch.upstream.shorthand
        except ValueError:
            # Remote branches raise ValueError when accessing .upstream
            pass
        return cls(
            name=branch.name,
            short_name=branch.shorthand,
            target_sha=str(branch.target),
            is_remote=branch.name.startswith("refs/remotes/"),
            upstream=upstream,
        )


@dataclass(frozen=True, slots=True)
class TagInfo:
    """Git tag information."""

    name: str
    target_sha: str
    is_annotated: bool
    message: str | None = None
    tagger: Signature | None = None


@dataclass(frozen=True, slots=True)
class RemoteInfo:
    """Git remote information."""

    name: str
    url: str
    push_url: str | None = None


@dataclass(frozen=True, slots=True)
class DiffFile:
    """Single file in a diff."""

    old_path: str | None
    new_path: str | None
    status: DeltaStatus
    additions: int
    deletions: int


@dataclass(frozen=True, slots=True)
class DiffInfo:
    """Git diff summary."""

    files: tuple[DiffFile, ...]
    total_additions: int
    total_deletions: int
    files_changed: int
    patch: str | None = None

    @classmethod
    def from_pygit2(cls, diff: pygit2.Diff, include_patch: bool = False) -> DiffInfo:
        files = tuple(
            DiffFile(
                old_path=delta.old_file.path if delta.old_file else None,
                new_path=delta.new_file.path if delta.new_file else None,
                status=_DELTA_STATUS_MAP.get(delta.status, "unknown"),
                additions=0,
                deletions=0,
            )
            for delta in diff.deltas
        )
        stats = diff.stats
        return cls(
            files=files,
            total_additions=stats.insertions,
            total_deletions=stats.deletions,
            files_changed=stats.files_changed,
            patch=diff.patch if include_patch else None,
        )


@dataclass(frozen=True, slots=True)
class BlameHunk:
    """A hunk in blame output."""

    commit_sha: str
    author: Signature
    start_line: int
    line_count: int
    original_start_line: int


@dataclass(frozen=True, slots=True)
class BlameInfo:
    """Git blame result."""

    path: str
    hunks: tuple[BlameHunk, ...]

    @classmethod
    def from_pygit2(cls, path: str, blame: pygit2.Blame) -> BlameInfo:
        return cls(
            path=path,
            hunks=tuple(
                BlameHunk(
                    commit_sha=str(hunk.final_commit_id),
                    author=Signature.from_pygit2(hunk.final_committer),  # type: ignore[arg-type]
                    start_line=hunk.final_start_line_number,
                    line_count=hunk.lines_in_hunk,
                    original_start_line=hunk.orig_start_line_number,
                )
                for hunk in blame
            ),
        )


@dataclass(frozen=True, slots=True)
class StashEntry:
    """Git stash entry."""

    index: int
    message: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class RefInfo:
    """Reference information (HEAD, etc)."""

    name: str
    target_sha: str
    shorthand: str
    is_detached: bool = False


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Result of a merge operation."""

    success: bool
    commit_sha: str | None
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MergeAnalysis:
    """Result of merge analysis."""

    up_to_date: bool
    fastforward_possible: bool
    conflicts_likely: bool


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Result of cherrypick/revert operations."""

    success: bool
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)


# =============================================================================
# Worktree Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    """Git worktree information."""

    name: str
    path: str
    head_ref: str  # Branch name or "HEAD" if detached
    head_sha: str
    is_main: bool  # True for main working directory
    is_bare: bool
    is_locked: bool
    lock_reason: str | None
    is_prunable: bool  # True if worktree directory is missing


# =============================================================================
# Submodule Types
# =============================================================================

SubmoduleState = Literal[
    "uninitialized",  # In .gitmodules but not cloned
    "clean",  # Initialized, at recorded commit
    "dirty",  # Has local modifications
    "outdated",  # Behind recorded commit
    "missing",  # Directory missing
]


@dataclass(frozen=True, slots=True)
class SubmoduleInfo:
    """Git submodule information."""

    name: str
    path: str
    url: str
    branch: str | None
    head_sha: str | None
    status: SubmoduleState


@dataclass(frozen=True, slots=True)
class SubmoduleStatus:
    """Detailed submodule status."""

    info: SubmoduleInfo
    workdir_dirty: bool
    index_dirty: bool
    untracked_count: int
    recorded_sha: str
    actual_sha: str | None


@dataclass(frozen=True, slots=True)
class SubmoduleUpdateResult:
    """Result of submodule update operation."""

    updated: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]  # (path, error)
    already_current: tuple[str, ...]


# =============================================================================
# Rebase Types
# =============================================================================

RebaseAction = Literal["pick", "reword", "edit", "squash", "fixup", "drop"]
RebaseState = Literal["done", "conflict", "edit_pause", "aborted"]


@dataclass(frozen=True, slots=True)
class RebaseStep:
    """A single step in a rebase plan."""

    action: RebaseAction
    commit_sha: str
    message: str | None = None  # Original message, or override for reword/squash


@dataclass(frozen=True, slots=True)
class RebasePlan:
    """A rebase plan ready for execution."""

    upstream: str
    onto: str
    steps: tuple[RebaseStep, ...]


@dataclass(frozen=True, slots=True)
class RebaseResult:
    """Result of a rebase operation."""

    success: bool
    completed_steps: int
    total_steps: int
    state: RebaseState
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)
    current_commit: str | None = None  # For edit_pause
    new_head: str | None = None  # Final HEAD after successful rebase
