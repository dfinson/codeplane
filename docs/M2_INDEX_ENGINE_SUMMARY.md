# M2 Index Engine Branch Summary

**Branch:** `dfinson/feature/m2-index-engine`  
**Base:** `main`  
**Date:** February 1, 2026  
**Stats:** 108 files changed, +20,313 lines, -5,283 lines

---

## Executive Summary

This branch implements the **M2 Index Engine** - a complete Tier 0 + Tier 1 indexing infrastructure for CodePlane. It transforms CodePlane from a specification into a working system capable of indexing real-world repositories, exposing structured facts via MCP tools, and running as a background daemon with file watching.

---

## Features Implemented

### 1. CLI (`src/codeplane/cli/`)

| Command | Description |
|---------|-------------|
| `cpl init` | Initialize CodePlane in a git repository. Creates `.codeplane/` directory, config, `.cplignore`, and builds initial index. |
| `cpl up` | Start the CodePlane daemon. Auto-runs `init` if needed. Displays startup banner with port. |
| `cpl down` | Stop the running daemon gracefully. |
| `cpl status` | Show daemon status (running/stopped, PID, port, index stats). |

**Error handling:** All commands provide clear error messages when run outside a git repository.

### 2. Daemon (`src/codeplane/daemon/`)

HTTP server architecture using **Starlette + Uvicorn**:

- **Background Indexer:** Async queue-based incremental indexer
- **File Watcher:** Watchfiles-based watcher with debouncing (500ms default)
- **Lifecycle Manager:** PID file, port file, graceful shutdown
- **Middleware:** Request validation, error handling

Daemon runs on a random available port, writes port to `.codeplane/daemon.port`.

### 3. Index Engine (`src/codeplane/index/`)

#### 3.1 Two-Tier Architecture

| Tier | Storage | Purpose |
|------|---------|---------|
| **Tier 0** | Tantivy | Lexical full-text search (code, comments, strings) |
| **Tier 1** | SQLite | Structural facts (definitions, references, imports, symbols) |

#### 3.2 SQLite Schema (11 tables)

```
contexts        - Language contexts (Python project, JS workspace, etc.)
files           - Indexed files with content_hash, line_count
def_facts       - Function/class/variable definitions
ref_facts       - Symbol references
import_facts    - Import statements
call_facts      - Function call sites
symbol_graph    - Parent-child relationships
doc_strings     - Documentation extracted from code
epochs          - Atomic index snapshots
index_metadata  - Key-value store for index state
excluded_paths  - Paths excluded by .cplignore
```

#### 3.3 Parser (`_internal/parsing/treesitter.py`)

Tree-sitter based parser supporting **13 languages**:
- Python, JavaScript, TypeScript, TSX, JSX
- Go, Rust, Java, C, C++, C#, Ruby, PHP

Extracts:
- Definitions (functions, classes, methods, variables)
- References
- Imports
- Call sites
- Docstrings

#### 3.4 Discovery Pipeline (`_internal/discovery/`)

| Component | Purpose |
|-----------|---------|
| `scanner.py` | Walk filesystem respecting .gitignore and .cplignore |
| `probe.py` | Detect project type from manifest files |
| `authority.py` | Language-specific context authority (Python, JS, etc.) |
| `router.py` | Route files to correct context |
| `membership.py` | Context membership rules |

#### 3.5 Indexing Pipeline (`_internal/indexing/`)

| Component | Purpose |
|-----------|---------|
| `structural.py` | Extract DefFacts, RefFacts, ImportFacts via tree-sitter |
| `lexical.py` | Build Tantivy full-text index |
| `graph.py` | Build symbol parent-child relationships |

#### 3.6 State Management (`_internal/db/`, `_internal/state/`)

| Component | Purpose |
|-----------|---------|
| `database.py` | SQLModel-based database operations |
| `epoch.py` | Atomic epoch management for consistent snapshots |
| `integrity.py` | Index corruption detection and recovery |
| `reconcile.py` | Diff-based reconciliation (what changed since last index) |
| `filestate.py` | File state tracking (mtime, hash, indexed epoch) |

#### 3.7 File Watcher (`_internal/watcher/`)

- Watchfiles integration for cross-platform file monitoring
- Debounced change batching
- Respects .gitignore and .cplignore
- Queues changed paths for background indexer

### 4. MCP Tools (`src/codeplane/tools/`)

#### `map_repo` Tool

Returns repository structure with:
- Directory tree with file counts
- Line counts per file and aggregated per directory
- Entry points (files with `if __name__ == "__main__"`)
- Public API (exported symbols from `__init__.py`)

```python
@dataclass
class DirectoryNode:
    name: str
    type: Literal["directory", "file"]
    children: list[DirectoryNode]
    file_count: int | None
    line_count: int | None
```

### 5. Configuration (`src/codeplane/config/`)

YAML-based configuration in `.codeplane/config.yaml`:
- Index settings (languages, exclusions)
- Daemon settings (port, host)
- Logging configuration

### 6. Git Operations (`src/codeplane/git/`)

Comprehensive git operations library:
- Repository detection and validation
- Branch operations
- Commit operations
- Rebase with conflict resolution
- Worktree support
- Submodule handling
- Credential management

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI (Click)                          │
│                  init │ up │ down │ status                  │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Daemon (Starlette/Uvicorn)                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Routes    │  │  Watcher    │  │ Background Indexer  │  │
│  │  (MCP API)  │  │ (watchfiles)│  │   (async queue)     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   IndexCoordinator                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    Discovery                        │    │
│  │    Scanner → Probe → Authority → Router → Context   │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    Indexing                         │    │
│  │    Parser → Structural → Lexical → Graph            │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    Storage                          │    │
│  │    SQLite (Tier 1) │ Tantivy (Tier 0) │ Epochs      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## Test Suite

### Test Structure

```
tests/
├── cli/                    # CLI unit tests
├── config/                 # Configuration tests
├── core/                   # Error and logging tests
├── daemon/                 # Daemon component tests
├── git/                    # Git operations tests
├── index/
│   ├── integration/        # Index integration tests
│   └── unit/               # Index unit tests (17 files)
├── integration/            # Cross-module integration tests
├── tools/                  # MCP tools tests
└── e2e/                    # End-to-end tests
    ├── anchors/            # Per-repo anchor symbol specs (7 repos)
    ├── test_full_index.py  # Scenario 1: Full index
    ├── test_incremental.py # Scenario 2: Incremental updates
    ├── test_daemon.py      # Scenario 3: Daemon lifecycle
    └── test_search.py      # Scenario 4+5: Search quality + performance
```

### Test Metrics

| Metric | Value |
|--------|-------|
| **Total Tests** | 610 |
| **Test Files** | 66 |
| **Coverage** | 81% |
| **Pass Rate** | 100% |

### Coverage by Module

| Module | Coverage |
|--------|----------|
| `core/` | 97-100% |
| `config/` | 95-97% |
| `git/` | 81-99% |
| `index/models.py` | 90% |
| `index/ops.py` | 83% |
| `index/_internal/parsing/` | 87% |
| `index/_internal/indexing/` | 86-100% |
| `index/_internal/db/` | 62-100% |
| `index/_internal/discovery/` | 41-92% |
| `index/_internal/watcher/` | 93% |
| `tools/map_repo.py` | 96% |
| `daemon/` | 41-64% |
| `cli/` | 12-88% |

### E2E Test Design

**Architecture:** Subprocess-based CLI testing
- Tests run `cpl` commands via subprocess in isolated venvs
- Validates actual user experience, not internal APIs
- Queries SQLite directly for validation

**Repository Tiers:**
- **Tier 1 (1K-10K LOC):** click, requests, attrs, more-itertools
- **Tier 2 (10K-50K LOC):** flask, pydantic, fastapi

**Test Scenarios:**
1. Full Index from Scratch (9 tests)
2. Incremental Update (2 tests)
3. Daemon Lifecycle (5 tests)
4. Search Quality (3 tests)
5. Query Performance (3 tests)

**Truth-Based Validation:**
- Anchor symbol specs per repo (YAML)
- Performance budgets (JSON)
- Direct SQLite queries for validation

---

## Key Design Decisions

### 1. Subprocess-Based E2E Testing
Tests exercise the real CLI via subprocess, not internal APIs. This ensures the actual user experience is validated.

### 2. Epoch-Based Atomicity
All index updates are atomic via epochs. Queries see consistent snapshots.

### 3. Two-Tier Storage
Tantivy for fast lexical search, SQLite for structured queries. Each optimized for its use case.

### 4. Context Discovery
Automatic detection of project types (Python, JS, etc.) via manifest probing. Files routed to appropriate contexts.

### 5. .cplignore for Exclusions
Gitignore-style exclusion patterns for index. Separate from .gitignore to allow indexing tracked-but-ignored files.

### 6. Background Indexing via Daemon
File watcher triggers incremental reindex automatically. No manual `cpl reindex` command needed.

### 7. Search is MCP-Only
Search exposed via MCP tools, not CLI. CLI is for lifecycle management only.

---

## Commits (50 total)

Major milestones:
1. `50411b0` - Initial models and parser for M2
2. `3244b35` - Context discovery pipeline
3. `edf1455` - Lexical, structural, and graph indexing
4. `7533faa` - File watcher infrastructure
5. `59ba507` - Comprehensive unit tests
6. `32bbd41` - Integration tests
7. `0989f0f` - README and SPEC updates
8. `3623af0` - Major refactoring for Tier 0+1
9. `921bc20` - Epoch management
10. `9e40a1e` - Integrity verification
11. `25c7bbf` - HTTP daemon implementation
12. `f5a901e` - map_repo tool
13. `6bb7dbe` - True CLI-based E2E testing
14. `790b929` - Final E2E test fixes

---

## Files Changed

### New Modules (63 source files, 13,608 LOC)

**Index Engine:**
- `src/codeplane/index/models.py` - SQLModel definitions
- `src/codeplane/index/ops.py` - IndexCoordinator
- `src/codeplane/index/_internal/parsing/treesitter.py` - Tree-sitter parser
- `src/codeplane/index/_internal/discovery/*.py` - Context discovery
- `src/codeplane/index/_internal/indexing/*.py` - Indexing pipeline
- `src/codeplane/index/_internal/db/*.py` - Database operations
- `src/codeplane/index/_internal/watcher/*.py` - File watching

**Daemon:**
- `src/codeplane/daemon/app.py` - Starlette app
- `src/codeplane/daemon/routes.py` - HTTP routes
- `src/codeplane/daemon/lifecycle.py` - Process management
- `src/codeplane/daemon/indexer.py` - Background indexer
- `src/codeplane/daemon/watcher.py` - File watcher integration

**CLI:**
- `src/codeplane/cli/init.py`
- `src/codeplane/cli/up.py`
- `src/codeplane/cli/down.py`
- `src/codeplane/cli/status.py`

**Tools:**
- `src/codeplane/tools/map_repo.py`

---

## Known Gaps / Future Work

### Coverage Gaps
- `daemon/` modules at 41-64% (lifecycle tested via E2E)
- `discovery/authority.py` at 41% (many language-specific paths)
- `cli/status.py` and `cli/down.py` at 12-22% (tested via E2E)

### Not Implemented
- Tier 3 polyglot repo E2E tests
- GitHub Actions CI workflow for E2E
- MCP tool implementations beyond map_repo
- Semantic/cross-language linkage (explicit non-goal for M2)

### Dependencies
- tree-sitter and language bindings
- tantivy-py for full-text search
- watchfiles for file monitoring
- starlette/uvicorn for HTTP

---

## How to Use

```bash
# Install
pip install -e .

# Initialize in a git repo
cd /path/to/repo
cpl init

# Start daemon
cpl up

# Check status
cpl status

# Stop daemon
cpl down
```

---

## Related Issues

This branch addresses:
- #6 - Index engine architecture
- #85 - Context discovery
- #86 - Structural indexing
- #87 - Lexical indexing
- #95 - Daemon implementation
- #96 - CLI commands
