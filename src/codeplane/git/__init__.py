"""Git operations module."""

from codeplane.git.credentials import SystemCredentialCallback, get_default_callbacks
from codeplane.git.errors import (
    AuthenticationError,
    BranchExistsError,
    BranchNotFoundError,
    ConflictError,
    DetachedHeadError,
    DirtyWorkingTreeError,
    GitError,
    NoStashEntriesError,
    NotARepositoryError,
    NothingToCommitError,
    RefNotFoundError,
    RemoteError,
    StashNotFoundError,
    UnmergedBranchError,
)
from codeplane.git.ops import GitOps

__all__ = [
    # Main class
    "GitOps",
    # Credentials
    "SystemCredentialCallback",
    "get_default_callbacks",
    # Errors
    "GitError",
    "NotARepositoryError",
    "RefNotFoundError",
    "BranchExistsError",
    "BranchNotFoundError",
    "ConflictError",
    "DirtyWorkingTreeError",
    "NothingToCommitError",
    "AuthenticationError",
    "RemoteError",
    "StashNotFoundError",
    "NoStashEntriesError",
    "DetachedHeadError",
    "UnmergedBranchError",
]
