"""Reusable transactional patterns for write operations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pygit2

from codeplane.git._internal.access import RepoAccess


@dataclass(frozen=True, slots=True)
class ConflictCheckResult:
    """Result of an operation that may produce conflicts."""

    has_conflicts: bool
    conflict_paths: tuple[str, ...]


class WriteFlows:
    """Reusable transactional patterns for git write operations."""

    def __init__(self, access: RepoAccess) -> None:
        self._access = access

    def extract_conflict_paths(self) -> tuple[str, ...]:
        """Extract unique conflict paths from index."""
        paths: set[str] = set()
        for ancestor, ours, theirs in self._access.index.conflicts:
            for entry in (ancestor, ours, theirs):
                if entry:
                    paths.add(entry.path)
        return tuple(sorted(paths))

    def check_conflicts(self) -> ConflictCheckResult:
        """
        Check if index has conflicts and extract paths.

        Returns:
            ConflictCheckResult with:
            - has_conflicts: True if any conflicts exist
            - conflict_paths: Sorted tuple of unique paths with conflicts

        Contract: Non-destructive read. Does not modify index or resolve conflicts.
        """
        if self._access.index.conflicts:
            return ConflictCheckResult(True, self.extract_conflict_paths())
        return ConflictCheckResult(False, ())

    def write_tree_and_commit(
        self,
        message: str,
        parents: list[pygit2.Oid],
        *,
        author: pygit2.Signature | None = None,
    ) -> str:
        """
        Write index tree and create commit. Returns sha.

        Contract: Uses passed parents verbatim - does NOT re-read HEAD.
        Caller is responsible for capturing HEAD oid before any mutations.
        """
        tree_id = self._access.index.write_tree()
        sig = self._access.default_signature
        oid = self._access.create_commit(
            "HEAD",
            author or sig,
            sig,
            message,
            tree_id,
            parents,
        )
        return str(oid)

    def commit_from_index(self, message: str) -> str:
        """Create commit from current index state. Returns sha."""
        parents = [] if self._access.is_unborn else [self._access.head_target]
        return self.write_tree_and_commit(message, parents)

    @contextmanager
    def stateful_op(self) -> Iterator[None]:
        """
        Context manager that guarantees state cleanup after stateful operations.

        IMPORTANT: This only calls state_cleanup() which clears merge/cherrypick/revert
        state files (MERGE_HEAD, CHERRY_PICK_HEAD, etc). It does NOT reset the index
        or working tree. For abort semantics, callers must explicitly reset after cleanup.
        """
        try:
            yield
        finally:
            self._access.state_cleanup()

    def run_merge_like_operation(
        self,
        operation_fn: Callable[[], None],
        message: str,
        parents: list[pygit2.Oid],
        *,
        author: pygit2.Signature | None = None,
    ) -> tuple[bool, str | None, tuple[str, ...]]:
        """
        Run merge-like operation with guaranteed cleanup.

        Returns (success, commit_sha_or_none, conflict_paths).
        """
        with self.stateful_op():
            operation_fn()
            conflicts = self.check_conflicts()
            if conflicts.has_conflicts:
                return False, None, conflicts.conflict_paths

            sha = self.write_tree_and_commit(message, parents, author=author)
            return True, sha, ()
