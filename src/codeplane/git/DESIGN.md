# Git Module — Design Spec

## Table of Contents

- [Scope](#scope)
  - [Responsibilities](#responsibilities)
  - [From SPEC.md](#from-specmd)
- [pygit2 Capability Assessment](#pygit2-capability-assessment)
  - [Credential Handling](#credential-handling)
- [Design Options](#design-options)
  - [Option A: Pure pygit2 (Recommended)](#option-a-pure-pygit2-recommended)
  - [Option B: Pure pygit2 (Local Only)](#option-b-pure-pygit2-local-only)
  - [Option C: pygit2 with Subprocess Fallback](#option-c-pygit2-with-subprocess-fallback)
- [Recommended Approach](#recommended-approach)
- [File Plan](#file-plan)
- [Dependencies](#dependencies)
- [Key Interfaces](#key-interfaces)
  - [GitOps Class](#gitops-class)
  - [Types](#types)
- [Credential Handling](#credential-handling-1)
- [Open Questions](#open-questions)
- [Interactive Rebase](#interactive-rebase)
  - [Design: Hybrid Plan-Based](#design-hybrid-plan-based)
  - [Interface](#interface)
  - [Types](#types-1)
  - [Implementation](#implementation)
- [Submodule Management](#submodule-management)
  - [Design: Full Management with Subprocess Fallback](#design-full-management-with-subprocess-fallback)
  - [Interface](#interface-1)
  - [Types](#types-2)
  - [Implementation Strategy](#implementation-strategy)
- [Worktree Support](#worktree-support)
  - [Design: GitOps-Returning Worktrees](#design-gitops-returning-worktrees)
  - [Interface](#interface-2)
  - [Types](#types-3)
  - [Usage Pattern](#usage-pattern)
- [Test Strategy](#test-strategy)

---

## Scope

The git module provides **comprehensive, structured Git operations** via `pygit2`. If an agent wants to mutate Git state, it should be able to. CodePlane exposes the full power of Git programmatically.

### Responsibilities

**Read Operations:**
- Status (staged, modified, untracked, conflicts)
- Diff (working tree, staged, between refs)
- Blame (line-level attribution)
- Log (commit history, revision walking)
- HEAD, branch, and ref information
- Merge analysis (fast-forward detection, conflict prediction)
- Describe (human-readable commit names)

**Write Operations:**
- Stage/unstage files
- Commit (with author/committer control)
- Amend commits
- Branch management (create, checkout, delete, rename)
- Reset (soft, mixed, hard)
- Stash (push, pop, list, drop)
- Merge (with conflict detection)
- Cherry-pick
- Revert
- Tag management
- Remote operations (fetch, push, pull) with credential support

### From SPEC.md

- §10: Git and file operations
- §9.7: Git behavior (mutation engine)

---

## pygit2 Capability Assessment

pygit2 provides comprehensive bindings to libgit2. Key capabilities:

| Feature | pygit2 Support | Notes |
|---------|----------------|-------|
| Status | ✅ Full | `repo.status()`, `repo.status_file()` |
| Diff | ✅ Full | Working tree, staged, ref-to-ref |
| Blame | ✅ Full | `repo.blame()` |
| Staging | ✅ Full | `index.add()`, `index.remove()` |
| Commits | ✅ Full | `repo.create_commit()`, `repo.amend_commit()` |
| Branches | ✅ Full | `repo.branches`, create/delete/checkout |
| Reset | ✅ Full | `repo.reset()` (SOFT, MIXED, HARD) |
| Merge | ✅ Full | `repo.merge()`, `repo.merge_commits()`, `merge_analysis()` |
| Cherry-pick | ✅ Full | `repo.cherrypick()` |
| Revert | ✅ Full | `repo.revert_commit()` |
| Tags | ✅ Full | Annotated and lightweight |
| Remotes | ✅ Full | fetch/push with `RemoteCallbacks` |
| Stash | ⚠️ Partial | libgit2 supports, pygit2 bindings may be limited |
| Rebase | ⚠️ Manual | Must be built from lower-level operations |
| Submodules | ⚠️ Partial | pygit2 read support; writes via subprocess fallback |
| Worktrees | ✅ Full | `repo.list_worktrees()`, `repo.add_worktree()` |

### Credential Handling

pygit2 supports credentials via `RemoteCallbacks`:

```python
class RemoteCallbacks:
    def credentials(self, url, username_from_url, allowed_types):
        # Return: Username, UserPass, Keypair, KeypairFromAgent
        ...
```

**Options for system credential integration:**

1. **KeypairFromAgent** — Uses SSH agent directly (pygit2 native)
2. **Credential helper callback** — Invoke `git credential fill` subprocess
3. **Direct credential passing** — Caller provides credentials explicitly

---

## Design Options

### Option A: Pure pygit2 (Recommended)

All operations via pygit2. Credentials handled via `RemoteCallbacks` with a helper that invokes system git credential manager when needed.

```python
class GitOps:
    def __init__(self, repo_path: Path):
        self._repo = pygit2.Repository(repo_path)
    
    # Read
    def status(self) -> GitStatus: ...
    def diff(self, base: str | None = None, staged: bool = False) -> GitDiff: ...
    def blame(self, path: Path) -> BlameResult: ...
    def log(self, limit: int = 50) -> list[CommitInfo]: ...
    def merge_analysis(self, their_head: str) -> MergeAnalysisResult: ...
    
    # Write - Index
    def stage(self, paths: list[Path]) -> None: ...
    def unstage(self, paths: list[Path]) -> None: ...
    
    # Write - Commits
    def commit(self, message: str, author: Signature | None = None) -> CommitResult: ...
    def amend(self, message: str | None = None) -> CommitResult: ...
    
    # Write - Branches
    def create_branch(self, name: str, ref: str = "HEAD") -> BranchInfo: ...
    def checkout(self, ref: str) -> None: ...
    def delete_branch(self, name: str, force: bool = False) -> None: ...
    
    # Write - History manipulation
    def reset(self, ref: str, mode: ResetMode = ResetMode.MIXED) -> None: ...
    def merge(self, ref: str) -> MergeResult: ...
    def cherrypick(self, commit: str) -> CherrypickResult: ...
    def revert(self, commit: str) -> RevertResult: ...
    
    # Write - Stash
    def stash_push(self, message: str | None = None, include_untracked: bool = False) -> StashResult: ...
    def stash_pop(self, index: int = 0) -> None: ...
    def stash_list(self) -> list[StashEntry]: ...
    
    # Write - Remotes
    def fetch(self, remote: str = "origin", callbacks: RemoteCallbacks | None = None) -> FetchResult: ...
    def push(self, remote: str = "origin", refspec: str | None = None, callbacks: RemoteCallbacks | None = None) -> PushResult: ...


class CredentialCallback(pygit2.RemoteCallbacks):
    """Credentials via system git credential helper."""
    
    def credentials(self, url, username_from_url, allowed_types):
        if allowed_types & pygit2.CredentialType.SSH_KEY:
            return pygit2.KeypairFromAgent(username_from_url or "git")
        
        if allowed_types & pygit2.CredentialType.USERPASS_PLAINTEXT:
            # Invoke: git credential fill
            creds = self._get_system_credentials(url)
            return pygit2.UserPass(creds.username, creds.password)
        
        return None
    
    def _get_system_credentials(self, url: str) -> Credentials:
        # subprocess: echo "url={url}" | git credential fill
        ...
```

**Pros:**
- Single library, consistent API
- Full control over all operations
- Deterministic behavior
- No shell escaping issues

**Cons:**
- Must implement credential helper bridge (one-time cost)
- Stash bindings may require workarounds if incomplete

---

### Option B: Pure pygit2 (Local Only)

All local operations via pygit2. Remote operations require explicit credential passing.

```python
class GitOps:
    # ... same as above, but:
    
    def fetch(self, remote: str, credentials: Credentials) -> FetchResult:
        """Fetch requires explicit credentials."""
        callbacks = pygit2.RemoteCallbacks(credentials=credentials.to_pygit2())
        ...
```

**Pros:**
- Simplest implementation
- No credential complexity
- Clear security boundary

**Cons:**
- Caller must manage credentials
- Less convenient for automated workflows

---

### Option C: pygit2 with Subprocess Fallback

pygit2 for most operations, subprocess `git` for:
- Stash (if pygit2 bindings incomplete)
- Remote operations (automatic credential helper support)

```python
class GitOps:
    # Local operations: pygit2
    def status(self) -> GitStatus: ...
    def commit(self, message: str) -> CommitResult: ...
    
    # Operations with subprocess fallback
    def stash_push(self, message: str | None = None) -> StashResult:
        if HAS_PYGIT2_STASH:
            return self._pygit2_stash_push(message)
        return self._subprocess_stash_push(message)
    
    def fetch(self, remote: str = "origin") -> FetchResult:
        # Use subprocess for automatic credential helper
        return self._subprocess_fetch(remote)
```

**Pros:**
- Automatic credential helper support via git CLI
- Fallback for any pygit2 gaps

**Cons:**
- Two code paths to maintain
- Shell escaping concerns with subprocess
- Less deterministic

---

## Recommended Approach

**Option A: Pure pygit2** with credential helper bridge.

Rationale:
1. **Consistency** — Single library means predictable behavior
2. **No shell escaping** — pygit2 is native Python, no subprocess injection risks
3. **Full control** — Every operation returns structured data
4. **Credential bridge is tractable** — `git credential fill` is simple to invoke

If stash bindings are incomplete in pygit2, implement via low-level operations (commit to special ref, restore).

---

## File Plan

```
git/
├── __init__.py          # Exports: GitOps, types
├── ops.py               # GitOps class: all operations
├── types.py             # Dataclasses: GitStatus, GitDiff, CommitResult, etc.
├── credentials.py       # CredentialCallback, system credential helper bridge
└── errors.py            # Git-specific error types
```

## Dependencies

- `pygit2>=1.19.0` — libgit2 Python bindings (already in pyproject.toml)

---

## Key Interfaces

### GitOps Class

```python
class GitOps:
    def __init__(self, repo_path: Path): ...
    
    # === Read Operations ===
    
    def status(self) -> GitStatus:
        """Current repository status: staged, modified, untracked, conflicts."""
    
    def diff(
        self,
        base: str | None = None,
        target: str | None = None,
        staged: bool = False,
        paths: list[Path] | None = None,
    ) -> GitDiff:
        """Generate diff. base=None means working tree, staged=True means index."""
    
    def blame(self, path: Path, line_range: tuple[int, int] | None = None) -> BlameResult:
        """Line-by-line attribution for a file."""
    
    def log(
        self,
        ref: str = "HEAD",
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
        paths: list[Path] | None = None,
    ) -> list[CommitInfo]:
        """Commit history."""
    
    def show(self, ref: str) -> CommitDetail:
        """Detailed commit information including diff."""
    
    def head(self) -> HeadInfo:
        """Current HEAD: commit, branch (or None if detached), dirty status."""
    
    def branches(self, pattern: str | None = None) -> list[BranchInfo]:
        """List branches, optionally filtered by glob pattern."""
    
    def tags(self, pattern: str | None = None) -> list[TagInfo]:
        """List tags, optionally filtered by glob pattern."""
    
    def remotes(self) -> list[RemoteInfo]:
        """List configured remotes."""
    
    def merge_analysis(self, their_head: str) -> MergeAnalysisResult:
        """Analyze merge: fast-forward possible, conflicts expected, etc."""
    
    def describe(self, ref: str = "HEAD") -> str | None:
        """Human-readable description (e.g., 'v1.2.3-5-gabcdef')."""
    
    # === Write Operations: Index ===
    
    def stage(self, paths: list[Path]) -> StageResult:
        """Stage files for commit."""
    
    def unstage(self, paths: list[Path]) -> None:
        """Remove files from staging area (keep working tree changes)."""
    
    def discard(self, paths: list[Path]) -> None:
        """Discard working tree changes (restore from index/HEAD)."""
    
    # === Write Operations: Commits ===
    
    def commit(
        self,
        message: str,
        author: Signature | None = None,
        committer: Signature | None = None,
        allow_empty: bool = False,
    ) -> CommitResult:
        """Create a commit from staged changes."""
    
    def amend(
        self,
        message: str | None = None,
        author: Signature | None = None,
    ) -> CommitResult:
        """Amend the most recent commit."""
    
    # === Write Operations: Branches ===
    
    def create_branch(self, name: str, ref: str = "HEAD") -> BranchInfo:
        """Create a new branch at the given ref."""
    
    def checkout(self, ref: str, create: bool = False) -> CheckoutResult:
        """Switch to a branch or ref. If create=True, create branch first."""
    
    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete a branch. force=True deletes unmerged branches."""
    
    def rename_branch(self, old_name: str, new_name: str) -> BranchInfo:
        """Rename a branch."""
    
    # === Write Operations: History Manipulation ===
    
    def reset(self, ref: str, mode: ResetMode = ResetMode.MIXED) -> ResetResult:
        """Reset HEAD to ref. Mode: SOFT, MIXED, HARD."""
    
    def merge(self, ref: str, message: str | None = None) -> MergeResult:
        """Merge ref into current branch."""
    
    def cherrypick(self, commit: str) -> CherrypickResult:
        """Cherry-pick a commit onto current branch."""
    
    def revert(self, commit: str) -> RevertResult:
        """Revert a commit (create inverse commit)."""
    
    def abort_merge(self) -> None:
        """Abort an in-progress merge."""
    
    # === Write Operations: Stash ===
    
    def stash_push(
        self,
        message: str | None = None,
        include_untracked: bool = False,
        keep_index: bool = False,
    ) -> StashResult:
        """Stash current changes."""
    
    def stash_pop(self, index: int = 0) -> StashPopResult:
        """Pop a stash entry, applying changes to working tree."""
    
    def stash_apply(self, index: int = 0) -> StashPopResult:
        """Apply a stash entry without removing it."""
    
    def stash_drop(self, index: int = 0) -> None:
        """Drop a stash entry."""
    
    def stash_list(self) -> list[StashEntry]:
        """List all stash entries."""
    
    # === Write Operations: Tags ===
    
    def create_tag(
        self,
        name: str,
        ref: str = "HEAD",
        message: str | None = None,
    ) -> TagInfo:
        """Create a tag. If message provided, creates annotated tag."""
    
    def delete_tag(self, name: str) -> None:
        """Delete a tag."""
    
    # === Write Operations: Remotes ===
    
    def fetch(
        self,
        remote: str = "origin",
        refspecs: list[str] | None = None,
        prune: bool = False,
        callbacks: RemoteCallbacks | None = None,
    ) -> FetchResult:
        """Fetch from remote."""
    
    def push(
        self,
        remote: str = "origin",
        refspecs: list[str] | None = None,
        force: bool = False,
        callbacks: RemoteCallbacks | None = None,
    ) -> PushResult:
        """Push to remote."""
    
    def pull(
        self,
        remote: str = "origin",
        branch: str | None = None,
        callbacks: RemoteCallbacks | None = None,
    ) -> PullResult:
        """Fetch and merge (convenience method)."""
```

### Types

```python
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


class ResetMode(Enum):
    SOFT = "soft"
    MIXED = "mixed"
    HARD = "hard"


@dataclass
class Signature:
    name: str
    email: str
    time: datetime | None = None


@dataclass
class GitStatus:
    branch: str | None  # None if detached HEAD
    head_commit: str
    is_clean: bool
    staged: list[FileStatus]
    modified: list[FileStatus]
    untracked: list[str]
    conflicts: list[ConflictFile]
    state: RepositoryState  # NONE, MERGE, REVERT, CHERRYPICK, etc.


@dataclass
class FileStatus:
    path: str
    status: Literal["added", "modified", "deleted", "renamed", "copied", "typechange"]
    old_path: str | None = None  # For renames


@dataclass
class ConflictFile:
    path: str
    ancestor_oid: str | None
    ours_oid: str | None
    theirs_oid: str | None


class RepositoryState(Enum):
    NONE = "none"
    MERGE = "merge"
    REVERT = "revert"
    CHERRYPICK = "cherrypick"
    BISECT = "bisect"
    REBASE = "rebase"
    REBASE_INTERACTIVE = "rebase_interactive"
    REBASE_MERGE = "rebase_merge"
    APPLY_MAILBOX = "apply_mailbox"
    APPLY_MAILBOX_OR_REBASE = "apply_mailbox_or_rebase"


@dataclass
class HeadInfo:
    commit: str
    branch: str | None
    is_detached: bool
    is_dirty: bool


@dataclass
class CommitInfo:
    oid: str
    short_oid: str
    message: str
    author: Signature
    committer: Signature
    parents: list[str]


@dataclass
class CommitDetail(CommitInfo):
    diff: GitDiff


@dataclass
class CommitResult:
    oid: str
    short_oid: str


@dataclass
class GitDiff:
    files: list[DiffFile]
    stats: DiffStats


@dataclass
class DiffStats:
    files_changed: int
    insertions: int
    deletions: int


@dataclass
class DiffFile:
    path: str
    old_path: str | None
    status: Literal["added", "modified", "deleted", "renamed", "copied"]
    hunks: list[DiffHunk]
    binary: bool = False


@dataclass
class DiffHunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    header: str
    lines: list[DiffLine]


@dataclass
class DiffLine:
    origin: Literal["+", "-", " ", "\\"]  # Add, delete, context, no-newline
    content: str
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class BlameResult:
    path: str
    lines: list[BlameLine]


@dataclass
class BlameLine:
    line_no: int
    content: str
    commit: str
    author: str
    author_email: str
    date: datetime
    original_line_no: int
    original_path: str


@dataclass
class BranchInfo:
    name: str
    commit: str
    is_current: bool
    is_remote: bool
    upstream: str | None


@dataclass
class TagInfo:
    name: str
    commit: str
    is_annotated: bool
    message: str | None
    tagger: Signature | None


@dataclass
class RemoteInfo:
    name: str
    url: str
    push_url: str | None
    fetch_refspecs: list[str]
    push_refspecs: list[str]


@dataclass
class MergeAnalysisResult:
    can_fastforward: bool
    is_up_to_date: bool
    has_conflicts: bool  # Estimated based on merge-base analysis
    merge_base: str | None


@dataclass
class MergeResult:
    success: bool
    fastforward: bool
    commit: str | None  # Merge commit OID if created
    conflicts: list[ConflictFile]


@dataclass
class CherrypickResult:
    success: bool
    commit: str | None
    conflicts: list[ConflictFile]


@dataclass
class RevertResult:
    success: bool
    commit: str | None
    conflicts: list[ConflictFile]


@dataclass
class ResetResult:
    previous_head: str
    new_head: str


@dataclass
class StashEntry:
    index: int
    message: str
    commit: str


@dataclass
class StashResult:
    commit: str
    message: str


@dataclass
class StashPopResult:
    success: bool
    conflicts: list[ConflictFile]


@dataclass
class StageResult:
    staged: list[str]
    already_staged: list[str]
    not_found: list[str]


@dataclass
class CheckoutResult:
    previous_branch: str | None
    current_branch: str | None
    is_detached: bool


@dataclass
class FetchResult:
    remote: str
    updated_refs: list[RefUpdate]


@dataclass
class PushResult:
    remote: str
    pushed_refs: list[RefUpdate]
    rejected_refs: list[RefRejection]


@dataclass
class PullResult:
    fetch: FetchResult
    merge: MergeResult


@dataclass
class RefUpdate:
    ref: str
    old_oid: str | None
    new_oid: str


@dataclass
class RefRejection:
    ref: str
    reason: str
```

---

## Credential Handling

### Strategy: System Credential Helper Bridge

```python
# credentials.py

import subprocess
import pygit2
from urllib.parse import urlparse


class SystemCredentialCallback(pygit2.RemoteCallbacks):
    """
    RemoteCallbacks implementation that uses system git credential helpers.
    
    Supports:
    - SSH via KeypairFromAgent (uses system SSH agent)
    - HTTPS via git-credential-manager or other configured helpers
    """
    
    def credentials(self, url: str, username_from_url: str | None, allowed_types: int):
        # SSH: use agent
        if allowed_types & pygit2.CredentialType.SSH_KEY:
            username = username_from_url or "git"
            return pygit2.KeypairFromAgent(username)
        
        # HTTPS: query system credential helper
        if allowed_types & pygit2.CredentialType.USERPASS_PLAINTEXT:
            creds = self._query_credential_helper(url)
            if creds:
                return pygit2.UserPass(creds["username"], creds["password"])
        
        return None
    
    def _query_credential_helper(self, url: str) -> dict | None:
        """
        Invoke: git credential fill
        
        See: https://git-scm.com/docs/git-credential
        """
        parsed = urlparse(url)
        input_data = f"protocol={parsed.scheme}\nhost={parsed.netloc}\n"
        if parsed.path:
            input_data += f"path={parsed.path.lstrip('/')}\n"
        input_data += "\n"
        
        try:
            result = subprocess.run(
                ["git", "credential", "fill"],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return None
            
            creds = {}
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    creds[key] = value
            
            if "username" in creds and "password" in creds:
                return creds
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        return None
```

---

## Open Questions

1. **pygit2 stash completeness?**
   - **Resolved:** pygit2 stash bindings work; implemented in M1

2. **Thread safety?**
   - **Recommendation:** One GitOps instance per thread; pygit2.Repository is not thread-safe

3. **Detached HEAD handling?**
   - **Resolved:** `head.branch = None`, all operations work, explicit in types

4. **Submodule support?**
   - **Resolved:** Full management via pygit2 + subprocess fallback (Issue #122)

5. **Worktree support?**
   - **Resolved:** GitOps-returning worktree operations (Issue #120)

---

## Interactive Rebase

**Issue:** #121

### Design: Hybrid Plan-Based

Combines batch efficiency with `edit` action flexibility:

1. Agent requests a rebase plan (commits to be rebased)
2. Agent modifies plan (reorder, change actions, edit messages)
3. CodePlane executes plan atomically
4. For `edit` actions, execution pauses for agent modifications
5. Agent continues or aborts

### Interface

```python
class GitOps:
    def rebase_plan(self, upstream: str, onto: str | None = None) -> RebasePlan:
        """Generate default plan (all picks). Agent can modify before execute."""
    
    def rebase_execute(self, plan: RebasePlan) -> RebaseResult:
        """Execute plan. On conflict or edit-pause, returns partial result."""
    
    def rebase_continue(self) -> RebaseResult:
        """Resume after conflict resolution or edit completion."""
    
    def rebase_abort(self) -> None:
        """Abort and restore original state."""
    
    def rebase_skip(self) -> RebaseResult:
        """Skip current commit and continue."""
```

### Types

```python
@dataclass
class RebasePlan:
    upstream: str
    onto: str
    steps: list[RebaseStep]

@dataclass
class RebaseStep:
    action: Literal["pick", "reword", "edit", "squash", "fixup", "drop"]
    commit_sha: str
    message: str | None = None  # For reword/squash

@dataclass
class RebaseResult:
    success: bool
    completed_steps: int
    total_steps: int
    state: Literal["done", "conflict", "edit_pause", "aborted"]
    conflict_paths: list[str] | None = None
    current_commit: str | None = None  # For edit_pause
    new_head: str | None = None
```

### Implementation

| Component | Description | Est. LOC |
|-----------|-------------|----------|
| `RebasePlanner` | Generate plan from commit range | ~150 |
| `RebaseFlow` | State machine, execute steps, handle conflicts | ~250 |
| Types | `RebasePlan`, `RebaseStep`, `RebaseResult` | ~80 |
| Tests | Edge cases, conflicts, all action types | ~400 |

---

## Submodule Management

**Issue:** #122

### Design: Full Management with Subprocess Fallback

Use pygit2 where bindings exist; fall back to `git submodule` subprocess for gaps.

### Interface

```python
class GitOps:
    def submodules(self) -> list[SubmoduleInfo]:
        """List all submodules with status."""
    
    def submodule_status(self, path: str) -> SubmoduleStatus:
        """Detailed status for one submodule."""
    
    def submodule_init(self, paths: list[str] | None = None) -> list[str]:
        """Initialize submodules. Returns initialized paths."""
    
    def submodule_update(
        self, 
        paths: list[str] | None = None,
        recursive: bool = False,
        init: bool = True,
    ) -> SubmoduleUpdateResult:
        """Update submodules to recorded commits."""
    
    def submodule_sync(self, paths: list[str] | None = None) -> None:
        """Sync submodule URLs from .gitmodules to .git/config."""
    
    def submodule_add(
        self,
        url: str,
        path: str,
        branch: str | None = None,
    ) -> SubmoduleInfo:
        """Add new submodule."""
    
    def submodule_deinit(self, path: str, force: bool = False) -> None:
        """Deinitialize submodule."""
    
    def submodule_remove(self, path: str) -> None:
        """Fully remove submodule."""
```

### Types

```python
@dataclass
class SubmoduleInfo:
    name: str
    path: str
    url: str
    branch: str | None
    head_sha: str | None
    status: SubmoduleState

class SubmoduleState(Enum):
    UNINITIALIZED = "uninitialized"
    CLEAN = "clean"
    DIRTY = "dirty"
    OUTDATED = "outdated"
    MISSING = "missing"

@dataclass
class SubmoduleStatus:
    info: SubmoduleInfo
    workdir_dirty: bool
    index_dirty: bool
    untracked_files: int
    recorded_sha: str
    actual_sha: str | None

@dataclass
class SubmoduleUpdateResult:
    updated: list[str]
    failed: list[tuple[str, str]]  # (path, error)
    already_current: list[str]
```

### Implementation Strategy

| Operation | Implementation | Rationale |
|-----------|---------------|----------|
| `submodules()` | pygit2 | Native support |
| `submodule_status()` | pygit2 | Native support |
| `submodule_init()` | pygit2 | Native support |
| `submodule_update()` | subprocess | Recursive, credential handling |
| `submodule_sync()` | subprocess | Simpler than manual config edit |
| `submodule_add()` | subprocess | Complex (.gitmodules edit + clone) |
| `submodule_deinit()` | subprocess | Config cleanup |
| `submodule_remove()` | hybrid | Subprocess deinit + pygit2 staging |

---

## Worktree Support

**Issue:** #120

### Design: GitOps-Returning Worktrees

Worktree operations return ready-to-use `GitOps` instances for seamless parallel workflows.

### Interface

```python
class GitOps:
    def worktrees(self) -> list[WorktreeInfo]:
        """List all worktrees (including main working directory)."""
    
    def worktree_add(
        self,
        path: Path,
        ref: str,
        checkout: bool = True,
    ) -> "GitOps":
        """Add worktree at path for ref. Returns GitOps for new worktree."""
    
    def worktree_open(self, name: str) -> "GitOps":
        """Get GitOps instance for existing worktree by name."""
    
    def worktree_remove(self, name: str, force: bool = False) -> None:
        """Remove worktree. force=True removes even if dirty."""
    
    def worktree_lock(self, name: str, reason: str | None = None) -> None:
        """Lock worktree to prevent pruning."""
    
    def worktree_unlock(self, name: str) -> None:
        """Unlock worktree."""
    
    def worktree_prune(self) -> list[str]:
        """Remove stale worktree entries. Returns pruned names."""
    
    def is_worktree(self) -> bool:
        """True if this GitOps is for a worktree (not main working directory)."""
    
    def worktree_info(self) -> WorktreeInfo | None:
        """Get info about this worktree, or None if main working directory."""
```

### Types

```python
@dataclass
class WorktreeInfo:
    name: str
    path: Path
    head_ref: str      # Branch name or "HEAD" if detached
    head_sha: str
    is_main: bool      # True for main working directory
    is_bare: bool
    is_locked: bool
    lock_reason: str | None
    is_prunable: bool  # True if worktree directory is missing
```

### Usage Pattern

```python
# Agent working on two branches simultaneously
ops = GitOps(repo_path)

# Create worktree for feature branch
feature_ops = ops.worktree_add(Path("/tmp/feature-work"), "feature/new-api")

# Work on main
ops.stage(["src/main.py"])
ops.commit("fix: typo")

# Simultaneously work on feature branch (separate GitOps)
feature_ops.stage(["src/api.py"])
feature_ops.commit("feat: new endpoint")

# Clean up
ops.worktree_remove("feature-work")
```

---

## Test Strategy

```
tests/git/
├── __init__.py
├── conftest.py          # Fixtures: temp repos, sample commits
├── test_status.py       # Status queries
├── test_diff.py         # Diff generation
├── test_blame.py        # Blame
├── test_commits.py      # Commit, amend
├── test_branches.py     # Branch operations
├── test_reset.py        # Reset operations
├── test_merge.py        # Merge, cherrypick, revert
├── test_stash.py        # Stash operations
├── test_remotes.py      # Fetch, push (with mock server)
├── test_credentials.py  # Credential callback
├── test_rebase.py       # Interactive rebase operations
├── test_submodules.py   # Submodule management
├── test_worktrees.py    # Worktree operations
└── fixtures/
    └── sample_repo/     # Pre-built test repository
```
