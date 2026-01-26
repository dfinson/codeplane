# Git Module — Design Spec

## Scope

The git module provides structured Git operations. Read operations via `pygit2`, remote operations via system `git`.

### Responsibilities

- Status (staged, modified, untracked, conflicts)
- Diff (working tree, staged, between refs)
- Blame (line-level attribution)
- Stage/unstage files
- Log (commit history)
- HEAD and branch information
- **Not:** commits, merges, rebases, stashes (explicitly out of scope)

### From SPEC.md

- §10: Git and file operations
- §9.7: Git behavior (mutation engine)

---

## Design Options

### Option A: Thin pygit2 wrapper

```python
def git_status(repo_path: Path) -> GitStatus:
    repo = pygit2.Repository(repo_path)
    return GitStatus(
        staged=[...],
        modified=[...],
        untracked=[...]
    )

def git_diff(repo_path: Path, base: str | None) -> GitDiff:
    ...
```

**Pros:** Simple, stateless
**Cons:** Repeated repo opening

### Option B: GitOps class

```python
class GitOps:
    def __init__(self, repo_path: Path):
        self.repo = pygit2.Repository(repo_path)
    
    def status(self) -> GitStatus: ...
    def diff(self, base: str | None = None, staged: bool = False) -> GitDiff: ...
    def blame(self, path: Path, lines: range | None = None) -> BlameResult: ...
    def stage(self, paths: list[Path]) -> None: ...
    def unstage(self, paths: list[Path]) -> None: ...
```

**Pros:** Reuses repo object, cleaner interface
**Cons:** Must handle repo state changes

### Option C: Hybrid (pygit2 + subprocess)

```python
class GitOps:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path
        self._pygit2_repo = None
    
    @property
    def repo(self) -> pygit2.Repository:
        # Lazy, reopens if needed
        ...
    
    def status(self) -> GitStatus:
        # Use pygit2
        ...
    
    async def fetch(self, remote: str = "origin") -> None:
        # Use subprocess for credential compatibility
        await run_git(["fetch", remote])
```

**Pros:** Best tool for each job
**Cons:** Two code paths

---

## Recommended Approach

**Option C (Hybrid)** — `pygit2` for local operations (fast, no subprocess), system `git` for remote operations (credential helpers, SSH agents).

---

## File Plan

```
git/
├── __init__.py
├── ops.py           # GitOps class: status, diff, blame, stage, log
└── subprocess.py    # System git runner for remote ops (fetch, etc.)
```

## Dependencies

- `pygit2` — libgit2 Python bindings
- Standard library `subprocess` for system git

## Key Interfaces

```python
# ops.py
class GitOps:
    def __init__(self, repo_path: Path): ...
    
    # Read operations
    def status(self) -> GitStatus: ...
    def diff(self, base: str | None = None, staged: bool = False) -> GitDiff: ...
    def blame(self, path: Path, line_range: tuple[int, int] | None = None) -> BlameResult: ...
    def log(self, limit: int = 10, since: str | None = None) -> list[Commit]: ...
    def head(self) -> HeadInfo: ...
    
    # Write operations (limited)
    def stage(self, paths: list[Path]) -> None: ...
    def unstage(self, paths: list[Path]) -> None: ...
    def mv(self, from_path: Path, to_path: Path) -> None: ...  # For tracked file renames

# Types
@dataclass
class GitStatus:
    branch: str
    head: str
    clean: bool
    staged: list[str]
    modified: list[str]
    untracked: list[str]
    conflicts: list[str]

@dataclass
class GitDiff:
    files: list[DiffFile]
    stats: DiffStats

@dataclass
class DiffFile:
    path: str
    status: Literal["added", "modified", "deleted", "renamed"]
    old_path: str | None
    hunks: list[DiffHunk]

@dataclass
class BlameResult:
    lines: list[BlameLine]

@dataclass
class BlameLine:
    line_no: int
    commit: str
    author: str
    date: str
    content: str
```

## Explicit Non-Operations (from SPEC.md §9.10, §15.2)

The following are **not** in scope:

- `git commit`
- `git merge`
- `git rebase`
- `git stash`
- `git reset`
- `git checkout` (branch switching)
- `git push` (write to remote)

These are agent/user responsibility, not CodePlane operations.

## Open Questions

1. pygit2 thread safety?
   - **Recommendation:** One GitOps instance per operation, don't share across threads
2. Diff format: unified or structured?
   - **Recommendation:** Structured (hunks as objects), can render to unified if needed
3. Handle detached HEAD?
   - **Recommendation:** Report `branch: null` in status, operations still work
