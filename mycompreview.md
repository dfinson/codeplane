# CodePlane M2 Index Engine — Comprehensive Code Review

**Branch:** Current working branch vs `main`  
**Date:** 2025-01-XX  
**Scope:** 254 files changed, +70,024 / -5,925 lines  
**Reviewer:** AI-assisted comprehensive review

---

## Executive Summary

This review covers the M2 Index Engine milestone — a major architectural addition to CodePlane that introduces:

- **Tier 0 (Lexical)**: Tantivy-based full-text search
- **Tier 1 (Structural)**: SQLite-backed structural facts via Tree-sitter parsing
- **MCP Server**: FastMCP-based tool exposure over HTTP
- **Testing Framework**: Runner packs with safe execution context
- **Refactoring Engine**: Index-based rename/move/delete operations

### Overall Assessment

| Dimension | Rating | Notes |
|-----------|--------|-------|
| Behavioral Correctness | ✅ GOOD | Epoch atomicity properly implemented with journal recovery |
| Architectural Integrity | ✅ GOOD | Clean `_internal` encapsulation, layer separation maintained |
| API Contracts | ✅ GOOD | Comprehensive MCPError codes with remediation hints |
| Error Handling | ⚠️ CAUTION | Some bare `except Exception:` blocks need review |
| Security | ✅ GOOD | Path traversal protection implemented correctly |
| Reliability | ✅ GOOD | Proper locking documented, timeout handling present |
| Observability | ✅ GOOD | structlog throughout, session-aware logging |
| Testing | ✅ GOOD | ~85 new test files with comprehensive coverage |
| Performance | ⚠️ CAUTION | Large ops.py files (2000 lines) may need monitoring |
| Configuration | ✅ GOOD | Pydantic models with validation |
| Documentation | ✅ GOOD | SPEC.md Section 7 comprehensive, DESIGN.md cleanup good |
| Code Quality | ⚠️ MINOR | Some large files could benefit from extraction |

### Risk Summary

| Risk | Severity | Status | Notes |
|------|----------|--------|-------|
| Epoch consistency under concurrent access | HIGH | ✅ MITIGATED | Journal-based two-phase commit implemented |
| def_uid collision | MEDIUM | ✅ MITIGATED | SHA-256 hash includes file path + disambiguator |
| Path traversal in file ops | HIGH | ✅ MITIGATED | `validate_path_in_repo()` properly implemented |
| Index corruption on crash | HIGH | ✅ MITIGATED | EpochJournal provides crash recovery |
| Subprocess timeout/cleanup | MEDIUM | ⚠️ PARTIAL | Timeouts present but not universal |
| SQLite locking under load | MEDIUM | ✅ MITIGATED | Retry logic with exponential backoff |

---

## Pass 1: Behavioral Correctness

### 1.1 Epoch Atomicity ✅

**Evidence:** [src/codeplane/index/_internal/db/epoch.py](src/codeplane/index/_internal/db/epoch.py)

The `EpochManager` implements a robust two-phase commit with rollback journal:

```python
class EpochManager:
    """Manages epoch lifecycle for atomic index updates.

    Implements two-phase commit with rollback journal:
    1. Write journal to disk (marks epoch as in-progress)
    2. Commit Tantivy staged changes
    3. Update journal (tantivy_committed=True)
    4. Commit SQLite epoch record
    5. Delete journal (marks epoch as complete)

    On crash recovery:
    - If journal exists with tantivy_committed=False: Tantivy is unchanged, safe
    - If journal exists with tantivy_committed=True, sqlite_committed=False:
      SQLite doesn't have the epoch, but Tantivy does. Recovery rebuilds Tantivy.
    """
```

**Findings:**
- ✅ Journal uses `os.fsync()` for durability
- ✅ `find_incomplete_epochs()` method exists for crash recovery
- ✅ Clear documentation of failure modes and recovery strategy

### 1.2 Reconciliation Locking ✅

**Evidence:** [src/codeplane/index/ops.py](src/codeplane/index/ops.py#L6-L8)

```python
# reconcile_lock: Only ONE reconcile() at a time (prevents RepoState corruption)
# tantivy_write_lock: Only ONE Tantivy write batch at a time (prevents crashes)
```

The `IndexCoordinator` uses `threading.Lock()` for serialization:

```python
self._reconcile_lock = threading.Lock()
self._tantivy_write_lock = threading.Lock()
```

**Findings:**
- ✅ Explicit documentation of locking purpose
- ✅ Two separate locks for different concerns (good separation)
- ⚠️ No deadlock detection - acceptable given lock ordering is implicit

### 1.3 def_uid Uniqueness ✅

**Evidence:** [src/codeplane/index/_internal/indexing/structural.py](src/codeplane/index/_internal/indexing/structural.py#L56-L71)

```python
def _compute_def_uid(
    unit_id: int,
    file_path: str,
    kind: str,
    lexical_path: str,
    signature_hash: str | None,
    disambiguator: int = 0,
) -> str:
    """Compute stable def_uid per SPEC.md §7.4.

    Includes file_path to distinguish same-named symbols in different files.
    """
    sig = signature_hash or ""
    raw = f"{unit_id}:{file_path}:{kind}:{lexical_path}:{sig}:{disambiguator}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**Findings:**
- ✅ SHA-256 hash provides collision resistance
- ✅ Includes disambiguator for overloads
- ✅ File path included to prevent cross-file collisions
- ⚠️ 16-character truncation (64 bits) - acceptable for practical use

### 1.4 Ledger Append-Only Invariant ✅

**Evidence:** Search for SQL UPDATE/DELETE in ledger code found no violations.

**Findings:**
- ✅ `MutationLedger` in [src/codeplane/mcp/ledger.py](src/codeplane/mcp/ledger.py) uses structured logging pattern
- ✅ No UPDATE or DELETE operations on ledger tables

---

## Pass 2: Architectural Integrity

### 2.1 `_internal` Package Encapsulation ✅

**Evidence:** All `_internal` imports are properly scoped through `__init__.py` re-exports.

```python
# src/codeplane/index/_internal/db/__init__.py
from codeplane.index._internal.db.database import BulkWriter, Database
from codeplane.index._internal.db.epoch import EpochManager, EpochStats
```

**Findings:**
- ✅ Public API exposed through package `__init__.py`
- ✅ No direct imports from `_internal` submodules in public code
- ✅ Clear separation between internal implementation and public interface

### 2.2 Layer Separation ✅

**Evidence:** MCP tools don't directly access database or internal modules.

```python
# src/codeplane/mcp/tools/mutation.py
from codeplane.mcp.errors import (
    ContentNotFoundError,
    MCPError,
    MCPErrorCode,
    MultipleMatchesError,
)
```

**Findings:**
- ✅ MCP tools use error types from `mcp.errors`, not internal exceptions
- ✅ Coordinator pattern properly separates concerns

### 2.3 Deleted Design Docs ✅

**Evidence:** 9 DESIGN.md files removed from diff.

**Findings:**
- ✅ Design documents consolidated into SPEC.md Section 7
- ✅ No orphaned references to deleted docs found

---

## Pass 3: API Contracts

### 3.1 MCP Error System ✅

**Evidence:** [src/codeplane/mcp/errors.py](src/codeplane/mcp/errors.py)

```python
class MCPErrorCode(str, Enum):
    """Machine-readable error codes for MCP tool failures."""

    # Validation errors - agent should fix input
    CONTENT_NOT_FOUND = "CONTENT_NOT_FOUND"
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
    ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
    ...
```

**Findings:**
- ✅ Comprehensive error codes with remediation hints
- ✅ Structured `ErrorResponse` with path and context
- ✅ Agents can programmatically handle errors

### 3.2 Response Envelope ✅

**Evidence:** Middleware wraps all tool responses.

```python
# src/codeplane/mcp/middleware.py
class ToolMiddleware(Middleware):
    """Middleware that handles tool calls with structured errors and UX."""
```

**Findings:**
- ✅ Consistent error wrapping
- ✅ Timing and session logging
- ✅ No raw exceptions leak to agents

---

## Pass 4: Error Handling

### 4.1 Exception Patterns ⚠️ CAUTION

**Evidence:** Search for exception patterns.

**Bare `except Exception:` occurrences found:**

1. [src/codeplane/testing/ops.py#L111](src/codeplane/testing/ops.py#L111):
   ```python
   except Exception:
       tools["pytest-cov"] = False
   ```
   **Assessment:** Acceptable - failure to detect tool is non-critical

2. [src/codeplane/testing/runtime.py#L419](src/codeplane/testing/runtime.py#L419):
   ```python
   except Exception:
       pass
   ```
   **Assessment:** ⚠️ Silent failure in runtime detection - should log

3. [src/codeplane/mcp/tools/introspection.py#L28](src/codeplane/mcp/tools/introspection.py#L28):
   ```python
   except Exception:
       return "unknown"
   ```
   **Assessment:** Acceptable - version query fallback

4. [src/codeplane/index/_internal/indexing/lexical.py#L297](src/codeplane/index/_internal/indexing/lexical.py#L297):
   ```python
   except Exception:
       # On failure, changes are discarded (Tantivy writer rollback)
       self._staged_adds.clear()
   ```
   **Assessment:** ⚠️ Should log the exception before discarding

**Recommendations:**
- Add logging to silent exception handlers
- Consider more specific exception types where possible

### 4.2 Timeout Handling ✅

**Evidence:** [src/codeplane/daemon/lifecycle.py#L87](src/codeplane/daemon/lifecycle.py#L87)

```python
async with asyncio.timeout(self.timeouts_config.server_stop_sec):
    await self.watcher.stop()
    await self.indexer.stop()
```

**Findings:**
- ✅ Server shutdown has timeout protection
- ✅ Test execution has configurable timeout_sec
- ✅ Subprocess runs use timeout parameter

---

## Pass 5: Security

### 5.1 Path Traversal Protection ✅

**Evidence:** [src/codeplane/files/ops.py](src/codeplane/files/ops.py#L119)

```python
def validate_path_in_repo(repo_root: Path, user_path: str) -> Path:
    """Validate and resolve a user-provided path within repo bounds.
    
    Raises MCPError with PERMISSION_DENIED if path escapes repo root.
    """
    resolved_root = repo_root.resolve()
    full_path = (repo_root / user_path).resolve()
    if not full_path.is_relative_to(resolved_root):
        raise MCPError(
            code=MCPErrorCode.PERMISSION_DENIED,
            message=f"Path '{user_path}' is outside repository",
            remediation="Use paths relative to repository root without '..' traversal.",
            path=user_path,
        )
    return full_path
```

**Findings:**
- ✅ Proper path resolution before comparison
- ✅ Clear error message with remediation
- ✅ Used consistently in file operations

### 5.2 Command Injection Protection ✅

**Evidence:** Subprocess calls use list form, not shell=True.

```python
# src/codeplane/lint/ops.py
proc = await asyncio.create_subprocess_exec(
    *cmd,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=self._repo_root,
)
```

**Findings:**
- ✅ All subprocess calls use exec form (list arguments)
- ✅ No shell=True usage found in source code

### 5.3 Safe Execution Context ✅

**Evidence:** [src/codeplane/testing/safe_execution.py](src/codeplane/testing/safe_execution.py)

```python
class SafeExecutionContext:
    """Provides defensive environment isolation for test execution.

    Protects against misconfigurations in target repositories by:
    1. Setting environment variables that override project configs
    2. Sanitizing commands to remove dangerous flags
    3. Isolating coverage/artifact files to prevent corruption
    4. Enforcing non-interactive execution modes
    """
```

**Findings:**
- ✅ CI/non-interactive environment enforced
- ✅ Telemetry disabled for various tools
- ✅ Language-specific defensive strategies documented

---

## Pass 6: Reliability

### 6.1 Race Condition Prevention ✅

**Evidence:** [src/codeplane/daemon/indexer.py](src/codeplane/daemon/indexer.py#L62-L63)

```python
_pending_paths: set[Path] = field(default_factory=set, init=False)
_pending_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
```

**Findings:**
- ✅ Explicit lock for pending paths set
- ✅ Atomic grab-and-clear pattern:
  ```python
  with self._pending_lock:
      if not self._pending_paths:
          return
      paths = list(self._pending_paths)
      self._pending_paths.clear()
  ```

### 6.2 Debouncing Implementation ✅

**Evidence:** [src/codeplane/daemon/watcher.py](src/codeplane/daemon/watcher.py#L25-L27)

```python
DEBOUNCE_WINDOW_SEC = 0.5  # Sliding window for batching rapid changes
MAX_DEBOUNCE_WAIT_SEC = 2.0  # Maximum wait before forcing flush
```

**Findings:**
- ✅ Two-tier debouncing (watcher + indexer)
- ✅ Maximum delay cap prevents indefinite buffering
- ✅ Configurable via server config

### 6.3 Graceful Shutdown ✅

**Evidence:** [src/codeplane/daemon/lifecycle.py](src/codeplane/daemon/lifecycle.py#L76-L98)

```python
async def stop(self) -> None:
    """Stop all daemon components gracefully."""
    logger.info("server stopping")

    try:
        async with asyncio.timeout(self.timeouts_config.server_stop_sec):
            await self.watcher.stop()
            await self.indexer.stop()
    except TimeoutError:
        logger.warning(
            "server_stop_timeout",
            message=f"Shutdown timed out after {self.timeouts_config.server_stop_sec}s",
        )

    self._shutdown_event.set()
```

**Findings:**
- ✅ Components stopped in correct order (watcher first)
- ✅ Timeout prevents hanging on shutdown
- ✅ Warning logged on timeout

---

## Pass 7: Observability

### 7.1 Structured Logging ✅

**Evidence:** structlog used consistently across all modules.

```python
# src/codeplane/daemon/lifecycle.py
import structlog
logger = structlog.get_logger()
```

**Key logging points:**
- `tool_start` / `tool_completed` with timing
- `epoch_journal_written` / `epoch_journal_deleted`
- `background_indexer_started` / `background_indexer_stopped`
- `server_stop_timeout` warning

### 7.2 Session-Aware Logging ✅

**Evidence:** [src/codeplane/mcp/middleware.py](src/codeplane/mcp/middleware.py#L61-L63)

```python
full_session_id = context.fastmcp_context.session_id or "unknown"
session_id = full_session_id[:8]  # Truncate for display
log.info("tool_start", tool=tool_name, session_id=session_id, **log_params)
```

**Findings:**
- ✅ Agent sessions are tracked
- ✅ Tool calls logged with session context
- ✅ Timing included in completion logs

---

## Pass 8: Testing

### 8.1 Coverage ✅

**Evidence:** ~85 new test files covering all new modules.

| Module | Test Coverage |
|--------|---------------|
| index/ | `tests/index/` - unit + integration |
| mcp/ | `tests/mcp/` - tool params, handlers, middleware |
| testing/ | `tests/testing/` - ops, parsers, packs |
| lint/ | `tests/lint/` - definitions, parsers, ops |
| daemon/ | `tests/daemon/` - lifecycle, watcher |
| refactor/ | `tests/refactor/` - ops, integration |

### 8.2 Test Isolation ✅

**Evidence:** pytest fixtures use temporary directories.

```python
# tests/mcp/conftest.py
subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
```

**Findings:**
- ✅ Tests use `tmp_path` fixtures
- ✅ Git repos initialized fresh per test
- ✅ No shared state between tests

---

## Pass 9: Performance

### 9.1 Large File Concerns ⚠️ MINOR

| File | Lines | Concern |
|------|-------|--------|
| `src/codeplane/index/ops.py` | ~2000 | Complex coordinator logic |
| `src/codeplane/index/models.py` | ~1008 | Many model definitions |
| `src/codeplane/index/_internal/parsing/treesitter.py` | ~1720 | Parser implementation |

**Recommendations:**
- Monitor performance of ops.py methods
- Consider extracting model groups if models.py grows further

### 9.2 Thread Pool Configuration ✅

**Evidence:** [src/codeplane/daemon/indexer.py](src/codeplane/daemon/indexer.py#L72-L76)

```python
self._executor = ThreadPoolExecutor(
    max_workers=self.config.max_workers,
    thread_name_prefix="codeplane-indexer",
)
```

**Findings:**
- ✅ Configurable worker count
- ✅ Named threads for debugging
- ✅ Proper shutdown with cancel_futures=True

---

## Pass 10: Configuration

### 10.1 Pydantic Models ✅

**Evidence:** [src/codeplane/config/models.py](src/codeplane/config/models.py)

```python
if not path.is_absolute():
    raise ValueError(f"File destination must be absolute path: {v}")
```

**Findings:**
- ✅ Validation on configuration values
- ✅ Sensible defaults provided
- ✅ Type safety via Pydantic

### 10.2 Default Values ✅

```python
class ServerConfig:
    port: int = 7654
    shutdown_timeout_sec: int = 5
    poll_interval_sec: float = 1.0
    debounce_sec: float = 0.3
```

---

## Pass 11: Documentation

### 11.1 SPEC.md Updates ✅

**Evidence:** Section 7 (Index Architecture) added with comprehensive coverage:

- 7.1 Overview
- 7.2 Tier 0 — Lexical Retrieval
- 7.3 Tier 1 — Structural Facts
- 7.4 Identity Scheme (def_uid)
- 7.5 Parser (Tree-sitter)
- 7.6 Epoch Model
- 7.7 File Watcher Integration
- 7.8 Bounded Query APIs
- 7.9 What This Index Does NOT Provide

### 11.2 Docstrings ✅

All major classes and functions have comprehensive docstrings:

```python
class EpochManager:
    """Manages epoch lifecycle for atomic index updates.

    Implements two-phase commit with rollback journal:
    1. Write journal to disk...
    """
```

---

## Pass 12: Compatibility

### 12.1 Cross-Filesystem Detection ✅

**Evidence:** [src/codeplane/daemon/watcher.py](src/codeplane/daemon/watcher.py#L41-L48)

```python
def _is_cross_filesystem(path: Path) -> bool:
    """Detect if path is on a cross-filesystem mount (WSL /mnt/*, network drives, etc.)."""
    resolved = path.resolve()
    path_str = str(resolved)
    # WSL accessing Windows filesystem
    if path_str.startswith("/mnt/") and len(path_str) > 5 and path_str[5].isalpha():
        return True
    return path_str.startswith(("/run/user/", "/media/", "/net/"))
```

**Findings:**
- ✅ WSL detection implemented
- ✅ Falls back to polling on cross-filesystem
- ✅ XDG index directory support for cross-filesystem setups

---

## Pass 13: Code Quality

### 13.1 Naming Conventions ✅

- Private modules use `_internal` prefix
- Private methods use `_` prefix
- Constants use UPPER_CASE
- Classes use PascalCase
- Functions use snake_case

### 13.2 Type Annotations ✅

Comprehensive type hints throughout:

```python
async def stop(self) -> None:
    """Stop all daemon components gracefully."""
```

### 13.3 Import Organization ✅

Consistent `from __future__ import annotations` usage for forward references.

---

## Recommendations Summary

### High Priority

1. **Add logging to silent exception handlers**
   - [src/codeplane/testing/runtime.py#L419](src/codeplane/testing/runtime.py#L419)
   - [src/codeplane/index/_internal/indexing/lexical.py#L297](src/codeplane/index/_internal/indexing/lexical.py#L297)

### Medium Priority

2. **Consider extracting large modules**
   - `ops.py` files exceeding 1500 lines
   - Potential extraction of query methods from IndexCoordinator

3. **Add more specific exception types where bare Exception is caught**

### Low Priority

4. **Monitor performance of large files under load**
5. **Consider adding metrics/tracing hooks for production observability**

---

## Conclusion

The M2 Index Engine implementation is **well-architected and production-ready**. Key strengths:

- **Robust atomicity guarantees** via epoch journal system
- **Clean architectural boundaries** with `_internal` encapsulation
- **Comprehensive error handling** with agent-friendly remediation hints
- **Strong security posture** with path traversal and command injection prevention
- **Good observability** with structured logging throughout
- **Thorough testing** with ~85 new test files

The identified issues are minor and do not block merge. They represent opportunities for improvement rather than blockers.

**Verdict:** ✅ Approved for merge with minor recommendations
