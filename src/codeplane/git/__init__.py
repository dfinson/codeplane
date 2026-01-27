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
from codeplane.git.models import (
    BlameHunk,
    BlameInfo,
    BranchInfo,
    CommitInfo,
    DiffFile,
    DiffInfo,
    MergeAnalysis,
    MergeResult,
    OperationResult,
    RefInfo,
    RemoteInfo,
    Signature,
    StashEntry,
    TagInfo,
)
from codeplane.git.ops import GitOps

__all__ = [
    # Main class
    "GitOps",
    # Models
    "Signature",
    "CommitInfo",
    "BranchInfo",
    "TagInfo",
    "RemoteInfo",
    "DiffFile",
    "DiffInfo",
    "BlameHunk",
    "BlameInfo",
    "StashEntry",
    "RefInfo",
    "MergeResult",
    "MergeAnalysis",
    "OperationResult",
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
