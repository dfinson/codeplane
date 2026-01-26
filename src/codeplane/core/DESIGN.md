# Core Module — Design Spec

## Scope

The core module contains shared types, error definitions, and utilities used across all other modules.

### Responsibilities

- Shared dataclasses and type definitions
- Error codes and exception hierarchy
- Structured logging setup
- Common utilities (hashing, path normalization, etc.)

### From SPEC.md

- §4.2: Error response schema
- §4.2: Logging specification
- §20.3: Session state types

---

## Design Options

### Option A: Single types.py

```python
# types.py - everything in one file
@dataclass
class Session: ...
@dataclass
class SearchResult: ...
@dataclass
class MutationDelta: ...
# etc.
```

**Pros:** Easy to find everything
**Cons:** Gets large, circular import risk

### Option B: Domain-grouped types

```python
# types/session.py
@dataclass
class Session: ...

# types/index.py
@dataclass
class SearchResult: ...

# types/mutation.py
@dataclass
class MutationDelta: ...
```

**Pros:** Organized, smaller files
**Cons:** Many imports

### Option C: Shared + domain-specific

```python
# core/types.py - truly shared types only
@dataclass
class Session: ...
@dataclass
class RepoFingerprint: ...

# Each domain module has its own types
# index/types.py, refactor/types.py, etc.
```

**Pros:** Clear ownership, minimal core
**Cons:** May duplicate some types

---

## Recommended Approach

**Option C (Shared + domain-specific)** — core contains only cross-cutting types (Session, errors, fingerprints), each domain owns its specific types.

---

## File Plan

```
core/
├── __init__.py
├── types.py         # Shared types: Session, RepoFingerprint, etc.
├── errors.py        # Error codes, CodePlaneError exception hierarchy
├── logging.py       # Structured JSON logging setup
└── utils.py         # Hashing, path normalization, etc.
```

## Dependencies

- Standard library only (this is the base layer)

## Key Interfaces

```python
# types.py
@dataclass
class Session:
    session_id: str
    task_id: str
    task_state: str
    counters: SessionCounters
    fingerprints: SessionFingerprints
    timing: SessionTiming

@dataclass
class SessionCounters:
    mutations: int
    mutations_budget: int
    test_runs: int
    test_runs_budget: int

@dataclass  
class RepoFingerprint:
    head_sha: str
    index_version: int
    file_hash: str  # Hash of all tracked file hashes

# errors.py
class CodePlaneError(Exception):
    code: int
    error: str
    message: str
    retryable: bool
    details: dict

    def to_response(self) -> dict:
        return {
            "code": self.code,
            "error": self.error,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details
        }

# Error hierarchy
class AuthError(CodePlaneError): ...        # 1xxx
class ConfigError(CodePlaneError): ...      # 2xxx
class IndexError(CodePlaneError): ...       # 3xxx
class RefactorError(CodePlaneError): ...    # 4xxx
class MutationError(CodePlaneError): ...    # 5xxx
class TaskError(CodePlaneError): ...        # 6xxx
class TestError(CodePlaneError): ...        # 7xxx
class LSPError(CodePlaneError): ...         # 8xxx
class InternalError(CodePlaneError): ...    # 9xxx

# Specific errors
class AuthTokenMissing(AuthError):
    code = 1001
    error = "AUTH_TOKEN_MISSING"
    retryable = False

class MutationBudgetExceeded(TaskError):
    code = 6001
    error = "TASK_BUDGET_EXCEEDED"
    retryable = False

# logging.py
def setup_logging(log_path: Path, level: str = "info") -> None:
    """Configure structured JSON logging to file."""

def get_logger(name: str) -> Logger:
    """Get a logger with correlation ID support."""

class CorrelationContext:
    """Context manager for setting op_id, task_id, session_id in logs."""
    
    def __init__(self, op_id: str | None = None, task_id: str | None = None, session_id: str | None = None): ...
    def __enter__(self) -> None: ...
    def __exit__(self, *args) -> None: ...

# utils.py
def hash_file(path: Path) -> str:
    """SHA256 hash of file contents."""

def hash_content(content: str | bytes) -> str:
    """SHA256 hash of content."""

def normalize_path(path: Path, repo_root: Path) -> str:
    """Convert to repo-relative, forward-slash path."""

def generate_id(prefix: str = "") -> str:
    """Generate unique ID (e.g., 'sess_a1b2c3d4')."""
```

## Error Code Ranges (from SPEC.md §4.2)

| Range | Category | Examples |
|-------|----------|----------|
| 1xxx | Auth | `AUTH_TOKEN_MISSING`, `AUTH_TOKEN_INVALID` |
| 2xxx | Config | `CONFIG_PARSE_ERROR`, `CONFIG_INVALID_VALUE` |
| 3xxx | Index | `INDEX_CORRUPT`, `INDEX_SCHEMA_MISMATCH` |
| 4xxx | Refactor | `REFACTOR_DIVERGENCE`, `REFACTOR_LSP_TIMEOUT` |
| 5xxx | Mutation | `MUTATION_SCOPE_VIOLATION`, `MUTATION_LOCK_TIMEOUT` |
| 6xxx | Task | `TASK_BUDGET_EXCEEDED`, `TASK_NOT_FOUND` |
| 7xxx | Test | `TEST_RUNNER_NOT_FOUND`, `TEST_TIMEOUT` |
| 8xxx | LSP | `LSP_NOT_INSTALLED`, `LSP_CRASH` |
| 9xxx | Internal | `INTERNAL_ERROR`, `INTERNAL_TIMEOUT` |

## Logging Format (from SPEC.md §4.2)

```json
{"ts":"2026-01-26T15:30:00.123Z","level":"info","msg":"daemon started","port":54321}
{"ts":"2026-01-26T15:30:01.456Z","level":"debug","op_id":"op_abc123","task_id":"task_xyz","msg":"refactor started"}
```

## Open Questions

1. Use structlog or stdlib logging?
   - **Recommendation:** structlog for better JSON support and context binding
2. Thread-local vs contextvars for correlation IDs?
   - **Recommendation:** contextvars (async-compatible)
3. Include stack traces in logs?
   - **Recommendation:** Only for `error` level, truncated to 10 frames
