"""Serializable data models for git operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Signature:
    """Git author/committer signature."""

    name: str
    email: str
    time: datetime


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """Git commit information."""

    sha: str
    short_sha: str
    message: str
    author: Signature
    committer: Signature
    parent_shas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BranchInfo:
    """Git branch information."""

    name: str
    short_name: str
    target_sha: str
    is_remote: bool
    upstream: str | None = None


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
    status: str  # 'added', 'deleted', 'modified', 'renamed', 'copied'
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
