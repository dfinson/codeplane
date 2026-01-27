"""Git module error types."""


class GitError(Exception):
    """Base error for git operations."""

    pass


class NotARepositoryError(GitError):
    """Path is not a git repository."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Not a git repository: {path}")
        self.path = path


class RefNotFoundError(GitError):
    """Reference (branch, tag, commit) not found."""

    def __init__(self, ref: str) -> None:
        super().__init__(f"Reference not found: {ref}")
        self.ref = ref


class BranchExistsError(GitError):
    """Branch already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Branch already exists: {name}")
        self.name = name


class BranchNotFoundError(GitError):
    """Branch not found."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Branch not found: {name}")
        self.name = name


class UnmergedBranchError(GitError):
    """Branch has unmerged changes."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Branch has unmerged changes: {name}. Use force=True to delete.")
        self.name = name


class NothingToCommitError(GitError):
    """No staged changes to commit."""

    def __init__(self) -> None:
        super().__init__("Nothing to commit: no staged changes")


class ConflictError(GitError):
    """Merge/cherry-pick/revert resulted in conflicts."""

    def __init__(self, operation: str, paths: list[str]) -> None:
        super().__init__(f"{operation} resulted in conflicts: {', '.join(paths)}")
        self.operation = operation
        self.paths = paths


class DirtyWorkingTreeError(GitError):
    """Working tree has uncommitted changes."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"Cannot {operation}: working tree has uncommitted changes")
        self.operation = operation


class DetachedHeadError(GitError):
    """Operation requires a branch but HEAD is detached."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"Cannot {operation}: HEAD is detached")
        self.operation = operation


class RemoteError(GitError):
    """Error communicating with remote."""

    def __init__(self, remote: str, message: str) -> None:
        super().__init__(f"Remote error ({remote}): {message}")
        self.remote = remote


class AuthenticationError(GitError):
    """Authentication failed for remote operation."""

    def __init__(self, remote: str, operation: str | None = None) -> None:
        op_part = f" during {operation}" if operation else ""
        super().__init__(f"Authentication failed for remote {remote!r}{op_part}")
        self.remote = remote
        self.operation = operation


class StashError(GitError):
    """Stash operation failed."""

    pass


class StashNotFoundError(StashError):
    """Stash entry not found."""

    def __init__(self, index: int) -> None:
        super().__init__(f"Stash entry not found: stash@{{{index}}}")
        self.index = index


class NoStashEntriesError(StashError):
    """No stash entries exist."""

    def __init__(self) -> None:
        super().__init__("No stash entries")


# =============================================================================
# Worktree Errors
# =============================================================================


class WorktreeError(GitError):
    """Worktree operation failed."""

    pass


class WorktreeNotFoundError(WorktreeError):
    """Worktree not found."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Worktree not found: {name}")
        self.name = name


class WorktreeExistsError(WorktreeError):
    """Worktree already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Worktree already exists: {name}")
        self.name = name


class WorktreeLockedError(WorktreeError):
    """Worktree is locked."""

    def __init__(self, name: str, reason: str | None = None) -> None:
        reason_part = f": {reason}" if reason else ""
        super().__init__(f"Worktree is locked: {name}{reason_part}")
        self.name = name
        self.reason = reason


# =============================================================================
# Submodule Errors
# =============================================================================


class SubmoduleError(GitError):
    """Submodule operation failed."""

    pass


class SubmoduleNotFoundError(SubmoduleError):
    """Submodule not found."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Submodule not found: {path}")
        self.path = path


class SubmoduleNotInitializedError(SubmoduleError):
    """Submodule is not initialized."""

    def __init__(self, path: str) -> None:
        super().__init__(f"Submodule not initialized: {path}")
        self.path = path


# =============================================================================
# Rebase Errors
# =============================================================================


class RebaseError(GitError):
    """Rebase operation failed."""

    pass


class RebaseInProgressError(RebaseError):
    """A rebase is already in progress."""

    def __init__(self) -> None:
        super().__init__(
            "A rebase is already in progress. Use rebase_continue, rebase_skip, or rebase_abort."
        )


class NoRebaseInProgressError(RebaseError):
    """No rebase is in progress."""

    def __init__(self) -> None:
        super().__init__("No rebase in progress")


class RebaseConflictError(RebaseError):
    """Rebase resulted in conflicts."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(f"Rebase conflict in: {', '.join(paths)}")
        self.paths = paths
