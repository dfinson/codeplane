# Mutation Module — Design Spec

## Scope

The mutation module applies atomic file edits to the repository. All file modifications flow through this module.

### Responsibilities

- Atomic file writes (temp file → rename)
- Precondition checking (hash/mtime verification)
- Scope enforcement (`.cplignore` exclusion)
- Concurrent file locking
- CRLF normalization
- Structured delta generation
- Git `mv` for tracked file moves

### From SPEC.md

- §9: Mutation engine
- §9.2: Apply protocol
- §9.3: Concurrency model
- §9.4: Scope enforcement
- §9.5: Structured delta format

---

## Design Options

### Option A: Functional approach

```python
def apply_mutations(edits: list[FileEdit], repo: Repo) -> MutationResult:
    with acquire_locks(edits):
        check_preconditions(edits, repo)
        temp_files = write_temp_files(edits)
        atomic_replace(temp_files)
        return generate_delta(edits)
```

**Pros:** Simple, explicit flow
**Cons:** No state between calls, repeated lock acquisition

### Option B: MutationEngine class

```python
class MutationEngine:
    def __init__(self, repo: Repo):
        self.repo = repo
        self.lock_manager = LockManager()
    
    async def apply(self, edits: list[FileEdit], dry_run=False) -> MutationResult: ...
    async def validate(self, edits: list[FileEdit]) -> ValidationResult: ...
```

**Pros:** Shared state (lock manager), cleaner interface
**Cons:** Stateful

### Option C: Transaction model

```python
class MutationTransaction:
    def __init__(self, repo: Repo):
        self.edits = []
        self.locks = []
    
    def add_edit(self, edit: FileEdit) -> None: ...
    async def validate(self) -> ValidationResult: ...
    async def commit(self) -> MutationResult: ...
    async def rollback(self) -> None: ...
```

**Pros:** Explicit transaction boundaries
**Cons:** More complex, may be overkill

---

## Recommended Approach

**Option B (MutationEngine class)** — holds lock manager, provides clean `apply()` interface, supports `dry_run` for previews.

---

## File Plan

```
mutation/
├── __init__.py
├── engine.py        # MutationEngine: validate, apply, dry_run
├── delta.py         # Structured delta generation
├── locks.py         # File locking (portalocker)
└── scope.py         # Scope validation (.cplignore checking)
```

## Dependencies

- `portalocker` — Cross-platform file locking
- Standard library `os`, `shutil`, `hashlib`

## Key Interfaces

```python
# engine.py
class MutationEngine:
    async def apply(
        self,
        edits: list[FileEdit],
        dry_run: bool = False,
        preconditions: dict[Path, str] | None = None  # path -> expected hash
    ) -> MutationResult: ...

# Types
@dataclass
class FileEdit:
    path: Path
    action: Literal["create", "update", "delete", "move"]
    content: str | None = None
    patches: list[Patch] | None = None
    move_to: Path | None = None

@dataclass
class MutationResult:
    applied: bool
    dry_run: bool
    mutation_id: str
    delta: MutationDelta
    affected_symbols: list[str]
    affected_tests: list[str]
    repo_fingerprint: str

@dataclass
class MutationDelta:
    files_changed: int
    insertions: int
    deletions: int
    files: list[FileDelta]

@dataclass
class FileDelta:
    path: str
    action: str
    old_hash: str | None
    new_hash: str | None
    diff_stats: DiffStats
```

## Apply Protocol (from SPEC.md §9.2)

1. Validate all edits against scope rules
2. Check preconditions (hash match) for all files
3. Acquire exclusive locks on all target files
4. Write new content to temp files
5. `os.replace()` temp → target (atomic)
6. `fsync()` on file and parent directory
7. Release locks
8. Generate and return structured delta

## Error Handling

- Precondition failure → abort before any write
- Lock timeout → return `MUTATION_LOCK_TIMEOUT` (retryable)
- Write failure → rollback (delete temp files), return error
- Scope violation → return `MUTATION_SCOPE_VIOLATION`

## Open Questions

1. Parallel file writes: thread pool or asyncio?
   - **Recommendation:** `asyncio.to_thread()` for file I/O
2. Git `mv` detection: automatic or explicit?
   - **Recommendation:** Explicit `action: "move"` triggers `git mv`
3. Patch format: line-based or character-based?
   - **Recommendation:** Line-based (simpler, matches LSP TextEdit)
