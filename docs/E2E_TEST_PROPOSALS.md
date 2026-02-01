# E2E Test Suite: Real-World Repository Indexing

This document defines the truth-based, polyglot-capable end-to-end test suite
for validating the CodePlane indexing infrastructure. Tests validate
correctness, scalability, and incremental behavior of the Tier 0 + Tier 1
syntactic index, without asserting semantic or cross-language linkage.

> **Implementation Status:** The core infrastructure is implemented in
> `tests/e2e/`. This document serves as both specification and implementation
> guide.

---

## Architecture: Subprocess-Based CLI Testing

E2E tests exercise the **actual CLI** via subprocess, not internal APIs.
This ensures tests validate the real user experience.

```
+-------------------+     subprocess      +-------------------+
|  pytest + E2E     | ------------------> |   cpl init/up     |
|    fixtures       |                     |   (real CLI)      |
+---------+---------+                     +---------+---------+
          |                                         |
          |  query SQLite                           |  writes
          v                                         v
    +-----------------------------------------------------+
    |              .codeplane/index.db                    |
    |              .codeplane/tantivy/                    |
    +-----------------------------------------------------+
```

### Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `cli_runner.py` | `tests/e2e/` | `IsolatedEnv` with venv + cpl installed from source |
| `repo_cache.py` | `tests/e2e/` | Shallow clone caching and materialization |
| `anchors_loader.py` | `tests/e2e/` | Load per-repo anchor symbol specs |
| `budgets_loader.py` | `tests/e2e/` | Load performance budgets |
| `conftest.py` | `tests/e2e/` | Fixtures: `e2e_repo`, `initialized_repo`, markers |

### Isolated Environment

Tests create an **isolated venv** with codeplane installed from source:

```python
@pytest.fixture(scope="session")
def isolated_env(tmp_path_factory) -> IsolatedEnv:
    env = create_isolated_env(base_path, "cpl_test")
    # env.run_cpl(["init"], cwd=repo_path) runs via subprocess
    return env
```

This ensures:
- No test pollution from development environment
- Real CLI entrypoint exercised
- Actual installation process validated

---

## Goals

1. **Validate scalability**
   Index repositories ranging from 10K-500K LOC without OOM or timeouts.

2. **Validate polyglot support**
   Correctly index multiple languages and contexts in a single repo.

3. **Validate correctness**
   Assert presence and metadata of specific anchor symbols, not just counts.

4. **Validate incremental behavior**
   Prove single-file edits only reindex affected files.

5. **Validate daemon lifecycle**
   Ensure `cpl up`/`cpl down`/`cpl status` work correctly.

6. **Enforce performance budgets**
   Fail tests when time or memory limits are exceeded.

---

## Repository Tiers

### Tier 1: Small Single-Language Repos (1K-10K LOC)

Purpose: correctness and fast feedback on every PR.

| Repository | Language | Status |
|-----------|----------|--------|
| pallets/click | Python | Implemented |
| psf/requests | Python | Implemented |
| python-attrs/attrs | Python | Implemented |
| more-itertools/more-itertools | Python | Implemented |

### Tier 2: Medium Single-Language Repos (10K-50K LOC)

Purpose: stress syntax extraction and incremental updates. Run nightly.

| Repository | Language | Status |
|-----------|----------|--------|
| pallets/flask | Python | Implemented |
| pydantic/pydantic | Python | Implemented |
| fastapi/fastapi | Python | Implemented |

### Tier 3: Polyglot / Multi-Context Repos

Purpose: validate context discovery, routing, and multi-language indexing.

| Requirement | Status |
|-------------|--------|
| 2+ contexts/workspaces | Not yet specified |
| 2+ languages | Not yet specified |
| Pinned commit SHAs | Not yet specified |

**Candidates:**
- JS/TS monorepo with workspaces
- Rust workspace with multiple crates
- Python + Rust mixed repo

---

## Truth-Based Validation: Anchor Symbols

Tests validate **specific anchor symbols** per repo, not just counts.

Anchor definitions in `tests/e2e/anchors/<owner>_<repo>.yaml`:

```yaml
# tests/e2e/anchors/pallets_click.yaml
repo: pallets/click
commit: 8.1.8
contexts:
  - root: "."
    language: python
    anchors:
      - name: Group
        kind: class
        file: src/click/core.py
        line_range: [1, 3000]
      - name: echo
        kind: function
        file: src/click/utils.py
        line_range: [1, 500]

search_queries:
  - query: "class Group"
    expected_path_contains: "core.py"
```

**Anchor assertions:**
- Exactly one matching `DefFact` exists in expected context
- `file_id -> File.path` ends with expected file
- `start_line` is within `line_range`
- `kind` matches exactly

---

## Performance Budgets

Budgets defined in `tests/e2e/budgets.json`:

```json
{
  "pallets/click": {
    "full_index_seconds": 30,
    "incremental_seconds": 5,
    "max_rss_mb": 1500
  },
  "pydantic/pydantic": {
    "full_index_seconds": 120,
    "incremental_seconds": 15,
    "max_rss_mb": 2500
  }
}
```

Budgets are enforced in tests:

```python
def test_full_index_within_budget(initialized_repo: InitResult) -> None:
    budget = initialized_repo.repo.budget
    assert initialized_repo.duration_seconds <= budget.full_index_seconds
    if initialized_repo.peak_rss_mb > 0:
        assert initialized_repo.peak_rss_mb <= budget.max_rss_mb
```

---

## Test Scenarios

### Scenario 1: Full Index from Scratch

**Location:** `tests/e2e/test_full_index.py`

**Status:** Implemented

**Tests:**
- `test_full_index_within_budget` - Performance budget enforcement
- `test_contexts_discovered` - Language contexts present
- `test_anchor_symbols_present` - All anchors validate
- `test_files_indexed` - Files table populated
- `test_defs_extracted` - DefFacts extracted
- `test_database_created` - SQLite DB exists
- `test_tantivy_index_created` - Tantivy directory exists
- `test_config_created` - Config file written
- `test_cplignore_created` - .cplignore written

**Flow:**
```
1. Clone repo at pinned SHA (shallow)
2. Copy to tmp_path for isolation
3. Run `cpl init` via subprocess
4. Query .codeplane/index.db directly
5. Assert anchors, budgets, artifacts
```

### Scenario 2: Incremental Update

**Location:** `tests/e2e/test_incremental.py`

**How it works:** Incremental reindexing happens automatically via the
daemon/watcher when files change. There is no `cpl reindex` CLI command.

**Implemented:**
- `test_reinit_includes_new_file` - Re-init picks up new files
- `test_reinit_removes_deleted_file` - Re-init removes deleted files

**Daemon-based incremental testing:** Covered in Scenario 3 via
`test_given_daemon_when_file_modified_then_reindex_triggered`.

### Scenario 3: Daemon Lifecycle

**Location:** `tests/e2e/test_daemon.py`

**Status:** Implemented

**Tests:**
- `test_given_repo_when_cpl_up_then_daemon_starts`
- `test_given_running_daemon_when_cpl_down_then_daemon_stops`
- `test_given_running_daemon_when_cpl_status_then_shows_running`
- `test_given_no_daemon_when_cpl_status_then_shows_not_running`
- `test_given_daemon_when_file_modified_then_reindex_triggered` (slow)

**Flow:**
```
1. Create minimal git repo
2. Run `cpl init`
3. Run `cpl up` -> verify PID/port files
4. Run `cpl status` -> verify output
5. Run `cpl down` -> verify cleanup
```

### Scenario 4: Search Quality

**Location:** `tests/e2e/test_search.py`

**How it works:** Search is exposed via MCP tools only, not CLI.
These tests validate the underlying indexed data that powers search.

**Implemented:**
- `test_anchor_symbols_in_database` - Direct SQLite lookup
- `test_def_facts_populated` - Table has rows
- `test_files_table_populated` - Table has rows

### Scenario 5: Query Performance

**Location:** `tests/e2e/test_search.py`

**Status:** Implemented

**Tests:**
- `test_def_query_under_budget` - 20 symbol lookups under 1s
- `test_file_listing_under_budget` - File listing under 500ms
- `test_def_count_by_kind_under_budget` - Grouped count under 500ms

---

## Test Infrastructure

### Repository Cache

`tests/e2e/repo_cache.py` implements:

- **Shallow clones:** `git clone --depth=1 --branch <tag>`
- **Cache validation:** `git fsck` on cached repos
- **Auto-repair:** Wipe and re-clone if corrupted
- **Materialization:** Copy to `tmp_path` for mutation tests

Cache location: `~/.codeplane-test-cache/<owner>_<repo>/`

### Pytest Markers

```python
# In conftest.py
config.addinivalue_line("markers", "e2e: mark test as end-to-end")
config.addinivalue_line("markers", "slow: mark test as slow running")
config.addinivalue_line("markers", "tier1: mark test for Tier 1 repos")
config.addinivalue_line("markers", "tier2: mark test for Tier 2 repos")
config.addinivalue_line("markers", "nightly: mark test for nightly runs")
```

### Running E2E Tests

```bash
# Run all E2E tests (slow)
pytest tests/e2e/ -m e2e

# Run Tier 1 only (faster)
pytest tests/e2e/ -m "e2e and tier1"

# Run with specific repo
pytest tests/e2e/test_full_index.py -k "click"

# Skip slow tests
pytest tests/e2e/ -m "e2e and not slow"
```

---

## CI/CD Integration

### PR Workflow

```yaml
# Run Tier 1 only on PRs
pytest tests/e2e/ -m "e2e and tier1" --timeout=300
```

- Enforces tight budgets
- Fast feedback (~2-3 min)

### Nightly Workflow

```yaml
# Run all tiers nightly
pytest tests/e2e/ -m e2e --timeout=600
```

- Includes Tier 2 + Tier 3
- Slightly relaxed budgets
- Full coverage

---

## Implementation Checklist

### Completed

- [x] `cli_runner.py` - Isolated venv with subprocess execution
- [x] `repo_cache.py` - Shallow clone caching
- [x] `anchors_loader.py` - YAML anchor spec loader
- [x] `budgets_loader.py` - JSON budget loader
- [x] `conftest.py` - Core fixtures and markers
- [x] `test_full_index.py` - Scenario 1 tests
- [x] `test_incremental.py` - Scenario 2 tests
- [x] `test_daemon.py` - Scenario 3 tests
- [x] `test_search.py` - Scenario 4 + 5 tests
- [x] Anchor specs for all Tier 1 repos
- [x] Anchor specs for all Tier 2 repos
- [x] Budget specs for all repos

### Not Started

- [ ] Tier 3 polyglot repos
- [ ] CI workflow integration (GitHub Actions)

---

## Non-Goals

- Cross-language linkage or semantic resolution
- pytest-benchmark (too flaky in CI)
- Coverage of internal APIs (that is unit test territory)
- Testing enormous repos (>500K LOC) on every PR

---

## Migration Notes

**From previous proposal:**

The original proposal described direct API invocation:
```python
coord = IndexCoordinator(repo_path)
result = asyncio.run(coord.initialize())
```

**Current implementation** uses subprocess-based CLI:
```python
result, peak_rss = e2e_repo.env.run_cpl(["init"], cwd=e2e_repo.path)
```

This change ensures:
1. CLI parsing and entrypoints are tested
2. Real installation process is validated
3. Tests match actual user experience
4. No internal API coupling in E2E tests
