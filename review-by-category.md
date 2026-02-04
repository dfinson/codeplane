# PR Review: `dfinson/feature/m2-index-engine` vs `main`

## Executive Summary

**Pending Items Status:**
- **Design decisions needed:** 6 items with comprehensive analyses awaiting decision
- **Implementation proposals:** 20+ items proposed but not yet implemented
- **Resolved items:** 7 items (1.1-1.5, 2.2, 2.3) - removed from this document

This document contains **only items that require attention** — either design decisions or future implementation.

---

## Table of Contents

### Part A: Design Decisions Needed

These items have comprehensive analyses complete and await stakeholder decision:

1. [3.2 Input Validation Hardcoding](#32-input-validation-hardcoding-analysis)
2. [3.3 Pagination Cursor Stale Lock Design](#33-pagination-cursor-stale-lock-design)
3. [5.0 Security CI Design](#50-security-ci-design)
4. [7.2 OpenTelemetry Design](#72-opentelemetry-design)
5. [10.1 Configuration System Design](#101-configuration-system-design)
6. [13.0 CI Quality Gates Design](#130-ci-quality-gates-design)

### Part B: Implementation Proposals

These items have proposed solutions but await prioritization and implementation:

- [Pass 3: API Contract & Input Validation](#pass-3-api-contract--input-validation)
- [Pass 4: Error Handling & Resilience](#pass-4-error-handling--resilience)
- [Pass 5: Security Review](#pass-5-security-review)
- [Pass 6: Reliability & Data Integrity](#pass-6-reliability--data-integrity)
- [Pass 7: Observability & Debugging](#pass-7-observability--debugging)
- [Pass 8: Testing Strategy](#pass-8-testing-strategy)
- [Pass 9: Performance & Scalability](#pass-9-performance--scalability)
- [Pass 10: Configuration & Deployment](#pass-10-configuration--deployment)
- [Pass 11: Documentation Alignment](#pass-11-documentation-alignment)
- [Pass 12: Backward Compatibility](#pass-12-backward-compatibility)
- [Pass 13: Code Quality & Maintainability](#pass-13-code-quality--maintainability)

---

# Part A: Design Decisions Needed

---

## 3.2 Input Validation Hardcoding: Analysis

**Status:** ❓ ANALYSIS COMPLETE - Decision required on defaults vs. configurability

**Problem:** Default values are hardcoded across the codebase without clear documentation or configuration options.

**Evidence:**

```python
# ops.py
limit: int = 20,  # hardcoded default

# Various tools
default_timeout = 30.0  # seconds
max_results = 500
batch_size = 100
```

**Analysis: Hardcoded vs. Configurable**

| Setting | Current Default | Risk of Configurability | Recommendation |
|---------|-----------------|------------------------|----------------|
| `limit` defaults | 20-100 | Low - user preference | Make configurable |
| `timeout` | 30s | Medium - can cause hangs or premature failures | Make configurable with guardrails |
| `max_results` | 500 | Low - memory/performance tradeoff | Keep hardcoded, document |
| `batch_size` | 100 | High - affects atomicity and memory | Keep hardcoded |

**Evidence-Backed Defaults Analysis:**

1. **`limit` (20-100)**: Industry standard is 10-50 for paginated APIs. GitHub uses 30, Elasticsearch uses 10.
   - Recommendation: Default 20, max 100, configurable

2. **`timeout` (30s)**: Depends on operation. Reads: 5-10s. Writes/reindex: 60-300s.
   - Recommendation: Per-operation defaults, global override via env var

3. **`max_results` (500)**: Memory-bound. 500 * ~1KB = ~500KB reasonable.
   - Recommendation: Keep hardcoded, document rationale

4. **`batch_size` (100)**: Affects transaction size and memory. 100 files * ~10KB = ~1MB.
   - Recommendation: Keep hardcoded, optimize based on profiling

**Decision Options:**

| Option | Description | Effort | Risk |
|--------|-------------|--------|------|
| A | Keep all hardcoded, document rationale | Low | Users may hit limits |
| B | Make `limit`/`timeout` configurable via env vars | Medium | Configuration sprawl |
| C | Full config system (see §10.1) | High | Over-engineering |

**Recommendation:** Option B - Selective configurability for user-facing limits.

---

## 3.3 Pagination Cursor Stale Lock Design

**Status:** ❓ ANALYSIS COMPLETE - Recommend Option A for v1, Option B deferred.

**Problem:** Pagination cursors can become stale if the underlying index changes during iteration.

**Analysis: Current Implementation**

Cursors encode:
- Offset or last-seen ID
- No epoch/version stamp

**Risk Matrix:**

| Scenario | Current Behavior | Impact |
|----------|-----------------|--------|
| Reindex during pagination | Results may skip or duplicate | Medium |
| File deleted during pagination | Stale references in results | Low |
| Epoch advance during pagination | Potential inconsistency | Medium |

---

### Design Option A: Epoch-Stamped Cursors (Simple)

```python
@dataclass
class PaginationCursor:
    offset: int
    created_epoch: int
    query_hash: str

def validate_cursor(self, cursor: PaginationCursor) -> bool:
    current_epoch = self._epoch_service.current_epoch()
    if cursor.created_epoch != current_epoch:
        raise MCPError(
            MCPErrorCode.CURSOR_STALE,
            f"Index changed since cursor creation (epoch {cursor.created_epoch} → {current_epoch})",
            remediation="Restart pagination from the beginning."
        )
    return True
```

Pros:
- Simple to implement
- Clear error semantics
- No memory overhead

Cons:
- Any reindex invalidates all cursors
- Users must restart pagination

---

### Design Option B: Auto-Expiring Read Locks (Medium)

```python
@dataclass
class ReadLock:
    id: str
    paths: set[str]
    created_at: float
    ttl_seconds: float = 300  # 5 minutes

class LockManager:
    _locks: dict[str, ReadLock] = {}
    
    def _expire_stale_locks(self):
        now = time.time()
        expired = [lid for lid, lock in self._locks.items() 
                   if now - lock.created_at > lock.ttl_seconds]
        for lid in expired:
            del self._locks[lid]
    
    def acquire_read_lock(self, cursor_id: str, paths: set[str]) -> ReadLock:
        self._expire_stale_locks()
        lock = ReadLock(id=cursor_id, paths=paths, created_at=time.time())
        self._locks[cursor_id] = lock
        return lock
    
    def allow_write(self, paths: set[str]) -> bool:
        """Returns True if write is allowed (no active read locks on paths)."""
        self._expire_stale_locks()
        for lock in self._locks.values():
            if paths & lock.paths:
                return False
        return True
```

Pros:
- Consistent reads during pagination
- Explicit write coordination
- Auto-expiry prevents deadlocks

Cons:
- Added complexity
- Potential for delayed writes during heavy pagination
- Needs cleanup on client disconnect

---

### Design Option C: Snapshot Isolation (Heavy)

```python
class SnapshotManager:
    """Maintain read-only snapshots for pagination sessions."""
    
    def create_snapshot(self, epoch_id: int) -> str:
        snapshot_id = str(uuid.uuid4())
        self._snapshots[snapshot_id] = epoch_id
        return snapshot_id
```

Pros:
- True isolation
- Writes never blocked

Cons:
- Heavy resource usage
- Complex Tantivy segment pinning
- Overkill for CodePlane use case

---

**Recommendation:**
- **v1:** Option A (Epoch-Stamped Cursors)
- **v2 (if needed):** Option B (Auto-Expiring Read Locks)

---

## 5.0 Security CI Design

**Status:** ❓ DESIGN PROPOSED - Recommend Option A for immediate implementation.

---

### Design Option A: GitHub-Native Security (Recommended for v1)

**Tools:**
- **Dependabot**: Automated dependency vulnerability scanning + PR creation
- **CodeQL**: SAST for Python/JS
- **Secret Scanning**: Detect accidentally committed credentials

```yaml
# .github/workflows/security.yml
name: Security Scan
on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday scan

jobs:
  codeql:
    runs-on: ubuntu-latest
    permissions:
      security-events: write
    steps:
      - uses: github/codeql-action/init@v3
        with:
          languages: python
      - uses: github/codeql-action/analyze@v3

  dependency-review:
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/dependency-review-action@v4
        with:
          fail-on-severity: high
```

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
```

Pros:
- Zero infrastructure cost
- Automatic PR creation for fixes
- Good Python/JS coverage

Cons:
- CodeQL slower than commercial alternatives
- Limited to GitHub-supported languages

---

### Design Option B: OSS Security Stack (Full Control)

**Tools:** Bandit, Semgrep, Trivy, gitleaks

Pros:
- Full control over rules
- Faster than CodeQL (~10x)
- Works on any CI platform

Cons:
- More maintenance burden
- Need to manage rule updates

---

### Design Option C: Commercial + OSS Hybrid (Enterprise)

**Tools:** Snyk, SonarCloud, GitHub Advanced Security

Pros:
- Best-in-class detection
- Lower false positive rates

Cons:
- Cost ($50-200/month)
- Vendor lock-in

---

**Recommendation:**
- **v1:** Option A (GitHub-Native)
- **v2:** Add Bandit + gitleaks selectively

---

## 7.2 OpenTelemetry Design

**Status:** ❓ DESIGN PROPOSED - Recommend Option A with lazy initialization.

---

### Design Option A: Full OpenTelemetry SDK (Recommended)

**Components:** Traces, Metrics, Logs bridge

```python
# src/codeplane/core/telemetry.py
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from functools import wraps
import os

_tracer: trace.Tracer | None = None

def init_telemetry():
    """Initialize OpenTelemetry if OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return  # No-op if not configured
    
    trace_provider = TracerProvider()
    trace_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
    )
    trace.set_tracer_provider(trace_provider)
    global _tracer
    _tracer = trace.get_tracer("codeplane")

def traced(name: str = None):
    """Decorator to trace function execution."""
    def decorator(func):
        span_name = name or func.__name__
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if _tracer is None:
                return await func(*args, **kwargs)
            with _tracer.start_as_current_span(span_name) as span:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    span.record_exception(e)
                    raise
        return async_wrapper
    return decorator
```

Pros:
- Full observability
- Zero-config when endpoint unset
- Future-proof for enterprise

Cons:
- ~10MB dependencies

---

### Design Option B: Metrics-Only (Lighter)

Prometheus counters and histograms only.

---

### Design Option C: Zero-Dep Structured Events

JSON events to stderr, parseable by any backend.

---

### Instrumentation Points

| Component | Span/Metric | Attributes |
|-----------|-------------|------------|
| `IndexCoordinator.initialize` | Span | contexts_discovered, duration |
| `IndexCoordinator.search` | Span + Histogram | mode, result_count, latency |
| `IndexCoordinator.reindex_*` | Span + Counter | files_added, files_removed |
| `Database.session` | Span (auto via SQLAlchemy) | query, duration |
| MCP tool handlers | Span | tool_name, success |

---

**Recommendation:** Option A with lazy initialization.

---

## 10.1 Configuration System Design

**Status:** ❓ DESIGN PROPOSED - Recommend Option B for v1.

---

### Design Option A: Opinionated (Zero Config)

```python
class Config:
    DB_PATH: Path = Path(".codeplane/index.db")
    TANTIVY_PATH: Path = Path(".codeplane/tantivy")
    BATCH_SIZE = 100  # Hardcoded
    DEBOUNCE_SEC = 0.5  # Hardcoded
```

Pros: Zero config surface, easier testing
Cons: Can't tune for specific hardware

---

### Design Option B: Balanced (Recommended)

Env vars for paths and tunables:

```python
@dataclass
class Settings:
    db_path: Path = Path(os.environ.get("CODEPLANE_DB_PATH", ".codeplane/index.db"))
    
    @cached_property
    def batch_size(self) -> int:
        return int(os.environ.get("CODEPLANE_BATCH_SIZE", 100))
```

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `CODEPLANE_DB_PATH` | `.codeplane/index.db` | SQLite database path |
| `CODEPLANE_BATCH_SIZE` | `100` | Files per batch |
| `CODEPLANE_DEBOUNCE_SEC` | `0.5` | Watcher debounce window |

Pros: Works out of box, power users can tune
Cons: Can't express complex config

---

### Design Option C: Flexible (Full Config File)

YAML config file with Pydantic validation.

Pros: Full expressiveness, per-project config
Cons: Complexity, maintenance burden

---

**Recommendation:** Option B for v1, Option C if users request per-project config.

---

## 13.0 CI Quality Gates Design

**Status:** ❓ DESIGN PROPOSED - Recommend Option A + coverage advisory for v1.

---

### Design Option A: Minimal Quality Gates (Recommended for v1)

**Gates:**
1. All tests pass
2. Lint clean (ruff)
3. Type check clean (mypy)
4. No new security vulnerabilities (Dependabot)

```yaml
jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv run ruff check src/ tests/
      - run: uv run mypy src/
      - run: uv run pytest -x --tb=short
```

Pros: Simple, fast (<5 min), clear pass/fail
Cons: No coverage tracking

---

### Design Option B: Comprehensive Quality Gates

**Additional Gates:**
- Coverage ≥ 80%
- Complexity limits
- Documentation coverage
- TODO/FIXME validation

Pros: Comprehensive enforcement
Cons: Slower (~10-15 min), may block urgent fixes

---

### Design Option C: Tiered Gates (Balanced)

**Tier 1 (Blocking):** Tests, lint, type check, security
**Tier 2 (Warning):** Coverage, complexity
**Tier 3 (Info):** TODO count, duplicates

Pros: Prevents false-positive blocking
Cons: More complex CI config

---

**Recommendation:** Option A + coverage advisory for v1, full Option C for v2.

---

# Part B: Implementation Proposals

---

## Pass 3: API Contract & Input Validation

### 3.1 MCPResponse Envelope Inconsistency (MEDIUM)

**Problem:** Some tools return structured `MCPResponse`, others return raw data.

**Proposed Solution:**
```python
# Wrapper decorator for consistent envelope
def mcp_response(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        return MCPResponse(data=result, timestamp=time.time())
    return wrapper
```

---

## Pass 4: Error Handling & Resilience

### 4.1 Silent Exception Swallowing (CRITICAL)

**Problem:** Multiple locations catch exceptions and continue silently.

**Proposed Solution:**
```python
import structlog
logger = structlog.get_logger()

for pack_class, _confidence in detected_packs:
    try:
        targets = await pack.discover(ws_root)
    except Exception as e:
        logger.warning(
            "test_discovery_failed",
            pack_id=pack.pack_id,
            error=str(e),
            exc_info=True,
        )
        continue  # Still continue, but now observable
```

---

### 4.2 Missing Error Propagation in Async Chains (HIGH)

**Problem:** Async methods that spawn background tasks don't propagate errors.

**Proposed Solution:**
```python
class BackgroundIndexer:
    _error_queue: asyncio.Queue[Exception]
    
    async def check_health(self) -> list[Exception]:
        errors = []
        while not self._error_queue.empty():
            errors.append(await self._error_queue.get())
        return errors
```

---

### 4.3 Incomplete Retry Logic for Network Operations (MEDIUM)

**Problem:** Git remote operations have no retry logic.

**Proposed Solution:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def fetch_remote(self, remote: str):
    self._repo.remotes[remote].fetch()
```

---

## Pass 5: Security Review

### 5.1 Path Traversal in Read Operations (CRITICAL)

**Problem:** Path validation appears insufficient.

**Proposed Solution:**
```python
def validate_path(self, user_path: str) -> Path:
    full = (self.repo_root / user_path).resolve()
    try:
        full.relative_to(self.repo_root.resolve())
    except ValueError:
        raise MCPError(
            MCPErrorCode.PERMISSION_DENIED,
            f"Path '{user_path}' is outside repository"
        )
    return full
```

---

### 5.2 SQL Injection via Raw Queries (MEDIUM)

**Problem:** Some SQL queries use string formatting.

**Proposed Solution:** Use SQLAlchemy's `in_()` for cleaner parameterization.

---

### 5.3 Sensitive Data in Error Messages (LOW)

**Problem:** Some error messages may leak file content.

**Proposed Solution:** Truncate and sanitize error snippets.

---

## Pass 6: Reliability & Data Integrity

### 6.1 Missing Integrity Verification (HIGH)

**Problem:** `IntegrityChecker` class implementation not found in diff.

**Proposed Solution:** Implement comprehensive integrity checks for FK violations, orphaned facts, Tantivy-SQLite sync.

---

### 6.2 WAL Mode Checkpoint Handling (MEDIUM)

**Problem:** No explicit checkpoint strategy for SQLite WAL mode.

**Proposed Solution:** Periodic `PRAGMA wal_checkpoint(PASSIVE)` based on transaction count.

---

### 6.3 Crash Recovery Protocol Missing (HIGH)

**Problem:** No documented or implemented crash recovery protocol.

**Proposed Solution:** Implement `RecoveryManager` that checks for incomplete epoch journals, checkpoints WAL, verifies Tantivy consistency.

---

## Pass 7: Observability & Debugging

### 7.1 Insufficient Structured Logging (MEDIUM)

**Problem:** Many operations lack structured logging.

**Proposed Solution:** Add structlog bindings with operation context, timing, and error details.

---

### 7.3 Debug Mode Verbosity (LOW)

**Problem:** No way to enable verbose debugging without code changes.

**Proposed Solution:** `CODEPLANE_DEBUG` env var for verbose logging.

---

## Pass 8: Testing Strategy

### 8.1 Test Coverage Gaps (HIGH)

**Coverage Assessment:**

| Module | Coverage Assessment |
|--------|--------------------|
| `index/` | GOOD |
| `daemon/` | MEDIUM - missing watcher edge cases |
| `refactor/` | LOW - needs more coverage |
| `mutation/` | LOW |

**Missing Test Scenarios:**
- Epoch atomicity failures
- Concurrent write conflicts
- Tantivy-SQLite desync
- Large file handling
- Unicode edge cases

---

### 8.2 Integration Test Isolation (MEDIUM)

**Problem:** Integration tests may leave temp files.

**Proposed Solution:** Fixtures with guaranteed cleanup.

---

### 8.3 Missing Property-Based Tests (MEDIUM)

**Problem:** Complex parsing/serialization lacks property-based testing.

**Proposed Solution:** Hypothesis tests for parser roundtrips and search robustness.

---

## Pass 9: Performance & Scalability

### 9.1 Full Table Scan in Hot Path (HIGH)

**Problem:** Several queries perform full table scans.

**Proposed Solution:** Use streaming with `yield_per` for large result sets.

---

### 9.2 Unbounded Memory in Batch Operations (MEDIUM)

**Problem:** `_walk_all_files` loads entire result into memory.

**Proposed Solution:** Generator-based file walking.

---

### 9.3 Missing Connection Pooling Tuning (LOW)

**Problem:** SQLite connection pool may not be optimized.

**Proposed Solution:** Configure `pool_size`, `pool_pre_ping`.

---

## Pass 10: Configuration & Deployment

### 10.2 Missing Health Check Details (LOW)

**Problem:** Health check endpoint lacks detail.

**Proposed Solution:** Return component health, uptime, version.

---

## Pass 11: Documentation Alignment

### 11.1 SPEC.md Drift (HIGH)

**Problem:** Implementation has drifted from SPEC.md.

**Discrepancies:**

| SPEC.md Claim | Implementation Reality |
|--------------|----------------------|
| §7.6: "Atomically committed" | Tantivy commits per-file |
| §8.5a: "Two-Phase Rename" | Missing decision capsules |

**Proposed Solution:** Update SPEC.md to reflect reality or fix implementation.

---

### 11.2 Missing API Documentation (MEDIUM)

**Problem:** MCP tool documentation incomplete.

**Proposed Solution:** Add comprehensive docstrings with examples.

---

## Pass 12: Backward Compatibility

### 12.1 Database Schema Migration Missing (CRITICAL)

**Problem:** No migration path from previous schema versions.

**Impact:** Existing users upgrading will lose index data.

**Proposed Solution:** Implement schema versioning and migration (Alembic or version-based).

---

### 12.2 Config Format Changes (MEDIUM)

**Problem:** Configuration format changes may break existing configs.

**Proposed Solution:** Version-aware config loading with migration.

---

## Pass 13: Code Quality & Maintainability

### 13.1 Type Annotation Gaps (MEDIUM)

**Problem:** Several functions lack complete type annotations.

**Proposed Solution:** Run `mypy --strict` and fix all errors.

---

### 13.2 God Object Pattern (MEDIUM)

**Problem:** `IndexCoordinator` has ~1600 lines with too many responsibilities.

**Proposed Solution:** Extract into focused classes:
- `DiscoveryService`
- `IndexingService`
- `QueryService`
- `EpochService`
- `IntegrityService`

---

### 13.3 Magic Strings (LOW)

**Problem:** String literals used instead of enums.

**Proposed Solution:** Use proper enum types for comparisons.

---

## Priority Summary

### P0 - Critical (Before Merge)

| Issue | Location | Status |
|-------|----------|--------|
| Path traversal risk | Multiple | Proposal |
| Database migration missing | database.py | Proposal |
| Silent exception swallowing | Multiple | Proposal |

### P1 - High (Follow-up)

| Issue | Location | Status |
|-------|----------|--------|
| Missing crash recovery | New file | Proposal |
| Test coverage gaps | tests/ | Proposal |
| SPEC.md drift | Multiple | Proposal |

### Decisions Pending

| Design | Options | Recommendation |
|--------|---------|---------------|
| Input Validation (3.2) | A/B/C | B (selective configurability) |
| Pagination Locking (3.3) | A/B/C | A (epoch cursors for v1) |
| Security CI (5.0) | A/B/C | A (GitHub-native) |
| OpenTelemetry (7.2) | A/B/C | A (full SDK, lazy init) |
| Configuration (10.1) | A/B/C | B (env vars) |
| CI Quality Gates (13.0) | A/B/C | A + coverage advisory |

---

## Resolved Items (Removed)

The following items were implemented and have been removed from this document:

- **1.1 Epoch Atomicity** → Two-phase commit + Tantivy staging implemented
- **1.2 Watcher Debouncing** → Sliding window debounce implemented
- **1.3 SQLite Busy Timeout** → Retry with exponential backoff implemented
- **1.4 Incremental Reindex** → File records created before structural indexing
- **1.5 Refactor Certainty** → RefTier-based classification implemented
- **2.1 Architecture Analysis** → Tantivy-SQLite separation is sound
- **2.2 Context Discovery** → Depth-descending sort implemented
- **2.3 Lock Hierarchy** → Verified correct, documented
