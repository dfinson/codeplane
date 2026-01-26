# Ledger Module — Design Spec

## Scope

The ledger module persists operation history for mechanical accountability. Append-only SQLite database per repository.

### Responsibilities

- Task lifecycle persistence (open, close, state transitions)
- Operation logging (mutations, tests, refactors)
- Convergence signal tracking (fingerprints, budgets, repeated failures)
- Retention policy enforcement (age-based, count-based)
- Query interface for diagnostics and debugging

### From SPEC.md

- §12: Task model, convergence controls, ledger
- §12.5: Operation ledger schema

---

## Design Options

### Option A: Raw SQLite

```python
def log_operation(db_path: Path, op: Operation) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO operations ...", op.to_tuple())
    conn.commit()
    conn.close()

def get_task(db_path: Path, task_id: str) -> Task | None:
    ...
```

**Pros:** Simple, direct
**Cons:** Repeated connection management, no abstraction

### Option B: Repository pattern

```python
class LedgerRepository:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
    
    def create_task(self, task: Task) -> None: ...
    def update_task(self, task_id: str, **updates) -> None: ...
    def log_operation(self, op: Operation) -> None: ...
    def get_task(self, task_id: str) -> Task | None: ...
    def get_operations(self, task_id: str) -> list[Operation]: ...
```

**Pros:** Clean interface, connection reuse
**Cons:** Must handle thread safety

### Option C: Event sourcing

```python
class LedgerEvent:
    timestamp: datetime
    event_type: str
    payload: dict

class Ledger:
    def append(self, event: LedgerEvent) -> None: ...
    def replay(self, task_id: str) -> Task: ...
```

**Pros:** Full history, can reconstruct any state
**Cons:** Overkill for this use case

---

## Recommended Approach

**Option B (Repository pattern)** — clean interface, WAL mode for concurrent reads, single writer.

---

## File Plan

```
ledger/
├── __init__.py
├── store.py         # LedgerStore: task and operation persistence
├── schema.py        # Table definitions, migrations
└── retention.py     # Cleanup policies (age, count)
```

## Dependencies

- Standard library `sqlite3`
- No ORMs (keep it simple)

## Key Interfaces

```python
# store.py
class LedgerStore:
    def __init__(self, db_path: Path): ...
    
    # Task operations
    def create_task(self, task: Task) -> None: ...
    def update_task(self, task_id: str, state: TaskState, **kwargs) -> None: ...
    def get_task(self, task_id: str) -> Task | None: ...
    def get_open_tasks(self) -> list[Task]: ...
    def close_all_open(self, reason: str) -> int: ...  # For daemon restart
    
    # Operation logging
    def log_operation(self, op: Operation) -> None: ...
    def get_operations(self, task_id: str, limit: int = 100) -> list[Operation]: ...
    def get_recent_operations(self, limit: int = 50) -> list[Operation]: ...
    
    # Convergence queries
    def get_failure_fingerprint_count(self, task_id: str, fingerprint: str) -> int: ...
    def get_mutation_fingerprint_count(self, task_id: str, fingerprint: str) -> int: ...
    
    # Retention
    def cleanup(self, max_age_days: int = 14, max_tasks: int = 500) -> int: ...

# Types
@dataclass
class Task:
    task_id: str
    session_id: str
    opened_at: datetime
    closed_at: datetime | None
    state: TaskState
    repo_head_sha: str
    limits: TaskLimits
    counters: TaskCounters

@dataclass
class Operation:
    op_id: int  # Auto-increment
    task_id: str
    timestamp: datetime
    duration_ms: int
    op_type: str
    success: bool
    repo_before_hash: str
    repo_after_hash: str
    changed_paths: list[str]
    diff_stats: DiffStats
    mutation_fingerprint: str | None
    failure_fingerprint: str | None
    failure_class: str | None
    failing_tests: list[str] | None
    limit_triggered: str | None
```

## SQLite Schema (from SPEC.md §12.5)

```sql
-- Enable WAL mode for concurrent reads
PRAGMA journal_mode=WAL;

CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    state TEXT NOT NULL,
    repo_head_sha TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    counters_json TEXT NOT NULL
);

CREATE TABLE operations (
    op_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    op_type TEXT NOT NULL,
    success INTEGER NOT NULL,
    repo_before_hash TEXT,
    repo_after_hash TEXT,
    changed_paths TEXT,         -- JSON array
    diff_stats TEXT,            -- JSON object
    short_diff TEXT,
    mutation_fingerprint TEXT,
    failure_fingerprint TEXT,
    failure_class TEXT,
    failing_tests TEXT,         -- JSON array
    limit_triggered TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX idx_operations_task ON operations(task_id);
CREATE INDEX idx_operations_timestamp ON operations(timestamp);
CREATE INDEX idx_tasks_state ON tasks(state);
```

## Open Questions

1. WAL mode file cleanup?
   - **Recommendation:** Checkpoint on daemon shutdown, auto-checkpoint during normal operation
2. Async SQLite?
   - **Recommendation:** Use `asyncio.to_thread()` for write operations, sync reads are fast enough
3. Schema migrations?
   - **Recommendation:** Version in `_meta` table, migrate on open if needed
