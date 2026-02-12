# CodePlane — Unified System Specification

## Table of Contents

- [1. Problem Statement](#1-problem-statement)
- [2. Core Idea](#2-core-idea)
- [3. Explicit Non-Goals](#3-explicit-non-goals)
- [4. Architecture Overview](#4-architecture-overview)
  - [4.1 Components](#41-components)
  - [4.2 CLI, Server Lifecycle, and Operability](#42-cli-server-lifecycle-and-operability)
  - [4.3 Terminology Note](#43-terminology-note-always-on-vs-operated-lifecycle)
- [5. Repository Truth & Reconciliation](#5-repository-truth--reconciliation-no-watchers)
  - [5.1 Design Goals](#51-design-goals)
  - [5.2 Canonical Repo State Version](#52-canonical-repo-state-version)
  - [5.3 File Type Classification](#53-file-type-classification)
  - [5.4 Change Detection Strategy](#54-change-detection-strategy)
  - [5.5 Reconciliation Triggers](#55-reconciliation-triggers)
  - [5.6 Rename and Move Detection](#56-rename-and-move-detection)
  - [5.7 CRLF, Symlinks, Submodules](#57-crlf-symlinks-submodules)
  - [5.8 Corruption and Recovery](#58-corruption-and-recovery)
  - [5.9 Reconcile Algorithm](#59-reconcile-algorithm-pseudocode)
  - [5.10 Reconciliation Invariants](#510-reconciliation-invariants)
- [6. Ignore Rules, Two-Tier Index Model, and Security](#6-ignore-rules-two-tier-index-model-and-security-posture)
  - [6.1–6.10 Security and Ignore Configuration](#61-security-guarantees)
- [7. Index Architecture (Tier 0 + Tier 1)](#7-index-architecture-tier-0--tier-1)
  - [7.1 Overview](#71-overview)
  - [7.2 Tier 0 — Lexical Retrieval](#72-tier-0--lexical-retrieval-tantivy)
  - [7.3 Tier 1 — Structural Facts](#73-tier-1--structural-facts-sqlite)
  - [7.4 Identity Scheme (def_uid)](#74-identity-scheme-def_uid)
  - [7.5 Parser (Tree-sitter)](#75-parser-tree-sitter)
  - [7.6 Epoch Model](#76-epoch-model)
  - [7.7 File Watcher Integration](#77-file-watcher-integration)
  - [7.8 Bounded Query APIs](#78-bounded-query-apis)
  - [7.9 What This Index Does NOT Provide](#79-what-this-index-does-not-provide)
- [8. Deterministic Refactor Engine](#8-deterministic-refactor-engine-scip-based-semantic-data)
  - [8.1 Purpose](#81-purpose)
  - [8.2 Core Principles](#82-core-principles)
  - [8.3 Supported Operations](#83-supported-operations)
  - [8.4 Context Discovery & Membership](#84-context-discovery--membership)
  - [8.5 Refactor Execution Flow](#85-refactor-execution-flow)
  - [8.5a Two-Phase Rename](#85a-two-phase-rename-agent-decision-flow)
  - [8.5b Witness Packets](#85b-witness-packets)
  - [8.5c Decision Capsules](#85c-decision-capsules)
  - [8.6 Multi-Context Handling](#86-multi-context-handling)
  - [8.7–8.14 Context, Config, and Guarantees](#87-context-selection-rules)
- [9. Mutation Engine](#9-mutation-engine-atomic-file-edits)
  - [9.1–9.10 Mutation Design](#91-design-objectives)
- [10. Git and File Operations](#10-git-and-file-operations-no-terminal-mediation)
- [11. Tests: Planning, Parallelism, Execution](#11-tests-planning-parallelism-execution)
  - [11.1–11.8 Test Model](#111-goal)
- [12. Task Model, Convergence Controls, and Ledger](#12-task-model-convergence-controls-and-ledger)
  - [12.1–12.5 Task and Ledger Design](#121-scope-and-principle)
- [13. Observability and Operator Insight](#13-observability-and-operator-insight)
  - [13.1–13.6 Observability Design](#131-why-observability)
- [15. Deterministic Refactoring Primitives](#15-deterministic-refactoring-primitives-summary-level-capability-list)
- [16. Embeddings Policy](#16-embeddings-policy)
- [17. Subsystem Ownership Boundaries](#17-subsystem-ownership-boundaries-who-owns-what)
- [18. Resolved Conflicts](#18-resolved-conflicts-previously-open)
- [19. Semantic Support Exploration](#19-semantic-support-exploration-design-archaeology)
  - [19.1 Approaches Explored](#191-approaches-explored)
  - [19.2 Failure Modes](#192-explicit-failure-modes-discovered)
  - [19.3 Conclusion](#193-conclusion-planner-based-architecture)
  - [19.4 Future Direction](#194-future-direction-explicitly-future--probable)
- [20. Risk Register](#20-risk-register-remaining-design-points)
- [21. Readiness Note](#21-readiness-note-what-is-stable-enough-for-api-surfacing-next)
- [22. What CodePlane Is](#22-what-codeplane-is-canonical-summary)
- [23. MCP API Specification](#23-mcp-api-specification)
  - [23.1 Design Principles](#231-design-principles)
  - [23.2 Protocol Architecture](#232-protocol-architecture)
  - [23.3 Response Envelope](#233-response-envelope)
  - [23.4 Tool Catalog](#234-tool-catalog)
  - [23.5 Progress Reporting](#235-progress-reporting)
  - [23.6 Pagination](#236-pagination)
  - [23.7 Tool Specifications](#237-tool-specifications)
  - [23.8 REST Endpoints](#238-rest-endpoints-operator)
  - [23.9 Error Handling](#239-error-handling)
  - [23.10 MCP Server Configuration](#2310-mcp-server-configuration)
  - [23.11 Versioning](#2311-versioning)

---

## 1. Problem Statement

Modern AI coding agents are not limited by reasoning ability. They are limited by **how they interact with repositories**.

Dominant sources of friction:

- Exploratory thrash  
  Agents build a mental model of a repo via repeated grep, file opens, and retries.

- Terminal mediation  
  Trivial deterministic actions (git status, diff, run test, cat file) are executed through terminals, producing unstructured text, retries, hangs, and loops.

- Editor state mismatch  
  IDE buffers, file watchers, and undo/keep UX drift from on-disk and Git truth.

- Missing deterministic refactors  
  Renames and moves that IDEs do in seconds take agents minutes via search-and-edit loops.

- No convergence control  
  Agents repeat identical failure modes with no enforced strategy changes or iteration caps.

- Wasteful context acquisition  
  Agents repeatedly ask for information that is already computable.

Result: **Small fixes take 5–10 minutes instead of seconds**, due to orchestration and I/O inefficiency, not model capability.

---

## 2. Core Idea

Introduce a **local repository control plane** that sits beneath agents and turns a repository into a **deterministic, queryable system**.

Key reframing:

- Agents plan and decide.
- CodePlane executes deterministically.
- Anything deterministic is computed once, not reasoned about repeatedly.
- Every state mutation returns complete structured context in a single call.

This replaces:

- grep + terminal + retries

with:

- indexed query → structured result → next action

---

## 3. Explicit Non-Goals

CodePlane is **not**:

- a chatbot
- an agent
- a semantic reasoning engine
- embedding-first
- a Git or IDE replacement
- an orchestrator

CodePlane does not plan, retry, or decide strategies. Its role is deterministic execution and deterministic context.

---

## 4. Architecture Overview

### 4.1 Components

- **CodePlane server (Python)**
  - Maintains deterministic indexes.
  - Owns file, Git, test, and refactor operations.
  - Exposes endpoints.

- **Agent client**
  - Copilot, Claude Code, Cursor, Continue, etc.
  - For operations CodePlane covers, agents should prefer CodePlane tools over direct file edits or shell commands.
  - Agents will still use terminals and other tools for tasks outside CodePlane's scope.

- **Git**
  - Authoritative history and audit layer.
  - Primary signal for detecting external mutations on tracked files.

Operational viewpoint:

- VS Code is a viewer, not a state manager.

### 4.2 CLI, Server Lifecycle, and Operability

CodePlane uses a single operator CLI: `cpl`. It is explicitly **not agent-facing**.

Core commands (idempotent; human output derivable from structured JSON via `--json`):

| Command | Description |
|---|---|
| `cpl init` | One-time repo setup: write `.codeplane/`, generate `.cplignore`, bind repo ID, build first index (or schedule immediately). |
| `cpl up` | Start the CodePlane server (foreground). Ctrl+C to stop. Idempotent check prevents duplicate instances. |
| `cpl status` | Single human-readable view: server running, repo fingerprint, index version, last reconcile, last error. |
| `cpl doctor` | Single "tell me what's wrong and how to fix it" command. Output suitable for pasting into issues. |

Humans learn: `init` once, then `up/status/doctor`.

### Folded and Removed Commands

The following capabilities are folded into core commands or removed to avoid surface area bloat:

| Capability | Disposition |
|---|---|
| Logs | Folded into `status --verbose` (last N log lines) and `doctor --logs` (bundled diag report). Optional alias `cpl logs` → `cpl status --follow` is acceptable but not a stable interface. |
| Inspect | Folded into `status --json` for machine-readable introspection. |
| Config CLI | Removed in v1. Use files: global config in user dir, repo config in `.codeplane/config`. One-off overrides via `cpl up --set key=value` if needed. |
| Rebuild index | Automatic when integrity checks fail; no manual trigger. |

Server model:

- **Foreground process** — `cpl up` runs in foreground, Ctrl+C to stop. No background daemonization.
- **Repo-scoped** — one server per repository; no multi-repo mode.
- `cpl up` in a repo directory starts a server for that repo only.
- Transport: **HTTP localhost** with ephemeral port.
  - Cross-platform with identical code (no socket vs named pipe divergence).
  - MCP clients can connect directly via HTTP/SSE transport (no stdio proxy needed).
- Response header:
  - All HTTP responses include `X-CodePlane-Repo: <absolute-path>` header.
  - Clients can use this to detect wrong-server mistakes when multiple CodePlane instances run simultaneously.
  - No request header required — clients don't need to send repo path on every request.
  - Rationale: Simpler client integration while still enabling cross-repo accident detection. No token management, no file permissions, no auth state.
- Isolation rationale:
  - Failure in one repo cannot affect another.
  - Version skew between repos is not a problem.
  - CI and local dev work identically.
  - Aligns with spec's determinism-first philosophy.

Repo activation:

- `cpl up` initializes repo if needed (creates `.codeplane/`, repo UUID, config).
- Index is eagerly built on startup and continuously maintained.

Server startup includes:

- Git HEAD verification
- Overlay index diff
- Index consistency check

Server shutdown (Ctrl+C or SIGTERM):

- Graceful shutdown timeout: 5 seconds (configurable)
- In-flight HTTP requests: allowed to complete until timeout, then aborted
- Active refactor/mutation operations: 
  - If in planning phase → abort immediately (no side effects)
  - If in apply phase → complete current file, abort remainder, rollback partial batch
- Connected SSE clients: receive `shutdown` event, then disconnect
- Flushes writes
- Releases locks

Logging:

- Multi-output support: logs can be sent to multiple destinations simultaneously
- Each output specifies format, destination, and optional level override
- Formats: `json` (structured JSON lines) or `console` (human-readable)
- Destinations: `stderr`, `stdout`, or absolute file path
- Default: single console output to stderr at INFO level
- Levels: `debug`, `info`, `warn`, `error`
- Required fields per JSON entry:
  - `ts`: ISO 8601 timestamp with milliseconds
  - `level`: log level
  - `event`: human-readable message
- Optional correlation fields:
  - `request_id`: request correlation identifier
  - `op_id`: operation identifier (for tracing a single request)
  - `task_id`: task envelope identifier
- Configuration example:
  ```yaml
  logging:
    level: DEBUG
    outputs:
      - format: console
        destination: stderr
        level: INFO        # Show INFO+ on console
      - format: json
        destination: /var/log/codeplane.jsonl
                            # Inherits DEBUG from parent
  ```
- JSON output example:
  ```json
  {"ts":"2026-01-26T15:30:00.123Z","level":"info","event":"server started","port":54321}
  {"ts":"2026-01-26T15:30:01.456Z","level":"debug","op_id":"abc123","event":"refactor planning started","symbol":"MyClass"}
  {"ts":"2026-01-26T15:30:02.789Z","level":"error","op_id":"abc123","event":"indexer timeout","lang":"java","timeout_ms":30000}
  ```
- Access via CLI:
  - `cpl status --verbose`: last 50 lines
  - `cpl status --follow`: tail -f equivalent
  - `cpl doctor --logs`: full log bundle for diagnostics

Installation and upgrades:

- Install modes (user-level only; no root/system install):
  - `pipx install codeplane`
  - Static binary from GitHub Releases
- Upgrades via package manager (pip/uv)

Diagnostics and introspection:

- `cpl doctor` checks:
  - Server reachable
  - Index integrity
  - Commit hash matches Git HEAD
  - Config sanity
- `cpl doctor --logs`: bundled diagnostic report including recent logs
- Runtime introspection:
  - `cpl status --verbose`: includes last N log lines and paths
  - `cpl status --json`: machine-readable index metadata (paths, size, commit, overlay state)
  - `cpl status --follow`: optional alias for tailing logs (not a stable interface)
  - Healthcheck endpoint exists (`/health`) returning JSON (interface details deferred)

Config precedence:

1. One-off overrides via `cpl up --set key=value` / env vars
2. Per-repo: `.codeplane/config.yaml`
3. Global: `~/.config/codeplane/config.yaml`
4. Built-in defaults

Environment variables use `CODEPLANE__` prefix with double underscore delimiter for nesting:
- `CODEPLANE__LOGGING__LEVEL=DEBUG`
- `CODEPLANE__SERVER__PORT=8080`

No dedicated config CLI in v1. Edit files directly.

Error response schema:

All API errors return a consistent JSON structure:

```json
{
  "code": 4001,
  "error": "INDEX_CORRUPT",
  "message": "Index checksum mismatch; rebuild required",
  "retryable": false,
  "details": {
    "expected_hash": "abc123",
    "actual_hash": "def456"
  }
}
```

Fields:
- `code`: Numeric error code (for programmatic handling)
- `error`: String error identifier (for logging and display)
- `message`: Human-readable description
- `retryable`: Boolean hint — `true` if retry may succeed without intervention
- `details`: Optional object with error-specific context

Defaults prevent footguns:

- `.cplignore` auto-generated
- Dangerous paths excluded
- Overlay disabled by default in CI

Failure recovery playbooks:

| Failure | Detection | Recovery Command |
|---|---|---|
| Corrupt index | `cpl doctor` fails hash check | Automatic rebuild (or `cpl debug index-rebuild`) |
| Schema mismatch | Startup error | Automatic rebuild on `cpl up` |
| Stale revision | `cpl status` shows mismatch | Automatic re-fetch/rebuild on `cpl up` |

Platform constraints:

- Transport:
  - HTTP localhost on all platforms (identical implementation)
  - No platform-specific IPC code
- Filesystem:
  - No background watchers (see reconciliation)
  - Hash-based change detection
- Locking:
  - Uses `portalocker` cross-platform
  - CRLF normalized internally
- Path casing:
  - Canonical casing tracked on Windows
  - Case sensitivity honored on Linux

### 4.3 Terminology Note: “Always-on” vs Operated Lifecycle

One source uses “local, always-on control plane” as conceptual framing; the operability spec defines explicit start/stop.

Unified operational interpretation:

- CodePlane is **conceptually** a “control plane beneath agents.”
- It is **operationally** a repo-scoped foreground server started via `cpl up` (Ctrl+C to stop).

---

## 5. Repository Truth & Reconciliation (No Watchers)

### 5.1 Design Goals

- Correctly reflect repository state on disk, even across external edits.
- Never mutate Git state unless explicitly triggered by a CodePlane operation.
- Cheap, deterministic reconciliation before/after every CodePlane operation.
- No reliance on OS watchers (watchers optional and narrow at most).
- Works across:
  1. Git-tracked files
  2. Git-ignored but CPL-tracked files
  3. CPL-ignored files

### 5.2 Canonical Repo State Version

Authoritative repo version is:

```
RepoVersion = (HEAD SHA, .git/index stat metadata, submodule SHAs)
```

- `HEAD SHA`: `git rev-parse HEAD` or libgit2 equivalent.
- `.git/index`: compare mtime + size (no need to read contents).
- Submodules: treat each submodule as its own repo, include its HEAD SHA.

### 5.3 File Type Classification

| Type | Defined By | Indexed? |
|---|---|---|
| 1. Indexable | All files not excluded by `.cplignore` or PRUNABLE_DIRS | Yes |
| 2. Ignored | Excluded via `.cplignore` or PRUNABLE_DIRS | No |

**Note:** Git-tracking is irrelevant for indexing. The index artifacts (`.codeplane/`) are gitignored by design, so it is safe to index sensitive files like `.env`. The only exclusion mechanism is `.cplignore` patterns and PRUNABLE_DIRS (e.g., `node_modules/`, `.git/`).

### 5.4 Change Detection Strategy

All indexable files use stat-based change detection:

1. Walk all files (excluding PRUNABLE_DIRS and `.cplignore` patterns).
2. For each file:
   - `stat()` compare to cached metadata (mtime, size, inode).
   - If metadata differs → hash file content and compare to stored hash.
   - If confirmed changed → reindex file and invalidate relevant caches.

### 5.5 Reconciliation Triggers

Reconciliation occurs:

- On server start
- Before and after every operation that reads or mutates repo state
- After agent-initiated file or Git ops (rename, commit, rebase, etc.)

### 5.6 Rename and Move Detection

- Detect delete+create pairs with identical hash → infer rename.
- Optional: Git-style similarity diff for small content changes.
- Default: treat as unlink + create unless hash match.

### 5.7 CRLF, Symlinks, Submodules

- CRLF: normalize line endings during hashing; avoid false dirty.
- Symlinks: treat as normal files; do not follow. Git tracks symlink targets as content blobs.
- Submodules:
  - Track submodule HEADs independently.
  - Reindex on submodule HEAD or path change.
  - Never recurse unless submodule is initialized.

### 5.8 Corruption and Recovery

- CodePlane never mutates `.git/index`, working tree, or HEAD.
- On Git metadata corruption: fail with clear message; don’t auto-repair.
- On CPL index corruption: wipe and reindex from Git + disk.

### 5.9 Reconcile Algorithm (Pseudocode)

```python
def reconcile(repo):
    head_sha = get_head_sha()
    index_stat = stat('.git/index')

    if (head_sha, index_stat) != repo.last_seen_version:
        repo.invalidate_caches()

    changed_files = []

    # 1. Git-tracked
    for path, entry in git_index.entries():
        fs_meta = stat(path)
        if fs_meta != entry.stat:
            if sha(path) != entry.hash:
                changed_files.append(path)

    # 2. CPL-tracked untracked files
    for path, entry in cpl_overlay.entries():
        fs_meta = stat(path)
        if fs_meta != entry.stat:
            if sha(path) != entry.hash:
                changed_files.append(path)

    # 3. Rename detection
    deleted = repo.files_missing()
    added = repo.files_added()
    for a in deleted:
        for b in added:
            if repo.cached_hash(a) == sha(b):  # use cached hash for deleted file
                repo.mark_rename(a, b)

    # 4. Reindex changed files
    for f in changed_files:
        repo.reindex(f)

    repo.last_seen_version = (head_sha, index_stat)
```

### 5.10 Reconciliation Invariants

- No server threads write_files **repository state** (working tree, `.git/`, HEAD).
- Background threads **may** update **derived state** (SQLite index, Tantivy, caches).
- Index updates are continuous: file watcher detects changes, background worker reindexes.
- Reconcile logic is stateless, deterministic, idempotent.
- Git is the sole truth for tracked file identity and content.
- CPL index is derived from disk + Git, never canonical.

---

## 6. Ignore Rules and Security Posture

### 6.1 Security Guarantees

- Index artifacts (`.codeplane/`) are gitignored by design, so sensitive files can be indexed safely.
- All indexing and mutation actions are scoped, audited, deterministic.

### 6.2 Threat Assumptions

- Runs under trusted OS user account.
- Does not defend against compromised OS or user session.

### 6.3 `.cplignore` Role and Semantics

`.cplignore` defines what CodePlane never indexes. Follows `.gitignore` syntax.

Security-focused posture defines `.cplignore` defaults that block noise. See defaults below.

### 6.4 Indexing Model

| Type | Defined By | Indexed? |
|---|---|---|
| Indexable | All files not excluded by `.cplignore` or PRUNABLE_DIRS | Yes |
| Ignored | Blocked via `.cplignore` or PRUNABLE_DIRS | No |

Git-tracking is irrelevant for indexing. Index artifacts are gitignored, so it's safe to index all files including `.env`.

### 6.5 `.cplignore` Defaults

Default ignore patterns for efficiency (block noisy directories):

```
node_modules/
dist/
build/
.venv/
__pycache__/
*.pyc
*.log
coverage/
pytest_cache/
```

### 6.6 Failure Modes and Protections

| Misconfig | Result | Mitigation |
|---|---|---|
| Missing `.cplignore` | All files indexed (may be slow) | Defaults applied automatically |

### 6.7 Security-Auditability Notes

- All mutations emit structured deltas.
- Index is deterministic and reproducible.
- Operation history is append-only (SQLite-backed).
- No automatic retries or implicit mutations.

---

## 7. Index Architecture (Tier 0 + Tier 1)

### 7.1 Overview

CodePlane builds a deterministic, incrementally updated **stacked index** with two tiers:

**Tier 0 — Lexical Retrieval (Always-On):**
- Tantivy full-text search for fast candidate discovery
- NOT semantic authority — candidate generation only
- One document per file with path, content, file_id, unit_id

**Tier 1 — Structural Facts (Tree-sitter + SQLite):**
- Syntactic fact extraction via Tree-sitter
- Persisted facts: DefFact, RefFact, ScopeFact, LocalBindFact, ImportFact, ExportSurface
- Bounded ambiguity infrastructure (AnchorGroups)
- No semantic resolution — explicit syntactic facts only

**Explicitly NOT provided:**
- No semantic authority or type checking
- No call graph or transitive analysis
- No heuristic inference or query-time resolution
- No SCIP, LSP, or compiler integration

This index is **glorified search + syntactic facts**. It enables a future refactor planner but provides no semantic guarantees itself.

Indexing scope:
- Git-tracked files (primary)
- CPL-tracked files (local overlay, never shared)
- CPL-ignored files excluded

No embeddings. Target latency: <100ms for fact lookups, <1s for lexical search.

### 7.2 Tier 0 — Lexical Retrieval (Tantivy)

#### Purpose

Fast candidate discovery by text search. Tier 0 is NOT semantic authority — it finds potential matches that Tier 1 facts refine.

#### Storage

- Engine: Tantivy via PyO3 bindings
- Location: `.codeplane/tantivy/`
- Update model: immutable segments + delete+add on change
- Atomicity: build in temp dir and swap in (`os.replace()`)

#### Schema

Each indexed file produces one Tantivy document:

| Field | Type | Purpose |
|-------|------|---------|
| `file_id` | integer | Unique file identifier (FK to SQLite) |
| `unit_id` | integer | Build unit / context ID |
| `path` | text (raw) | Relative file path |
| `content` | text (tokenized) | Full file content |
| `language_family` | text | Language family identifier |

#### APIs (All Bounded)

```python
lexical_search(
    query: str,
    limit: int,              # REQUIRED, hard cap
    unit_id: int | None,     # Optional scope filter
    language: str | None,    # Optional language filter
) -> list[LexicalHit]        # Never exceeds limit
```

All queries MUST specify a limit. Unbounded queries are forbidden.

#### Performance Targets

- Indexing throughput: 5k–50k docs/sec
- Query latency: <10ms warm cache for top-K
- Incremental updates based on content hash diff

### 7.3 Tier 1 — Structural Facts (SQLite)

#### Purpose

Persist explicit syntactic facts extracted via Tree-sitter. These facts enable bounded queries and future refactor planning. No semantic inference.

#### Storage

- Engine: SQLite, single-file, ACID, WAL mode
- Location: `.codeplane/index.db`
- Concurrency: Readers non-blocking, single writer

#### Fact Tables

##### 7.3.1 DefFact

Definition facts for symbols (functions, classes, methods, variables).

| Column | Type | Description |
|--------|------|-------------|
| `def_uid` | TEXT PK | Stable definition identity (see §7.4) |
| `file_id` | INTEGER FK | File containing definition |
| `unit_id` | INTEGER FK | Build unit / context |
| `kind` | TEXT | function, class, method, variable, etc. |
| `name` | TEXT | Simple name |
| `qualified_name` | TEXT | Full path (e.g., `module.Class.method`) |
| `lexical_path` | TEXT | Syntactic nesting path for identity |
| `start_line` | INTEGER | Definition start line (1-indexed) |
| `start_col` | INTEGER | Definition start column |
| `end_line` | INTEGER | Definition end line |
| `end_col` | INTEGER | Definition end column |
| `signature_hash` | TEXT | Hash of syntactic signature |
| `display_name` | TEXT | Human-readable form |

##### 7.3.2 RefFact

Reference facts for identifier occurrences.

| Column | Type | Description |
|--------|------|-------------|
| `ref_id` | INTEGER PK | Reference identifier |
| `file_id` | INTEGER FK | File containing reference |
| `unit_id` | INTEGER FK | Build unit / context |
| `scope_id` | INTEGER FK | Enclosing scope (FK to ScopeFact) |
| `token_text` | TEXT | Exact text slice from source |
| `start_line` | INTEGER | Reference start line |
| `start_col` | INTEGER | Reference start column |
| `end_line` | INTEGER | Reference end line |
| `end_col` | INTEGER | Reference end column |
| `role` | TEXT | DEFINITION, REFERENCE, IMPORT, EXPORT |
| `ref_tier` | TEXT | PROVEN, STRONG, ANCHORED, UNKNOWN |
| `certainty` | TEXT | CERTAIN, UNCERTAIN |

**ref_tier classification (index-time only, no query-time upgrades):**

| Tier | Meaning | Criteria |
|------|---------|----------|
| PROVEN | Lexically bound, same-file | LocalBindFact exists with certainty=CERTAIN |
| STRONG | Cross-file with explicit trace | ImportFact + ExportSurface chain exists |
| ANCHORED | Ambiguous, grouped | Member of an AnchorGroup |
| UNKNOWN | Cannot classify | Default for unresolved refs |

##### 7.3.3 ScopeFact

Lexical scope facts for binding resolution.

| Column | Type | Description |
|--------|------|-------------|
| `scope_id` | INTEGER PK | Scope identifier |
| `file_id` | INTEGER FK | File containing scope |
| `unit_id` | INTEGER FK | Build unit / context |
| `parent_scope_id` | INTEGER FK | Parent scope (NULL for file scope) |
| `kind` | TEXT | file, class, function, block, etc. |
| `start_line` | INTEGER | Scope start line |
| `start_col` | INTEGER | Scope start column |
| `end_line` | INTEGER | Scope end line |
| `end_col` | INTEGER | Scope end column |

##### 7.3.4 LocalBindFact

Same-file binding facts (index-time only, NO query-time inference).

| Column | Type | Description |
|--------|------|-------------|
| `bind_id` | INTEGER PK | Binding identifier |
| `file_id` | INTEGER FK | File containing binding |
| `unit_id` | INTEGER FK | Build unit / context |
| `scope_id` | INTEGER FK | Scope where name is bound |
| `name` | TEXT | Bound identifier name |
| `target_kind` | TEXT | DEF, IMPORT, UNKNOWN |
| `target_uid` | TEXT | def_uid or import_uid or NULL |
| `certainty` | TEXT | CERTAIN, UNCERTAIN |
| `reason_code` | TEXT | PARAM, LOCAL_ASSIGN, DEF_IN_SCOPE, IMPORT_ALIAS |

**Critical invariant:** LocalBindFact is written at index time. Query layer reads only — no upgrades, no inference.

##### 7.3.5 ImportFact

Explicit import statements (syntactic only, no dynamic resolution).

| Column | Type | Description |
|--------|------|-------------|
| `import_uid` | TEXT PK | Import identity |
| `file_id` | INTEGER FK | File containing import |
| `unit_id` | INTEGER FK | Build unit / context |
| `scope_id` | INTEGER FK | Scope where import is visible |
| `imported_name` | TEXT | Name being imported |
| `alias` | TEXT | Local alias (NULL if none) |
| `source_literal` | TEXT | Import source string literal (if extractable) |
| `import_kind` | TEXT | python_import, python_from, js_import, ts_import_type, go_import, rust_use, csharp_using, csharp_using_static, etc. |
| `certainty` | TEXT | CERTAIN, UNCERTAIN |

**What is NOT imported:** Dynamic imports (`importlib.import_module(var)`), computed imports, or any form where the source cannot be statically extracted.

##### 7.3.6 ExportSurface

Materialized export surface per build unit.

| Column | Type | Description |
|--------|------|-------------|
| `surface_id` | INTEGER PK | Surface identifier |
| `unit_id` | INTEGER FK | Build unit |
| `surface_hash` | TEXT | Hash of all entries for invalidation |
| `epoch_id` | INTEGER | Epoch when surface was computed |

##### 7.3.7 ExportEntry

Individual exported names within an ExportSurface.

| Column | Type | Description |
|--------|------|-------------|
| `entry_id` | INTEGER PK | Entry identifier |
| `surface_id` | INTEGER FK | Parent ExportSurface |
| `exported_name` | TEXT | Public name |
| `def_uid` | TEXT | Target definition (NULL if unresolved) |
| `certainty` | TEXT | CERTAIN, UNCERTAIN |
| `evidence_kind` | TEXT | explicit_export, default_module, __all__literal, etc. |

##### 7.3.8 ExportThunk

Re-export declarations (strictly constrained forms only).

| Column | Type | Description |
|--------|------|-------------|
| `thunk_id` | INTEGER PK | Thunk identifier |
| `source_unit` | INTEGER FK | Unit doing the re-export |
| `target_unit` | INTEGER FK | Unit being re-exported from |
| `mode` | TEXT | REEXPORT_ALL, EXPLICIT_NAMES, ALIAS_MAP |
| `explicit_names` | TEXT | JSON array of names (if EXPLICIT_NAMES) |
| `alias_map` | TEXT | JSON object of name→alias (if ALIAS_MAP) |
| `evidence_kind` | TEXT | Syntax node type that produced this |

**Strictly forbidden:** Arbitrary predicates, heuristics, or computed re-exports.

##### 7.3.9 AnchorGroup

Bounded ambiguity buckets for refs that cannot be classified as PROVEN or STRONG.

| Column | Type | Description |
|--------|------|-------------|
| `group_id` | INTEGER PK | Group identifier |
| `unit_id` | INTEGER FK | Build unit |
| `member_token` | TEXT | The identifier text (e.g., `foo`) |
| `receiver_shape` | TEXT | Receiver pattern (e.g., `self.`, `obj.`, `None`) |
| `total_count` | INTEGER | Total refs in this group |
| `exemplar_ids` | TEXT | JSON array of ref_ids (hard-capped) |

**Critical invariants:**
- `exemplar_ids` is hard-capped (default: 10 exemplars max)
- Exemplars selected by deterministic ordering: (file_path, start_line, start_col)
- `total_count` tracks true population size for reporting
- No unbounded lists ever returned

##### 7.3.10 DynamicAccessSite

Telemetry for dynamic access patterns (reporting only, never blocks).

| Column | Type | Description |
|--------|------|-------------|
| `site_id` | INTEGER PK | Site identifier |
| `file_id` | INTEGER FK | File containing site |
| `unit_id` | INTEGER FK | Build unit |
| `start_line` | INTEGER | Site start line |
| `start_col` | INTEGER | Site start column |
| `pattern_type` | TEXT | bracket_access, getattr, reflect, eval, etc. |
| `extracted_literals` | TEXT | JSON array of literal strings (if any) |
| `has_non_literal_key` | BOOLEAN | True if key is computed/dynamic |

##### 7.3.11 DefSnapshotRecord

Historical snapshots of definitions at each epoch, enabling epoch-based semantic diff.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-incrementing identifier |
| `epoch_id` | INTEGER (indexed) | Epoch when snapshot was taken |
| `file_path` | TEXT (indexed) | File containing the definition |
| `kind` | TEXT | Symbol kind (function, class, variable, etc.) |
| `name` | TEXT | Symbol name |
| `lexical_path` | TEXT | Dot-separated qualified path (e.g., `MyClass.my_method`) |
| `signature_hash` | TEXT | Hash of the symbol's signature for change detection |
| `display_name` | TEXT | Human-readable display name |
| `start_line` | INTEGER | Definition start line |
| `end_line` | INTEGER | Definition end line |

Populated during `publish_epoch()` — snapshots all `DefFact` rows for files indexed in that epoch. No foreign key to `File` table (file_path stored directly for historical stability).

Used by `semantic_diff` tool in epoch mode (`base="epoch:N"`, `target="epoch:M"`) to compare structural definitions across index states without requiring git history.

### 7.4 Identity Scheme (def_uid)

#### Purpose

Stable definition identity that survives renames and moves within syntactic constraints.

#### Construction

```
def_uid = sha256(
    unit_id + ":" +
    kind + ":" +
    lexical_path + ":" +
    signature_hash + ":" +
    disambiguator
)[:16]  # Truncated for readability
```

#### Components

| Component | Source | Purpose |
|-----------|--------|---------|
| `unit_id` | Build unit ID | Namespace isolation |
| `kind` | Tree-sitter node type | Distinguish functions from classes |
| `lexical_path` | Syntactic nesting | e.g., `Class.method`, `module.func` |
| `signature_hash` | Parameter/return syntax | Distinguish overloads |
| `disambiguator` | Sibling index | Handle same-signature siblings |

#### Limitations (Explicitly Documented)

- Macros/codegen: Definitions inside macro expansions have unstable identity
- Conditional compilation: `#ifdef` blocks may produce different identities per config
- Dynamic definitions: `setattr(obj, name, func)` not captured
- Monkey patching: Runtime modifications invisible to index

### 7.5 Parser (Tree-sitter)

- Default parser: Tree-sitter via Python bindings
- Languages: ~15 bundled grammars (~15 MB total), version-pinned
- Failure mode: If grammar fails or file unsupported, skip with warning
- No fallback tokenization — lexical index handles fuzzy matching
- Tree-sitter provides syntactic structure only; NO semantic resolution

### 7.6 Epoch Model

#### Purpose

Epochs are incremental snapshot barriers ensuring consistent index state.

#### Semantics

- Epochs are **incremental** — no duplication of unchanged data
- Only changed files are reindexed between epochs
- Publishing an epoch means: SQLite facts committed + Tantivy updates committed
- Epoch ID is monotonically increasing

#### Lifecycle

```
file changes → background indexing → publish_epoch() → epoch_id++
```

#### Freshness Contract

When a UX-facing operation requires index data:

1. Determine required files/units
2. If any are DIRTY/STALE/UNINDEXED: **block** for next epoch
3. UX **never reads stale data**
4. Epochs are expected to be sub-second to ~2s

There is NO fallback to stale data. UX correctness > latency.

#### Current Implementation: Global Freshness

The current implementation uses a **global freshness gate** via `_fresh_event`:

- Cleared synchronously when any file mutation begins (`mark_stale()`)
- Set when reindex completes
- All query methods (`search`, `get_def`, `get_references`, etc.) await freshness before returning

This prevents stale reads but has a limitation: any mutation blocks ALL queries repo-wide.

#### Near-Term: File-Level Freshness (PLANNED)

For improved concurrency, queries should block only on affected files:

- Track `{file_id: freshness_state}` per file
- Query for file X blocks only if file X is DIRTY
- Other files remain queryable during partial reindex
- Requires: per-file dirty tracking, dependency-aware blocking for cross-file queries

See GitHub issue for design proposals and implementation timeline: https://github.com/dfinson/codeplane/issues/132

### 7.7 File Watcher Integration

#### Purpose

Continuous background indexing decoupled from UX flow.

#### Requirements

- Watch all files NOT ignored by:
  - Repository `.gitignore`
  - `.codeplane/.gitignore`
  - CPL ignore rules
- Debounce events (handle storms, mid-write saves)
- Enqueue changed files for background indexing
- Never block UX during ingestion

#### Init Behavior

When `cpl init` runs:
1. Start file watchers
2. Trigger full initial index
3. Begin epoch publication loop
4. Create `.codeplane/.gitignore` to ignore artifacts

### 7.8 Bounded Query APIs

All fact queries MUST be bounded. No unbounded result sets.

#### Required APIs

```python
# Definition lookups
get_def(def_uid: str) -> DefFact | None
list_defs_by_name(unit_id: int, name: str, limit: int) -> list[DefFact]

# Reference lookups
list_refs_by_def_uid(def_uid: str, tier: RefTier | None, limit: int) -> list[RefFact]
list_proven_refs(def_uid: str, limit: int) -> list[RefFact]  # Convenience

# Scope lookups
get_scope(scope_id: int) -> ScopeFact | None
list_scopes_in_file(file_id: int) -> list[ScopeFact]

# Binding lookups
get_local_bind(scope_id: int, name: str) -> LocalBindFact | None
list_binds_in_scope(scope_id: int, limit: int) -> list[LocalBindFact]

# Import lookups
list_imports(file_id: int, limit: int) -> list[ImportFact]
get_import(import_uid: str) -> ImportFact | None

# Export lookups
get_export_surface(unit_id: int) -> ExportSurface | None
list_export_entries(surface_id: int, limit: int) -> list[ExportEntry]

# Anchor group lookups
get_anchor_group(unit_id: int, member_token: str, receiver_shape: str) -> AnchorGroup | None
list_anchor_groups(unit_id: int, limit: int) -> list[AnchorGroup]

# Telemetry lookups
list_dynamic_access_sites(file_id: int | None, unit_id: int | None, limit: int) -> list[DynamicAccessSite]
```

#### Forbidden APIs (REMOVED)

The following are explicitly NOT provided:
- Call graph traversal
- Callers / callees
- Impact analysis
- Transitive closure
- Type hierarchy
- "Resolution" of any kind
- Qualified name matching as fallback

### 7.9 What This Index Does NOT Provide

To prevent misuse, this section explicitly documents what the index cannot do:

1. **No semantic authority**: The index does not prove that a reference binds to a definition. PROVEN and STRONG are syntactic classifications, not semantic proofs.

2. **No safe refactor guarantees**: The index enables a future planner to propose edits. It does not guarantee those edits are correct.

3. **No type information**: The index knows `x = foo()` but not that `x` is a `List[str]`.

4. **No cross-language resolution**: Python importing from a JS module is not traced.

5. **No dynamic behavior modeling**: `getattr(obj, name)` is logged, not resolved.

6. **No inference or heuristics**: If explicit syntactic proof doesn't exist, the ref stays UNKNOWN.

---
## 8. Deterministic Refactor Engine (SCIP-Based Semantic Data)

### 8.1 Purpose

Provide IntelliJ-class deterministic refactoring (rename / move / delete / change signature) across multi-language repositories using **pre-indexed SCIP semantic data** as the semantic authority, preserving determinism, auditability, and user control.

This subsystem is narrowly scoped: a high-correctness refactor planner and executor.

### 8.2 Core Principles

- SCIP-based semantics: all refactor planning uses pre-indexed SCIP semantic data
- Static configuration: languages, environments, roots known at startup
- No speculative semantics: CodePlane never guesses bindings
- No working tree mutation during planning
- Single atomic apply to the real repo
- Mutation gate: semantic writes require all affected files to be CLEAN
- Optional subsystem: enabled by default, configurable off

### 8.3 Supported Operations

- `rename_symbol(from, to, at)`
- `rename_file(from_path, to_path)`
- `move_file(from_path, to_path)`
- `delete_symbol(at)`

All operations:
- Return **structured diff output** with `files_changed`, `edits`, `symbol`, `new_name`, etc.
- Provide **preview → apply → rollback** semantics
- Are **atomic** at the patching level
- Operate across **tracked and untracked (overlay) files**
- Require all affected files to be in CLEAN semantic state
- Trigger deterministic re-indexing after apply

### 8.3a Architecture Overview

![CodePlane Semantic Refactor Architecture](docs/images/codeplane-semantic-refactor-architecture.png)

#### SCIP-Based Execution

- All refactor planning (rename, move, delete) uses SCIP semantic index data
- SCIP provides: symbol definitions, references, type hierarchies, import graphs
- No persistent language servers; semantic data pre-computed by batch indexers
- CodePlane maintains full control of edit application, version tracking, and reindexing

#### Semantic Data Flow

1. User requests refactor (e.g., rename symbol)
2. CodePlane checks mutation gate: all affected files must be CLEAN
3. Query SCIP index for all occurrences of target symbol
4. Generate structured edit plan from occurrence positions
5. Preview edits to user
6. Apply edits atomically
7. Mark affected files as DIRTY, enqueue semantic refresh

#### Edit Application and Reindexing

- Edit plans generated from SCIP occurrence data
- File edits are applied atomically via mutation engine
- Affected files are marked DIRTY and re-indexed
- Syntactic index updated immediately; semantic index refreshed via job queue
- Overlay/untracked files are updated as first-class citizens

### 8.3b Language Support Model

- Semantic refactors available for languages with SCIP indexers installed
- Syntactic-only fallback available via `force_syntactic: true` option
- Unsupported languages can still use syntactic edits (find/replace with confirmation)
- No runtime auto-detection; language support declared at init

### 8.4 Context Discovery & Membership

This section is the **authoritative source** for context discovery, ownership, and membership rules. Sections 8.6–8.9 provide operational details that build on these definitions.

#### 8.4.1 Core Definitions & Correctness Rules

**A. The "Language Family" Key**

All phases must use these exact string identifiers. No other strings are valid.

Unified Families: `javascript`, `python`, `go`, `rust`, `jvm`, `dotnet`, `ruby`, `php`, `swift`, `elixir`, `haskell`, `terraform`, `sql`, `docker`, `markdown`, `json_yaml`, `protobuf`, `graphql`, `config`.

**B. Ownership Model**

Context ownership is defined per-family.

- Query: `get_context(repo_id, file_path, language_family)`
- Result: Returns exactly one `Context` or `None`.
- Implication: A file path can be owned by multiple contexts if and only if they belong to different families.

**C. Path Canonicalization & Containment**

All paths are POSIX Canonical Relative Paths (Separator `/`, no leading `/`, repo root is `""`).

Segment-Safe Containment:

```python
def is_inside(file_path: str, root_path: str) -> bool:
    if root_path == "": return True
    if file_path == root_path: return True
    return file_path.startswith(root_path + "/")
```

**D. Router State (Strict Consistency)**

To prevent non-deterministic file ownership during startup:

- Gating: The Router MUST NOT answer queries until the initial Probe phase has resolved all pending candidates to `valid`, `failed`, `empty`, or `detached`.
- Valid Only: The Router considers ONLY contexts with `probe_status='valid'`.

#### 8.4.2 Phase A: Discovery (Candidate Generation)

Output: A list of `CandidateContext` objects.

Precedence: If multiple Tier 1 fences claim authority over a path, the **Closest Ancestor** wins.

**Strategy 1: Marker-Based Discovery**

Scan directories. If markers match, create candidates for the corresponding family.

| Unified Family | Tier 1 Markers (Workspace Fences) | Tier 2 Markers (Package Roots) |
|----------------|-----------------------------------|--------------------------------|
| javascript | `pnpm-workspace.yaml`, `package.json` (w/ workspaces), `lerna.json`, `nx.json`, `turbo.json`, `rush.json`, `yarn.lock`, `pnpm-lock.yaml` | `package.json`, `deno.json`, `deno.jsonc`, `tsconfig.json`, `jsconfig.json` |
| python | `uv.lock`, `poetry.lock`, `Pipfile.lock` | `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `Pipfile` |
| go | `go.work` | `go.mod` |
| rust | `Cargo.lock`, `Cargo.toml` (w/ `[workspace]`) | `Cargo.toml` |
| jvm | `settings.gradle`, `settings.gradle.kts`, `.mvn` | `build.gradle`, `build.gradle.kts`, `pom.xml`, `build.sbt` |
| dotnet | `*.sln` | `*.csproj`, `*.fsproj`, `*.vbproj` |
| terraform | `.terraform.lock.hcl` | `main.tf`, `versions.tf` |
| ruby | `Gemfile.lock` | `Gemfile` |
| php | `composer.lock` | `composer.json` |
| config | `flake.lock` | `flake.nix` |
| protobuf | `buf.work.yaml` | `buf.yaml` |

**Strategy 2: Ambient Discovery (Root-Only)**

For families without reliable package markers, we GUARANTEE a fallback context.

Families: `sql`, `docker`, `markdown`, `json_yaml`, `graphql` (plus `protobuf` if no `buf.yaml`).

Rule: Always create exactly one Candidate Context at `root_path=""` for each of these families.

#### 8.4.3 Phase A.2: Tier 1 Authority (The Filter)

Filter Tier 2 candidates based on Tier 1 configuration. Applies to `javascript`, `go`, `rust`, `jvm`.

Conflict Resolution: If a Tier 2 root T is inside Tier 1 root A and B (where B is inside A), B is the authoritative fence.

**Javascript (`javascript`):**
- Markers: `package.json` (workspaces), `pnpm-workspace.yaml`. (Lockfiles alone do NOT trigger strict fencing).
- Logic: Root R (the workspace file location) is ALWAYS pending. Sub-roots matching workspace globs are pending. Others are detached.

**Go (`go`):**
- Marker: `go.work`.
- Logic: Listed modules pending. Others detached.

**Rust (`rust`):**
- Marker: `Cargo.toml` (with `[workspace]`).
- Logic: R is pending. Listed members pending. Others detached.

**JVM (`jvm`):**
- Marker: `settings.gradle`, `settings.gradle.kts`.
- Logic: Scan for `include('path')` or `include("path")`.
- Strict Mode: If ALL includes are simple string literals, mark unlisted as detached.
- Permissive Mode: If any variable expansion/concatenation is detected, mark ALL discovered roots as pending.

#### 8.4.4 Phase B: Membership & Exclusion

**1. The Mandatory Hole-Punch Rule**

For every Candidate Context C of Family F:
1. Identify all other Candidates for Family F that satisfy `is_inside(child.root, C.root)`.
2. Normalization: Convert `child.root` to a Canonical Relative Path from `C.root`.
3. Glob Format: Append `/**`. (e.g., if child is `apps/api` relative to C, add `apps/api/**`).
4. Add to `C.exclude_spec`.

Note: This creates the "No-Owner Zone" for files inside detached contexts, which is intended behavior.

**2. The Master Include Spec (Canonical)**

Every marker row from Phase A maps here.

| Family | Type | Include Spec (Canonical Globs) |
|--------|------|--------------------------------|
| javascript | Code | `["**/*.js", "**/*.jsx", "**/*.mjs", "**/*.cjs", "**/*.vue", "**/*.svelte", "**/*.astro", "**/*.ts", "**/*.tsx", "**/*.cts", "**/*.mts"]` |
| python | Code | `["**/*.py", "**/*.pyi", "**/*.pyw", "**/*.pyx", "**/*.pxd", "**/*.pxi"]` |
| go | Code | `["**/*.go"]` |
| rust | Code | `["**/*.rs"]` |
| jvm | Code | `["**/*.java", "**/*.kt", "**/*.kts", "**/*.scala", "**/*.sc"]` |
| dotnet | Code | `["**/*.cs", "**/*.fs", "**/*.fsx", "**/*.vb"]` |
| ruby | Code | `["**/*.rb", "**/*.rake", "**/Gemfile"]` |
| php | Code | `["**/*.php"]` |
| terraform | Data | `["**/*.tf", "**/*.hcl"]` |
| sql | Data | `["**/*.sql"]` |
| docker | Data | `["**/Dockerfile", "**/*.Dockerfile", "**/docker-compose.yml", "**/docker-compose.yaml"]` |
| markdown | Data | `["**/*.md", "**/*.markdown", "**/*.mdx"]` |
| json_yaml | Data | `["**/*.json", "**/*.yaml", "**/*.yml", "**/*.toml", "**/*.jsonc"]` |
| protobuf | Data | `["**/*.proto"]` |
| graphql | Data | `["**/*.graphql", "**/*.gql"]` |
| config | Data | `["**/*.nix"]` |

**Universal Excludes:** `["**/node_modules/**", "**/venv/**", "**/__pycache__/**", "**/.git/**", "**/target/**", "**/dist/**", "**/build/**", "**/vendor/**"]`

#### 8.4.5 Phase C: The Partitioned Probe (Deterministic)

Protocol:
1. Check Status: If `detached`, STOP.
2. Sampling: Select up to 5 files matching `include_spec`.
   - Sort Order: Path length ascending (shortest first), then lexicographical.
   - Note: This minimizes fixture/generated code noise.
3. Validation: Stop at the first file that passes the validation check. If ANY sample passes, the context is `valid`. If ALL samples fail, the context is `failed`.

**Type A: "Code" Families**

Valid File:
- Tree-sitter parse contains Zero ERROR nodes (optional strictness) OR error count is < 10% of total nodes.
- Tree contains at least one Named node that is NOT an Extra (comment/whitespace) and NOT an ERROR.

Failed Context: All sampled files fail the check.
Empty Context: No files match `include_spec`.

**Type B: "Data" Families**

Valid File:
- Tree-sitter parse produces a tree.
- Root node has child count > 0.
- Contains Zero ERROR nodes.

Failed Context: All samples have syntax errors.
Empty Context: No files match `include_spec`.

#### 8.4.6 The Router (Implementation)

```python
class ContextRouter:
    """
    The Canonical Source of Truth.
    State: Active only when probe_status IN ('valid').
    """
    def get_context_for_file(self, repo_id: str, file_path: str, language_family: str) -> Context | None:
        # 1. Fetch Candidates (VALID ONLY)
        candidates = self.get_valid_contexts(repo_id, language_family)
        
        # 2. Sort: Deepest Root First
        candidates.sort(key=lambda c: len(c.root_path), reverse=True)
        
        for ctx in candidates:
            # 3. SEGMENT-SAFE CONTAINMENT
            if not is_inside(file_path, ctx.root_path):
                continue
            
            # 4. EXCLUSION CHECK (Canonical Globs)
            #    Matches path against Universal Excludes + Hole Punches
            if match_globs(file_path, ctx.exclude_spec):
                continue
                
            # 5. INCLUSION CHECK
            if match_globs(file_path, ctx.include_spec):
                return ctx
                
        return None
```

#### 8.4.7 Context Worktree

A persistent Git worktree per context sandbox:

- Reset to base commit R before each operation
- Sparse checkout to minimize I/O

### 8.5 Refactor Execution Flow

**Simple flow (CLEAN + CERTAIN files):**

1. **Mutation Gate Check**: All affected files must be CLEAN + CERTAIN
2. **Query SCIP Index**: Find all occurrences of target symbol
3. **Generate Edit Plan**: Compute structured edits from occurrence positions
4. **Preview**: Show user the planned changes
5. **Apply**: Execute edits atomically via mutation engine
6. **Mark DIRTY**: Affected files enqueued for semantic re-indexing
7. **Syntactic Update**: Immediate update of syntactic index

**Blocked flow (non-CLEAN files):**

If any affected file is DIRTY/STALE/PENDING_CHECK:
- Return `status: "blocked"` with witness packet
- Include `suggested_refresh_scope` for targeted re-indexing
- User can wait for semantic refresh to complete
- Or use `force_syntactic: true` for syntactic-only edit

**Ambiguous flow (CLEAN + AMBIGUOUS files):**

If files are CLEAN but AMBIGUOUS (semantic uncertainty):
- Return `status: "needs_decision"` with:
  - Candidates (each with complete `apply_plan`)
  - Witness packet (structured evidence)
  - Decision capsules (micro-queries agent can answer)
- Agent selects candidate and calls `/decisions/commit` with proof
- See §8.5a for details

### 8.5a Two-Phase Rename (Agent Decision Flow)

Rename is the classic ambiguity case. When semantic identity is uncertain (dynamic dispatch, multiple definitions, reflection), CodePlane returns a decision problem instead of guessing.

**Phase 1: Plan**

```
POST /refactor/rename
{
  "symbol": "MyClass.process",
  "new_name": "handle",
  "context_id": "python-main"
}

Response (needs_decision):
{
  "status": "needs_decision",
  "plan_id": "uuid",
  "symbol": "MyClass.process",
  "candidates": [
    {
      "id": "group_0",
      "description": "MyClass.process in src/core.py (semantic)",
      "confidence": 0.95,
      "provenance": "semantic",
      "occurrences": [...],
      "apply_plan": { "edits": [...] }
    },
    {
      "id": "group_1", 
      "description": "process in src/utils.py (syntactic match)",
      "confidence": 0.6,
      "provenance": "syntactic",
      "occurrences": [...],
      "apply_plan": { "edits": [...] }
    }
  ],
  "witness": { ... },
  "decision_capsules": [...],
  "commit_endpoint": "/decisions/commit",
  "expires_at": "2026-01-29T12:05:00Z"
}
```

**Phase 2: Commit**

Agent reasons over candidates, gathers proof, then commits:

```
POST /decisions/commit
{
  "plan_id": "uuid",
  "selected_candidate_id": "group_0",
  "proof": {
    "symbol_identity": "MyClass.process",
    "anchors": [
      { "file": "src/core.py", "line": 42, "anchor_before": "def ", "anchor_after": "(self" }
    ],
    "file_line_evidence": [
      { "file": "src/core.py", "line": 42, "content_hash": "abc123" }
    ]
  }
}
```

**Critical invariant**: Decision commit MUST re-validate the full mutation gate before applying edits. Verifying anchors and hashes alone is insufficient—the core invariant "semantic writes require CLEAN + CERTAIN" cannot be bypassed.

```
Decision commit flow:
1. Retrieve plan and candidate
2. RE-VALIDATE MUTATION GATE (not just anchors):
   - Recompute affected_files from plan
   - Check file states: all must be CLEAN
   - If non-CLEAN: return blocked with suggested_refresh_scope
   - If CLEAN but now AMBIGUOUS: return needs_decision (state shifted)
3. Verify anchors match current file content
4. Verify file line hashes
5. Apply edits atomically
6. Cache decision for future similar scenarios
```

### 8.5b Witness Packets

Every `blocked` or `needs_decision` response includes a witness packet—structured evidence for agent consumption:

```json
{
  "bounds": {
    "files_scanned": ["src/core.py", "src/utils.py"],
    "contexts_queried": ["python-main"],
    "time_budget_ms": 5000,
    "truncated": false
  },
  "facts": [
    {
      "fact_type": "definition",
      "location": {"file": "src/core.py", "line": 42},
      "content": "def process(self, data):",
      "provenance": "semantic",
      "confidence": 0.95
    }
  ],
  "invariants_failed": ["files_not_clean"],
  "disambiguation_checklist": [
    {
      "question": "Which definition is in scope at the call site?",
      "fact_needed": "scope_resolution",
      "how_to_verify": "Check import chain from line 15"
    }
  ]
}
```

### 8.5c Decision Capsules

Pre-packaged micro-queries that agents can answer by reading code:

| Capsule Type | Question | Stop Rule |
|--------------|----------|-----------|
| `scope_resolution` | Which of these N definitions is in scope at cursor? | First importable, non-shadowed definition |
| `receiver_resolution` | Which receivers can reach this call? | All assignments flowing to receiver position |
| `context_membership` | Which contexts include this file? | All contexts whose patterns match |

Capsules reduce ambiguity from "here's everything, figure it out" to "answer this specific bounded question."

### 8.6 Multi-Context Handling

> See §8.4 for authoritative context discovery and ownership rules.

When multiple semantic contexts exist for a language (e.g., multiple Python venvs):

**Detection:**
- Each context produces independent SCIP index data
- Same file may have different semantic interpretations per context

**Refactor behavior:**
- Query all relevant contexts
- Merge occurrence sets
- Detect divergence (same position, different symbol identity)
- If divergent: fail and report conflicting contexts
- If consistent: proceed with merged occurrence set

CodePlane never silently guesses semantics.

### 8.7 Context Selection Rules

> Context ownership is defined in §8.4. This section covers selection for refactor operations.

Minimum set:

- Context owning the definition file
- Contexts including known dependents (from index/config)

If uncertain:

- Query all contexts for that language (bounded by config)

### 8.8 Context Detection at Init

> Discovery phases and marker tables are defined in §8.4.2–8.4.3. This section covers initialization behavior.

Principle: best-effort and safe; require explicit config when ambiguous.

Signals:

- .NET: multiple `.sln`
- Java: multiple independent `pom.xml` / `build.gradle`
- Go: multiple `go.mod` not unified by `go.work`
- Python: multiple env descriptors in separate subtrees

Classification:

- Single context → uses single context data
- Multiple valid roots → multi-context mode
- Ambiguous → require explicit config

Persistence:

- `.codeplane/contexts.yaml` (versioned schema)

### 8.9 Configuration Model (Minimal)

```yaml
contexts:
  - id: core-java
    language: java
    workspace_roots: [./core]
    env:
      build_root: ./core
    indexer:
      name: scip-java

defaults:
  max_parallel_contexts: 4
  divergence_behavior: fail
```

### 8.10 Git-Aware File Moves

- If a file rename or move affects a Git-tracked file:
  - CodePlane will perform a `git mv`-equivalent operation
  - This updates Git's index to reflect the move (preserving history)
  - Only performed if the file is clean and tracked
  - Fails safely if the working tree state is inconsistent (e.g. modified, unstaged)
- If the file is untracked or ignored (e.g. overlay files):
  - CodePlane performs a normal filesystem move only
- This ensures Git rename detection and downstream agent operations remain correct
- Preserves history; never commits

Structured diff will reflect:
```json
{
  "file_moved": true,
  "from": "src/old_path.py",
  "to": "src/new_path.py",
  "git_mv": true
}
```

### 8.11 Comments and Documentation References

SCIP-based renames **do not affect** comments, docstrings, or markdown files.

Examples of unaffected references:
- `# MyClassA` (comment)
- `"""Used in MyClassA."""` (docstring)
- `README.md` references to `MyClassA`
- Code examples in documentation
- Inline code references (`` `MyClassA` ``)

#### Auto-Update with Warning

CodePlane performs a **post-refactor documentation sweep** that:

1. **Scans** for textual references to the renamed symbol:
   - Comments in source code (from structural index)
   - Documentation files (markdown, RST, AsciiDoc, plain text)
   - Docstrings (extracted during indexing)
   - Code blocks in documentation (parsed for symbol references)
   - Inline code spans (`` `SymbolName` ``)

2. **Categorizes** matches by confidence:
   - **High confidence**: Exact match in backticks, code blocks, or import statements
   - **Medium confidence**: Exact match in prose near code context
   - **Low confidence**: Partial match or ambiguous context

3. **Auto-applies** changes but **flags for review**:
   - All documentation edits are applied in the same atomic patch
   - The response includes a `doc_updates_applied` field with:
     - Files changed
     - Matches found (with confidence levels)
     - Line numbers and context
   - A `review_recommended: true` flag when any low/medium confidence matches exist

4. **Structured response** includes both semantic and documentation edits:

```json
{
  "refactor": "rename_symbol",
  "semantic_edits": {
    "files_changed": 12,
    "edits": [...]
  },
  "doc_edits": {
    "files_changed": 3,
    "review_recommended": true,
    "matches": [
      {
        "file": "README.md",
        "line": 45,
        "confidence": "high",
        "context": "See `MyClassA` for details"
      },
      {
        "file": "docs/guide.md", 
        "line": 123,
        "confidence": "medium",
        "context": "The MyClassA handles authentication"
      }
    ]
  }
}
```

The agent receives the full diff and can verify documentation updates make sense in context. Since the operation is atomic, rollback reverts both semantic and documentation changes together.

#### Configuration

```yaml
refactor:
  doc_sweep:
    enabled: true           # default
    auto_apply: true        # apply doc changes automatically
    min_confidence: medium  # only auto-apply medium+ confidence
    scan_extensions:
      - .md
      - .rst
      - .adoc
      - .txt
```

This ensures textual references to renamed symbols are coherently updated without being conflated with semantic SCIP-backed mutations, while giving agents visibility into what changed and why.

### 8.12 Optional Subsystem Toggle

The deterministic SCIP-backed refactor engine is **enabled by default**, but may be disabled via configuration or CLI for environments with limited resources.

**Why disable:**
- SCIP indexers consume resources during indexing
- Some users may prefer to delegate refactors to agents or external tools

**How to disable:**

Via config:
```yaml
refactor:
  enabled: false
```

Or CLI:
```bash
cpl up --no-refactor
```

When disabled:

- No SCIP indexers run
- No refactor endpoints
- Syntactic indexing and generic mutation remain

### 8.13 Refactor Out of Scope

- Git commits, staging, revert, or history manipulation
- Test execution or build validation
- Refactor logs beyond structured diff response
- Dynamic language inference (e.g., `eval`, `getattr`)
- Partial or speculative refactors
- Multi-symbol refactors

### 8.14 Guarantees + Result Types

Always:
- **Deterministic**: Same refactor input → same result
- **Isolated**: Edits are applied only to files with CLEAN semantic state
- **Audit-safe**: Git-aware moves preserve index correctness
- **Overlay-compatible**: Untracked files handled equally
- **Agent-delegated commit control**: CodePlane never stages or commits
- No working tree mutation during planning
- Single atomic apply
- Explicit divergence reporting

Best-effort:

- Validation reporting
- Coverage limited to successfully loaded contexts

Results:

- Applied: merged patch, contexts used, optional validation results
- Divergence: conflicting hunks, contexts involved, diagnostics
- InsufficientContext: no viable context loaded; explicit configuration required

---

## 9. Mutation Engine (Atomic File Edits)

### 9.1 Design Objectives

- Never leave repo partial/corrupt/indeterminate.
- Always apply mutations atomically, or not at all.
- Permit concurrent mutations only when edits are disjoint.
- Maintain clean separation between file mutations and Git state (except rename tracking).
- Predictable cross-platform behavior (line endings, permissions, fsync).
- Always emit a structured delta reflecting the full effect.

### 9.2 Apply Protocol

- All edits are planned externally (SCIP index or reducer).
- All file edits staged in memory or temp files.
- Each target file exclusively locked prior to apply.
- Contents replaced wholesale via:
  - `os.replace()` (POSIX)
  - `ReplaceFile()` (Windows)
- `fsync()` called on new file and parent directory for durability.
- CRLF normalized to LF during planning; re-encoded on write to preserve original form.
- No in-place edits.

### 9.3 Concurrency Model

- Thread pool executor applies independent files in parallel.
- Thread count defaults to number of vcores.
- Final file write + rename serialized per file.
- Preconditions (hash or mtime+size) must pass prior to apply; otherwise abort.
- Overlapping mutations detected and blocked.

### 9.4 Scope Enforcement

- All file edits must fall within explicit working set or allowlist.
- `.cplignore` paths categorically excluded.
- Git-ignored files are editable but flagged for agent confirmation.
- New file paths created under allowed directory accepted.
- Mutations that touch unscoped paths rejected pre-apply.

### 9.5 Structured Delta Format (Required)

Per-file:

- `path`: relative path
- `oldHash`: pre-edit SHA256
- `newHash`: post-edit SHA256
- `lineEnding`: LF | CRLF
- `edits`: array of `{ range: {start: {line, char}, end: {line, char}}, newText, semantic, symbol? }`

Global:

- `mutationId`: UUID or agent-generated key
- `repoFingerprint`: hash of full file state
- `symbolsChanged`: optional list of semantic symbols affected
- `testsAffected`: optional list of test names

### 9.6 Failure and Rollback

- Any failure during write, rename, or precondition check aborts the batch.
- Temp files deleted.
- Locks released.
- Repo left in original state.
- No Git commands run as part of rollback.

### 9.7 Git Behavior (Mutation Engine)

- `git mv` is the only allowed Git mutation, and only for clean tracked files.
- Git index, HEAD, or refs are never modified.
- No Git status, reset, merge, stash operations triggered as rollback.

### 9.8 SCIP and Edit Planning

- All semantic refactors sourced from SCIP index data.
- No fallback to internal symbol index for semantic edit planning.
- All edits must conform to a unified diff format.

### 9.9 Performance Constraints

- Full-batch application of ~20 files should complete in <1s on modern SSD.
- Pre-write prep (diff, temp staging) parallelized.
- Final apply (rename+fsync) serialized and lock-guarded.
- No assumption of in-place edit savings.

### 9.10 Out of Scope (Mutation Engine)

- No Git commits, staging, reset, stash, merge.
- No recovery using Git state.
- No in-place edits or patch files.
- No speculative edits or partial semantic ops.

---

## 10. Git and File Operations (No Terminal Mediation)

Git:

All Git operations via `pygit2` (libgit2 bindings):

- **Read operations:** status, diff, blame, log, branches, tags, remotes, merge analysis
- **Write operations:**
  - Index: stage, unstage, discard
  - Commits: commit, amend
  - Branches: create, checkout, delete, rename
  - History: reset (soft/mixed/hard), merge, cherry-pick, revert
  - Stash: push, pop, apply, drop, list
  - Tags: create, delete
  - Remotes: fetch, push, pull
  - Rebase: plan, execute, continue, abort, skip (interactive rebase support)
  - Submodules: list, status, init, update, sync, add, deinit, remove
  - Worktrees: list, add, open, remove, lock, unlock, prune

Note: Some submodule operations (update, sync, add, deinit, remove) and worktree
remove use subprocess fallbacks to `git` CLI for completeness and credential
support where pygit2 bindings are incomplete.

Credentials for remote operations:
- SSH: via `KeypairFromAgent` (uses system SSH agent)
- HTTPS: via credential helper callback that invokes `git credential fill`

Agents never run git shell commands directly (except for the subprocess fallbacks noted above).

File operations:

- Native Python
- Atomic writes
- Hash-checked
- Scoped

Critical mutation semantics rule:

Every state-mutating operation returns a complete structured JSON delta including:

- Files changed
- Hashes before/after
- Diff stats
- Affected symbols
- Affected tests
- Updated repo state

This exists to eliminate verification loops and follow-up probing.

---

## 11. Tests: Planning, Parallelism, Execution

### 11.1 Goal

Fast deterministic test execution across large suites by parallelizing at test **target** level (files, packages, classes). Must support any language CodePlane indexes.

### 11.2 Definitions

- Test Target: smallest runnable unit CodePlane manages (e.g., a test file or Go package).
- Worker: CodePlane-managed subprocess executing one or more targets.
- Batch: set of targets assigned to worker.
- Estimated Cost: scalar weight used to balance batches (default 1).

### 11.3 Target Model

```json
{
  "target_id": "tests/test_utils.py",
  "lang": "python",
  "kind": "unit",
  "cmd": ["pytest", "tests/test_utils.py"],
  "cwd": "repo_root",
  "estimated_cost": 1.2
}
```

### 11.4 Execution Strategy

1. Discover targets:
   - per-language logic
   - stable `target_id`
   - default `estimated_cost`
2. Greedy bin packing:
   - assign to N workers by cost-balanced packing
3. Parallel execution:
   - spawn N subprocesses
   - each runs its batch sequentially
   - per-target and global timeouts
4. Merge results:
   - parse outputs to structured schema
   - classify failures
   - detect retries
   - label flaky outcomes

### 11.5 Test Runner Discovery

CodePlane uses a three-tier resolution strategy: explicit config → marker detection → language defaults.

#### Resolution Order (First Match Wins)

1. **Explicit config** in `.codeplane/config.yaml`
2. **Marker file detection** (see table below)
3. **Language default** (fallback)

#### Marker File Detection

| Marker | Runner | Priority |
|--------|--------|----------|
| `pytest.ini` | pytest | High |
| `pyproject.toml` with `[tool.pytest]` | pytest | High |
| `setup.cfg` with `[tool:pytest]` | pytest | Medium |
| `jest.config.js`, `jest.config.ts`, `jest.config.json` | jest | High |
| `package.json` with `"jest"` key | jest | Medium |
| `vitest.config.js`, `vitest.config.ts` | vitest | High |
| `go.mod` | go test | High |
| `Cargo.toml` | cargo test | High |
| `*.csproj` with test references | dotnet test | High |
| `pom.xml` | mvn test | Medium |
| `build.gradle`, `build.gradle.kts` | gradle test | Medium |
| `Gemfile` with rspec | rspec | Medium |
| `mix.exs` | mix test | High |

#### Language Defaults (When No Marker Found)

| Language | Default Runner |
|----------|---------------|
| Python | pytest |
| JavaScript/TypeScript | jest |
| Go | go test |
| Rust | cargo test |
| Java | mvn test |
| C# | dotnet test |
| Ruby | rspec |
| Elixir | mix test |

#### Config Override

```yaml
tests:
  runners:
    # Override detected runner
    python: pytest
    typescript: vitest  # Use vitest instead of detected jest
    
  # Custom runners for specific patterns
  custom:
    - pattern: "e2e/**/*.spec.ts"
      runner: playwright
      cmd: ["npx", "playwright", "test", "{path}"]
    - pattern: "integration/**/*.test.py"
      runner: pytest
      cmd: ["pytest", "--integration", "{path}"]
      timeout_sec: 120
      
  # Exclude patterns from test discovery
  exclude:
    - "**/fixtures/**"
    - "**/mocks/**"
```

#### Multiple Runners in Same Repo

When multiple test frameworks are detected:
- Each is registered independently
- Test targets are tagged with their runner
- Parallel execution respects runner boundaries
- Results are merged with runner attribution

Example: repo with jest (unit) + playwright (e2e) + pytest (backend):
```json
[
  {"target_id": "src/__tests__/utils.test.ts", "runner": "jest"},
  {"target_id": "e2e/login.spec.ts", "runner": "playwright"},
  {"target_id": "tests/test_api.py", "runner": "pytest"}
]
```

#### Runner Not Found

If a runner is configured but not available in PATH:
- `cpl doctor` reports: `Test runner 'pytest' not found in PATH`
- Test operations return error `7001 TEST_RUNNER_NOT_FOUND`
- CodePlane does not install test runners (user responsibility)

### 11.6 Language-Specific Targeting Rules

Target rules depend on language + available runner; supports any language with:

- recognized parser (Tree-sitter)
- declarative discovery of test files/commands
- CLI runner that can execute individual test units

| Language | Target Granularity | Target ID Example | Cmd Template |
|---|---|---|---|
| Python | File (`test_*.py`) | `tests/test_utils.py` | `pytest {path}` |
| Go | Package (`./pkg/foo`) | `pkg/foo` | `go test -json ./pkg/foo` |
| JS/TS | File (`*.test.ts`) | `src/__tests__/foo.test.ts` | `jest {path}` |
| Java | Class or module | `com.example.FooTest` | `mvn -Dtest=FooTest test` |
| .NET | Project or class | `MyProject.Tests.csproj` | `dotnet test {path}` |
| Rust | File or module | `tests/integration_test.rs` | `cargo test --test {name}` |
| Ruby | File (`*_spec.rb`) | `spec/models/user_spec.rb` | `rspec {path}` |
| Elixir | File (`*_test.exs`) | `test/my_app_test.exs` | `mix test {path}` |

### 11.6 Defaults

- `N = min(#vCPUs, 8)`
- Target cost = 1 if unknown
- Fail-fast: stop if first failure batch completes (configurable)
- Timeout: 30s per target (configurable)

### 11.7 Optional Enhancements

- Historical cost recording per target (rolling median)
- Resource class labels (`unit`, `integration`, etc.)
- Test suite fingerprints for delta debugging

### 11.8 Out of Scope

- Per-test-case parallelism
- CI sharding or remote execution
- API interface definition (handled separately)

---

## 12. Task Model, Convergence Controls, and Ledger

### 12.1 Scope and Principle

CodePlane models tasks, enforces convergence bounds, and persists an operation ledger.

Core principle:

CodePlane never relies on agent discipline; it enforces mechanical constraints making non-convergence visible, finite, and auditable.

### 12.2 Task Definition and Lifecycle

A task is a correlation envelope for operations.

A task exists to:

- group related operations
- apply execution limits
- survive server restarts
- produce structured outcomes

A task does not:

- own control flow
- store agent reasoning
- perform retries
- infer success/failure

Lifecycle states:

| State | Meaning |
|---|---|
| OPEN | Task active; operations correlated |
| CLOSED_SUCCESS | Task ended cleanly |
| CLOSED_FAILED | Task aborted due to limits/invariants |
| CLOSED_INTERRUPTED | Server restart or client disconnect |

Tasks are explicitly opened and closed; never reopened implicitly.

Persisted task state:

```yaml
task_id: string
opened_at: timestamp
closed_at: timestamp | null
state: OPEN | CLOSED_*
repo_snapshot:
  git_head: sha
  index_version: int
limits:
  max_mutations: int
  max_test_runs: int
  max_duration_sec: int
counters:
  mutation_count: int
  test_run_count: int
last_mutation_fingerprint: string | null
last_failure_fingerprint: string | null
```

Not persisted:

- prompts
- agent intent
- reasoning traces
- retry logic

### 12.3 Convergence Controls (Server-Enforced)

1. Mutation budget:
   - Each state-mutating call increments `mutation_count`.
   - If `mutation_count > max_mutations`, reject mutation and set task to CLOSED_FAILED.

2. Test execution budget:
   - Test runs are first-class operations.
   - If `test_run_count > max_test_runs`, reject further test calls.

3. Failure fingerprinting:
   - Deterministic failures fingerprinted using:
     - failing test names
     - normalized exception type
     - normalized stack trace
     - exit code
   - Fingerprint returned in each failure response.
   - If same fingerprint occurs after a mutation, CodePlane flags non-progress.

4. Mutation fingerprinting:
   - Each mutation returns fingerprint:
     - `files_changed_hash`
     - `diff_stats`
     - `symbol_changes`
   - Identical consecutive mutation fingerprints:
     - mark as no-op
     - budget still increments

CodePlane does not decide next step.

### 12.4 Restart Semantics

On server restart:

- All OPEN tasks marked CLOSED_INTERRUPTED.
- Repo reconciled from Git.
- Indexes revalidated incrementally.
- No task resumes implicitly.

Clients must open a new task.

Guarantees:

- No mixed state
- No replayed side effects
- No phantom progress

### 12.5 Operation Ledger

#### v1 vs v1.5 Scope

CodePlane deliberately distinguishes between **v1 (minimal, SQLite-only)** logging and **v1.5 (optional artifact expansion)**.

- v1 focuses on *mechanical accountability* only.
- v1.5 exists solely to improve developer ergonomics if real pain appears.

#### Purpose

The ledger provides **mechanical accountability**, not observability or surveillance.

It exists to answer:
- what happened
- in what order
- under what limits
- with what effects

Primary persistence:

- Local append-only SQLite DB owned by server, stored in repo:
  - `.codeplane/ledger.db`

v1 ledger schema (SQLite only):

```sql
tasks (
  task_id TEXT PRIMARY KEY,
  opened_at TIMESTAMP,
  closed_at TIMESTAMP,
  state TEXT,
  repo_head_sha TEXT,
  limits_json TEXT
);

operations (
  op_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  timestamp TIMESTAMP,
  duration_ms INTEGER,
  op_type TEXT,
  success BOOLEAN,

  -- repo boundaries
  repo_before_hash TEXT,
  repo_after_hash TEXT,

  -- mutation summary (no content)
  changed_paths TEXT,           -- JSON array of file paths
  diff_stats TEXT,              -- files_changed, insertions, deletions
  short_diff TEXT,              -- e.g. "+ foo.py", "- bar.ts", "~ baz.go"

  -- convergence signals
  mutation_fingerprint TEXT,
  failure_fingerprint TEXT,
  failure_class TEXT,
  failing_tests TEXT,
  limit_triggered TEXT,

  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);
```

Ledger is append-only.

Optional artifact store (v1.5, deferred):

- Only if needed for debugging.
- Stores:
  - full test logs
  - full diffs/patches
  - tool stdout/stderr
- Stored on filesystem; referenced by artifact_id + hash in SQLite.
- Short-lived (hours/days).
- Derived mirror (non-authoritative) may exist:
  - `~/.codeplane/ledger/YYYY-MM-DD.ndjson`
- Ledger remains authoritative; artifacts disposable.

Retention policy:

- v1 default:
  - retain 7–14 days or last 500 tasks
  - configurable
- v1.5:
  - artifacts retained 24–72 hours
  - aggressively GCed
  - missing artifacts never invalidate ledger integrity

Audit model:

- Intended auditors: developers, agent/tool authors, maintainers.
- Explicitly not for: compliance surveillance, user monitoring, model training.

Explicitly does not do:

- no retries
- no backoff
- no strategy shifts
- no planning
- no success inference

---

## 13. Observability and Operator Insight

### 13.1 Why Observability

CodePlane is infrastructure. Infrastructure requires visibility.

Operators need to answer:

- Is the server healthy?
- Are agents making progress or spinning?
- Which operations are slow, failing, or succeeding?
- Is the index fresh or stale?

Without observability, operators debug blind.

### 13.2 Scope and Principles

Observability in CodePlane serves **operators and tool authors**, not surveillance or model training.

Principles:

1. **Visibility without overhead**: Observability is always-on, not sampled or opt-in.
2. **Structured and queryable**: Telemetry is structured data, not log grep.
3. **Bundled and self-contained**: No external dependencies required. Dashboard ships with server.
4. **Standards-based**: OpenTelemetry for traces and metrics. Exportable but not required.

### 13.3 What CodePlane Monitors

Observability covers three categories:

#### Operations (Request-Level)

Every MCP operation emits a trace with spans:

- Operation type, parameters, and outcome
- Duration and timing breakdown
- Task correlation (if within a task envelope)
- Files touched, symbols resolved, tests run
- Error codes and failure fingerprints

Purpose: Understand what agents are doing, how long it takes, and what fails.

#### System Health (Server-Level)

The server exposes continuous health metrics:

| Metric | What It Measures |
|--------|------------------|
| Index staleness | Time since last reconciliation; drift from Git HEAD |
| Indexer status | Per-language SCIP indexer availability and last run |
| Resource usage | Memory, CPU, open file handles |
| Reconciliation rate | Reconciliations per minute; duration histogram |
| Task throughput | Tasks opened/closed per interval; budget exhaustion rate |

Purpose: Know if the server is healthy before problems compound.

#### Convergence Signals (Agent-Level)

Observability surfaces agent progress signals:

| Signal | What It Measures |
|--------|------------------|
| Mutation fingerprint repetition | Same fingerprint after mutation → no progress |
| Failure fingerprint repetition | Same failure after mutation → non-converging |
| Budget utilization | Percentage of task budget consumed |
| Operation cadence | Operations per minute; pauses and bursts |

Purpose: Detect spinning agents and non-convergent loops without CodePlane making decisions.

### 13.4 How Operators Access Observability

#### Dashboard Endpoint

The server exposes a unified dashboard at `/dashboard`:

- Bundled with server; no external setup
- Accessible via browser at `http://127.0.0.1:<port>/dashboard`
- Unified view of traces, metrics, and health

Dashboard capabilities:

- Filter operations by task, operation type, outcome, time range
- View individual traces with span breakdowns
- Monitor real-time health metrics
- Identify slow or failing operations

#### Metrics Endpoint

The server exposes a Prometheus-compatible metrics endpoint at `/metrics`:

- Scrapeable by external monitoring systems
- Useful for fleet-level aggregation (optional, not required)
- Includes all health metrics from section 13.3

#### Programmatic Access

- Traces: Available via OpenTelemetry export (optional configuration)
- Metrics: Available via `/metrics` endpoint
- Ledger: Remains the authoritative record (section 12.5)

### 13.5 Relationship to Ledger

The ledger (section 12) and observability serve different purposes:

| Aspect | Ledger | Observability |
|--------|--------|---------------|
| Purpose | Mechanical accountability | Operational insight |
| Retention | Days to weeks | Real-time + short-term |
| Audience | Post-hoc audit | Live debugging |
| Format | SQLite, append-only | Traces, metrics, dashboards |
| Scope | Task and operation records | System-wide health |

They complement, not replace, each other.

### 13.6 What Observability Does Not Do

Observability does not:

- Make decisions for agents
- Trigger alerts or automated responses
- Persist indefinitely (traces are ephemeral; ledger is durable)
- Phone home or transmit externally (unless explicitly configured)
- Require external infrastructure to function

Observability is passive visibility, not active control.

---

## 15. "Deterministic Refactoring Primitives" (Summary-Level Capability List)

This section preserves the explicit capability list for quick reference.

Refactors described as tool operations:

- `rename_symbol(from, to, at)`
- `rename_file(from_path, to_path)`
- `move_file(from_path, to_path)`
- `delete_symbol(at)`

Implementation:

- All semantic refactors use SCIP index data as the sole authority
- CodePlane never guesses or speculatively resolves bindings
- Non-semantic operations (exact-match comment/docstring sweeps, mechanical file renames) are handled separately and reported as optional, previewable patches

All refactors:

- Produce atomic edit batches
- Provide previews
- Apply via CodePlane patch system
- Return full structured context

---

## 16. Embeddings Policy

Embeddings are intentionally excluded from the core design.

Rationale:

- Agents can explore structure deterministically.
- Embedding lifecycle cost is high.
- Core value is indexing + structure + execution.

If added later:

- Optional
- Gated
- Partial
- Never foundational

---

## 17. Subsystem Ownership Boundaries (Who Owns What)

### 15.1 CodePlane Owns

- Repo reconciliation (Git-centric, deterministic)
- Indexing:
  - Tantivy lexical index
  - SQLite structural metadata
  - Graph construction/traversal bounds
  - Atomic index updates
- Shared tracked index artifact production/consumption rules (CI build, checksum verify, cache, forward-compat limits)
- Overlay index lifecycle (local-only, rebuildable)
- File mutation application protocol:
  - lock
  - scope enforce
  - atomic apply
  - structured deltas
- Semantic refactor protocol:
  - contexts
  - worktrees
  - patch merge
  - divergence reporting
  - single atomic apply
- Test target discovery adapters + parallel target execution harness
- Task envelopes + convergence limits
- Operation ledger persistence + retention + optional artifacts
- Operator CLI + lifecycle + diagnostics + config layering

### 15.2 CodePlane Does Not Own

- Planning, strategy selection, retries, success inference
- Editor buffer state; it reconciles from disk + Git
- Git commits, staging/branch management flows, merges, rebases, stashes, resets (explicitly out of scope for mutation engine; read-only operations allowed)
- Embeddings-first semantic retrieval
- Remote execution / CI sharding

---

## 18. Resolved Conflicts (Previously Open)

The following contradictions have been resolved:

1. **`.env` overlay indexing**: Resolved. Default-blocked in `.cplignore`. Users can explicitly whitelist via `!.env` if needed. See section 6.10.

2. **Refactor fallback semantics**: Resolved. Semantic refactors are SCIP-based; CodePlane never guesses bindings. "Structured lexical edits" refers only to non-semantic operations (exact-match comment sweeps, mechanical file renames). These are explicitly not semantic refactors.

3. **Tree-sitter failure policy**: Resolved. On parse failure, skip file, log warning, continue indexing. Never abort the indexing pass for a single file failure. See section 7.4.

4. **"Always-on" framing vs explicit lifecycle**: Resolved. CodePlane is conceptually a control plane, operationally a repo-scoped server managed via `cpl up` (Ctrl+C to stop). OS service integration is deferred.

---

## 19. Semantic Support Exploration (Design Archaeology)

This section documents the semantic indexing approaches explored during CodePlane development. The designs described here were investigated, partially implemented, and ultimately **reverted** in favor of a simpler planner-based architecture. This record is preserved to prevent future re-exploration of known dead-ends.

### 19.1 Approaches Explored

#### Tree-sitter-only Symbol Graphs (Defs/Refs/Scopes)

**What was tried:**
- Extract definitions, references, and scopes entirely via Tree-sitter queries
- Build best-effort binding graphs within files using syntactic scope nesting
- Use syntactic interface hashing to detect "likely safe" rename targets

**Why it failed:**
- Cross-file references require semantic resolution that Tree-sitter cannot provide
- Dynamic languages (Python, JavaScript) have binding semantics invisible to syntax
- False positives in binding led to silently incorrect renames
- Interface hashing was fragile and produced false invalidation cascades

#### Best-Effort Binding and Anchor-Group Approaches

**What was tried:**
- Group ambiguous references into "anchor groups" keyed by receiver shape + member token
- Use heuristic confidence scores to surface "likely correct" candidates
- Return bounded candidate sets with exemplars + counts

**Why it failed:**
- Anchor group explosion: large codebases produced thousands of groups
- No reliable way to distinguish "probably right" from "dangerously wrong"
- Agent disambiguation burden shifted problem rather than solving it
- Confidence scores were unprovable and misleading

#### Export-Surface Fingerprinting and Invalidation

**What was tried:**
- Hash public interface (exported symbols, signatures) per module
- Only re-index dependents when interface hash changes
- Demand-driven rebinding: defer cross-file resolution until query time

**Why it failed:**
- Interface boundaries are often unclear (Python has no enforced public/private)
- Re-export chains (`from x import *`) broke fingerprint isolation
- Demand-driven rebinding was too slow for interactive use
- Invalidation cascades when a widely-used module changed

#### LSP-Based Designs

**What was tried:**
- Use persistent Language Server Protocol servers for semantic queries
- LSP provides precise references, type hierarchies, and symbol resolution

**Why rejected:**
- Memory overhead: 200MB–1GB+ per language server
- Cold start latency: 5–30+ seconds per project
- Multi-environment complexity: cannot easily run multiple interpreters/SDKs
- Server resource constraints: CodePlane must remain lightweight
- Multi-worktree hostility: LSP assumes single project root
- Operational complexity outweighed benefits for refactor planning

#### SCIP Batch Indexers

**What was tried:**
- One-shot indexers (scip-python, scip-go, scip-typescript, etc.)
- Batch process outputs SCIP protobuf files with symbols, occurrences, relationships
- Import into SQLite for query-time resolution
- File state model: Freshness (CLEAN/DIRTY/STALE/PENDING_CHECK) × Certainty (CERTAIN/AMBIGUOUS)
- Refresh job workers with HEAD-aware deduplication and scoped refresh

**Why it failed:**
- **Identity instability**: SCIP symbol identifiers are version-specific; indexer updates broke cached edges
- **Candidate floods**: Cross-file semantic queries returned unbounded result sets
- **Stale semantics**: Time between file edit and semantic refresh created correctness windows
- **Multi-worktree hostility**: SCIP indexers assume monolithic project structure
- **Profile complexity**: Different Python interpreters, Node versions produced incompatible indexes
- **Operational burden**: Installing, versioning, and updating indexers per language
- **Complexity-benefit mismatch**: Full semantic engines don't pay off for bounded rename planning

#### Hybrid Approaches

**What was tried:**
- Tree-sitter for "syntactic certainty" within files
- SCIP for cross-file "semantic authority"
- Runtime upgrade: Anchored → Strong when semantic data confirms binding

**Why it failed:**
- Query-time upgrades were non-deterministic (depended on refresh timing)
- Mixed confidence levels in output were confusing
- "Partial semantic" results were worse than "no semantic" for agent trust

### 19.2 Explicit Failure Modes Discovered

| Failure Mode | Impact | Root Cause |
|--------------|--------|------------|
| Identity instability | Broken edges after indexer upgrade | SCIP symbol strings encode version-specific details |
| Candidate floods | Unbounded result sets overwhelm agents | No principled way to cap without losing correctness |
| Stale semantics | Renames based on outdated index state | File content changes faster than refresh completes |
| Multi-worktree hostility | Per-worktree index state conflicts | Semantic engines assume single checkout |
| Profile divergence | Python venv A ≠ venv B semantics | Same file has different bindings per environment |
| Operational complexity | Users don't install/maintain indexers | Adding 8+ external tools is hostile to adoption |

### 19.3 Conclusion: Planner-Based Architecture

The correct shippable baseline is a **refactor planner** with a **full stacked index**:

**Tier 0 — Tantivy Lexical Index (always-on):**
- Fast, deterministic lexical retrieval
- Candidate discovery, never semantic authority
- One document per file with file_id, path, language, content tokens

**Tier 1 — Tree-sitter/SQLite Structural Facts:**
- DefFact: definitions with kind, range, signature hash
- RefFact: references with token text, role, certainty
- ScopeFact: lexical scope nesting
- LocalBindFact: same-file bindings (syntactically provable)
- ImportFact: explicit import statements (not dynamic resolution)
- ExportSurface: exported names per module

**Planner Output (not semantic authority):**
- Bounded candidate sets
- Patch previews with text edits
- Coverage + Risk manifest (explicit about what is PROVEN vs ANCHORED)
- Auto-apply limited to PROVEN edits (lexical matches, same-file bindings)
- Everything else is proposal-only unless explicitly promoted

**What "PROVEN" means:**
- Same-file definition-reference within lexical scope
- Import statements with explicit module path
- Export re-declarations matching import source

**What remains "ANCHORED" (proposal-only):**
- Cross-file references without import chain proof
- Dynamic access patterns
- Receiver-based dispatch
- Multi-hop import chains

### 19.4 Future Direction (Explicitly FUTURE / PROBABLE)

**Tier 3: Optional semantic backends (FUTURE)**

Semantic engines (SCIP, LSP, compiler APIs) may be reintroduced as:
- Opt-in batch jobs per language/project
- User-initiated, not server-managed
- Results cached but not authoritative
- Planner remains the default UX

**Gating criteria for reintroduction:**
- Documented complexity-benefit analysis per language
- Clear operational model (install, version, update)
- Proof that semantic confidence exceeds planner confidence for target use case
- No impact on server startup time or memory footprint

Until these criteria are met, semantic engines are explicitly deferred.

---

## 20. Risk Register (Remaining Design Points)

Items 1-3 from the original register have been resolved (see section 16). Remaining items:

1. Multi-context scaling:
   - context explosion risk
   - operational limits beyond `max_parallel_contexts` not fully specified
2. Optional watchers:
   - must never become correctness-critical
   - must not violate "no background mutation"
3. Security posture depends on Git hygiene:
   - secrets committed to Git leak into shared artifacts by definition; mitigations are external (pre-commit hooks, scanning)

---

## 21. Readiness Note: What Is Stable Enough for API Surfacing Next

Stable enough that API design should be mechanical:

- Repo fingerprinting, reconciliation triggers, and invariants
- Index composition and update protocol
- Structured delta requirements for all mutations
- Mutation apply protocol and scope rules
- Refactor context/worktree planning and divergence reporting shapes
- Test target model and parallel execution semantics
- Task envelope semantics, budgets, fingerprinting, restart behavior
- Ledger schema, retention policy, optional artifact model
- CLI lifecycle and operability checks
- Config layering and defaults framework
- Observability model, trace/metric categories, and dashboard scope

All previously-open contradictions have been resolved. API surfacing can proceed.

---

## 22. What CodePlane Is (Canonical Summary)

CodePlane is:

- A repository control plane
- A deterministic execution layer
- A structured context provider
- A convergence enforcer

It turns AI coding from slow and chaotic into fast, predictable, and auditable by fixing the system, not the model.

---

## 23. MCP API Specification

### 23.1 Design Principles

The MCP API is the primary interface for AI agents to interact with CodePlane.

Core design choices:

| Dimension | Choice | Rationale |
|-----------|--------|-----------|
| Protocol | **Hybrid**: MCP (tools) + REST (admin) | MCP for agents, REST for operators |
| Framework | **FastMCP**: Official MCP Python SDK | Zero custom protocol code, schema from types |
| Granularity | **Namespaced families**: ~35 tools | One tool per operation, grouped by prefix |
| Streaming | **Context.report_progress**: native MCP | Progress via protocol, not separate tools |
| Naming | **Prefixed families**: `git_*`, `search_*`, etc. | Namespace safety, semantic grouping |
| State | **Envelope wrapper**: meta in every response | Session context without model pollution |

**Tool Design Principles:**

1. **One tool, one purpose** — Each tool has a single responsibility and return type
2. **Namespaced families** — Related tools share prefix: `git_*`, `search_*`, `refactor_*`
3. **Session via envelope** — Every response wrapped with session/timing metadata
4. **Progress via Context** — Long operations report progress through MCP's native mechanism
5. **Pagination via response models** — Cursor-based pagination encoded in return type

### 23.2 Protocol Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MCP Clients                               │
│  (Claude, Cursor, Copilot, Continue, custom agents)             │
└─────────────────────┬───────────────────────────────────────────┘
                      │ MCP/JSON-RPC 2.0 over HTTP/SSE
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CodePlane Server                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  FastMCP Server │  │  REST Handler   │  │  SSE Handler    │  │
│  │   (~35 tools)   │  │  (/health, etc) │  │  (streaming)    │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
│           │                    │                    │           │
│           └────────────────────┼────────────────────┘           │
│                                ▼                                │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                  Response Envelope Wrapper                  ││
│  │  - Wrap all tool responses with ToolResponse[T]             ││
│  │  - Inject session_id, request_id, timestamp                 ││
│  │  - Track task state, budgets, fingerprints                  ││
│  └─────────────────────────────────────────────────────────────┘│
│                                │                                │
│           ┌────────────────────┼────────────────────┐           │
│           ▼                    ▼                    ▼           │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐      │
│  │   Index     │      │  Refactor   │      │   Mutation  │      │
│  │   Engine    │      │   Engine    │      │   Engine    │      │
│  └─────────────┘      └─────────────┘      └─────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### 23.3 Response Envelope

All tool responses are wrapped in a consistent envelope that provides session context without polluting domain models.

**Envelope schema:**

```python
@dataclass
class ResponseMeta:
    session_id: str | None
    request_id: str
    timestamp_ms: int
    task_id: str | None = None
    task_state: str | None = None  # "OPEN" | "CONVERGED" | "FAILED" | "CLOSED"

@dataclass
class ToolResponse(Generic[T]):
    result: T
    meta: ResponseMeta
```

**Wire format:**

```json
{
  "result": {
    "oid": "abc123def456",
    "message": "feat: add new feature"
  },
  "meta": {
    "session_id": "sess_a1b2c3d4e5f6",
    "request_id": "req_x9y8z7w6v5u4",
    "timestamp_ms": 1706400000000,
    "task_id": "task_p1q2r3s4t5u6",
    "task_state": "OPEN"
  }
}
```

**Implementation:**

A `@codeplane_tool` decorator wraps FastMCP's `@mcp.tool()` to inject the envelope:

```python
def codeplane_tool(mcp: FastMCP):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, ctx: Context, **kwargs):
            result = await fn(*args, ctx=ctx, **kwargs)
            return ToolResponse(
                result=result,
                meta=ResponseMeta(
                    session_id=get_session_id(ctx),
                    request_id=ctx.request_id,
                    timestamp_ms=int(time.time() * 1000),
                    task_id=get_task_id(ctx),
                    task_state=get_task_state(ctx),
                )
            )
        return mcp.tool()(wrapper)
    return decorator
```

**Session lifecycle:**

1. **Auto-creation**: Session created on first tool call from a connection
2. **Task binding**: Session creates an implicit task envelope for convergence tracking
3. **State tracking**: All operations within session share counters and fingerprints
4. **Timeout**: Idle sessions close after 30 minutes (configurable)
5. **Explicit control**: Client can create/close/switch sessions via `session_*` tools

**Explicit session override:**

Any tool can accept optional `session_id` parameter to:
- Join an existing session from another connection
- Resume a session after reconnect
- Run operations in a specific task context

### 23.4 Tool Catalog

Tools are organized into namespaced families. Each tool has a single responsibility and strongly-typed response.

#### Session Tools

| Tool | Purpose |
|------|---------|
| `session_info` | Get current session state, task context, operation history |
| `session_create` | Create new session with optional task description |
| `session_close` | Close session and finalize task |

#### Git Tools — Read Operations

| Tool | Purpose |
|------|---------|
| `git_status` | Repository status (staged, modified, untracked, conflicts) |
| `git_diff` | Generate diff between refs or working tree |
| `git_blame` | Line-by-line authorship for a file |
| `git_log` | Commit history with optional filters |
| `git_show` | Show commit details |
| `git_branches` | List branches with tracking info |
| `git_tags` | List tags |
| `git_remotes` | List configured remotes |
| `git_stash_list` | List stash entries |

#### Git Tools — Write Operations

| Tool | Purpose |
|------|---------|
| `git_stage` | Stage files for commit |
| `git_unstage` | Unstage files |
| `git_discard` | Discard working tree changes |
| `git_commit` | Create commit |
| `git_amend` | Amend previous commit |
| `git_create_branch` | Create new branch |
| `git_checkout` | Switch branches or restore files |
| `git_delete_branch` | Delete branch |
| `git_merge` | Merge branches |
| `git_cherrypick` | Cherry-pick commits |
| `git_revert` | Revert commits |
| `git_reset` | Reset HEAD to a state |
| `git_stash_push` | Stash changes |
| `git_stash_pop` | Pop stash entry |
| `git_fetch` | Fetch from remote |
| `git_push` | Push to remote |
| `git_pull` | Pull from remote |

#### Git Tools — Rebase

| Tool | Purpose |
|------|---------|
| `git_rebase_plan` | Generate rebase plan (commits to be rebased) |
| `git_rebase_execute` | Execute rebase plan |
| `git_rebase_continue` | Continue after conflict resolution |
| `git_rebase_abort` | Abort and restore original state |
| `git_rebase_skip` | Skip current commit |

#### Git Tools — Submodules

| Tool | Purpose |
|------|---------|
| `git_submodules` | List submodules with status |
| `git_submodule_init` | Initialize submodules |
| `git_submodule_update` | Update submodules to recorded commits |
| `git_submodule_add` | Add new submodule |
| `git_submodule_remove` | Remove submodule |

#### Git Tools — Worktrees

| Tool | Purpose |
|------|---------|
| `git_worktrees` | List worktrees |
| `git_worktree_add` | Add new worktree |
| `git_worktree_remove` | Remove worktree |

#### Search Tools

| Tool | Purpose |
|------|---------|
| `search` | Unified search (lexical, symbol, references, definitions) |

#### Analysis Tools

| Tool | Purpose |
|------|---------|
| `semantic_diff` | Structural change summary from index facts (added, removed, signature/body changed, renamed) with blast-radius enrichment |

#### Read Tools

| Tool | Purpose |
|------|---------|
| `read_files` | Read file contents with optional line ranges |
| `map_repo` | Repository structure and mental model |

#### Mutation Tools

| Tool | Purpose |
|------|---------|
| `write_files` | Atomic file edits |

#### Refactor Tools (Requires SCIP)

| Tool | Purpose |
|------|---------|
| `refactor_rename` | Rename symbol across codebase |
| `refactor_move` | Move symbol to different file |
| `refactor_preview` | Preview refactoring changes |
| `refactor_apply` | Apply previewed refactoring |

#### Test Tools

| Tool | Purpose |
|------|---------|
| `test_discover` | Discover tests in codebase |
| `test_run` | Execute tests with progress |

#### Status Tools

| Tool | Purpose |
|------|---------|
| `status` | Server health, index state |

**Total: ~36 tools**

### 23.5 Progress Reporting

Long-running operations report progress through MCP's native `Context.report_progress()` mechanism rather than separate streaming tool variants.

**Example:**

```python
@codeplane_tool(mcp)
async def test_run(
    targets: list[str] | None = None,
    fail_fast: bool = False,
    ctx: Context,
) -> TestSuiteResult:
    """Run tests with live progress updates."""
    tests = await discover_tests(targets)
    results = []
    
    for i, test in enumerate(tests):
        await ctx.report_progress(
            progress=i,
            total=len(tests),
            message=f"Running {test.name}",
        )
        result = await run_test(test)
        results.append(result)
        
        if fail_fast and not result.passed:
            break
    
    return TestSuiteResult(tests=results)
```

Clients receive progress events via the MCP protocol's built-in progress notification mechanism.

### 23.6 Pagination

Tools returning collections support cursor-based pagination for large result sets.

#### Request Parameters

```typescript
{
  // ... tool-specific parameters ...
  cursor?: string;  // Opaque continuation token from previous response
  limit?: number;   // Results per page (default 20, max 100)
}
```

#### Response Schema

```typescript
{
  results: Array<T>;
  pagination: {
    next_cursor?: string;      // Present if more results available
    total_estimate?: number;   // Approximate total (optional, may be expensive)
  };
  // ... other tool-specific fields ...
}
```

#### Pagination Behavior

1. **Cursor opacity** — Cursors are opaque strings; clients must not parse or construct them
2. **Cursor lifetime** — Cursors remain valid for the session lifetime or 1 hour, whichever is shorter
3. **Consistency model** — Pagination uses snapshot isolation; concurrent writes do not affect in-flight pagination
4. **Exhaustion** — When `next_cursor` is absent, all results have been returned

#### Paginated Tools

| Tool | Paginates | Notes |
|------|-----------|-------|
| `search` | Yes | All search modes |
| `map_repo` (structure) | Yes | File tree only |
| `git_log` | Yes | Commit history |
| `git_blame` | Yes | Line authorship |
| `read_files` | No | Uses explicit line ranges |
| `write_files` | No | Single operation |
| `semantic_diff` | Yes | Structural changes list |

### 23.7 Tool Specifications

The following sections define detailed parameter and response schemas for each tool. All responses are wrapped in the `ToolResponse` envelope (see 22.3).

---

#### `search`

Unified search across lexical index, symbols, and references.

**Parameters:**

```typescript
{
  query: string;                    // Search query
  mode: "lexical" | "symbol" | "references" | "definitions";
  scope?: {
    paths?: string[];               // Limit to paths (glob patterns)
    languages?: string[];           // Limit to languages
    kinds?: string[];               // Symbol kinds: function, class, variable, etc.
  };
  limit?: number;                   // Max results (default 20, max 100)
  cursor?: string;                  // Continuation token
  include_snippets?: boolean;       // Include code snippets (default true)
  session_id?: string;              // Optional session override
}
```

**Response:**

```typescript
{
  results: Array<{
    path: string;
    line: number;
    column: number;
    snippet: string;
    symbol?: {
      name: string;
      kind: string;
      container?: string;
    };
    score: number;
    match_type: "exact" | "fuzzy" | "semantic";
  }>;
  pagination: {
    next_cursor?: string;
    total_estimate?: number;
  };
  query_time_ms: number;
}
```

---

#### `map_repo`

Repository mental model — structure, languages, entry points, dependencies.

Queries the existing index to build a mental model. Does NOT scan the filesystem — reflects only what's indexed.

**Parameters:**

```typescript
{
  include?: Array<"structure" | "languages" | "entry_points" | "dependencies" | "test_layout" | "public_api">;
  depth?: number;                   // Directory depth (default 3)
  session_id?: string;
}
```

**Response:**

```typescript
// DirectoryNode structure
interface DirectoryNode {
  name: string;
  path: string;
  is_dir: boolean;
  children?: DirectoryNode[];       // Only for directories
  file_count?: number;              // Only for directories
  line_count?: number;              // Only for files (from File.line_count)
}

{
  structure: {
    root: string;
    tree: DirectoryNode[];          // Nested directory structure
    file_count: number;
    contexts: string[];             // Valid context root paths
  };
  languages: Array<{
    language: string;               // Language family from File.language_family
    file_count: number;
    percentage: number;
  }>;
  entry_points: Array<{
    path: string;
    kind: string;                   // DefFact.kind (function, class, method)
    name: string;                   // DefFact.name
    qualified_name?: string;        // DefFact.qualified_name
  }>;
  dependencies: {
    external_modules: string[];     // From ImportFact.source_literal (non-relative)
    import_count: number;
  };
  test_layout: {
    test_files: string[];           // File paths matching test patterns
    test_count: number;
  };
  public_api: Array<{
    name: string;                   // ExportEntry.exported_name
    def_uid?: string;               // Target definition UID
    certainty: string;              // CERTAIN or UNCERTAIN
    evidence?: string;              // Evidence kind (e.g., __all__literal)
  }>;
  _session: SessionState;
}
```

---

#### `read_files`

Read file contents with optional line ranges.

**Parameters:**

```typescript
{
  paths: string | string[];         // Single path or array
  ranges?: Array<{                  // Optional line ranges per file
    path: string;
    start_line: number;
    end_line: number;
  }>;
  include_metadata?: boolean;       // Include file stats (default false)
  session_id?: string;
}
```

**Response:**

```typescript
{
  files: Array<{
    path: string;
    content: string;
    language: string;
    line_count: number;
    range?: { start: number; end: number };
    metadata?: {
      size_bytes: number;
      modified_at: string;
      git_status: "clean" | "modified" | "untracked";
      hash: string;
    };
  }>;
  _session: SessionState;
}
```

---

#### `write_files`

Atomic file edits with structured delta response.

**Parameters:**

```typescript
{
  edits: Array<{
    path: string;
    action: "create" | "update" | "delete";
    content?: string;               // Full content for create/update
    patches?: Array<{               // Or line-level patches
      range: { start: number; end: number };
      replacement: string;
    }>;
  }>;
  dry_run?: boolean;                // Preview only (default false)
  session_id?: string;
}
```

**Response:**

```typescript
{
  applied: boolean;
  dry_run: boolean;
  delta: {
    mutation_id: string;
    files_changed: number;
    insertions: number;
    deletions: number;
    files: Array<{
      path: string;
      action: "created" | "updated" | "deleted";
      old_hash?: string;
      new_hash?: string;
      diff_stats: { insertions: number; deletions: number };
    }>;
  };
  affected_symbols?: string[];
  affected_tests?: string[];
  repo_fingerprint: string;
  _session: SessionState;
}
```

---

#### Git Tools (`git_*`)

Git operations are exposed as individual tools with the `git_` prefix. Each tool maps to a specific operation with strongly-typed parameters and responses.

**Tool naming convention:** `git_{operation}` (e.g., `git_status`, `git_commit`, `git_diff`)

**Common optional parameter:** All git tools accept `session_id?: string` for session override.

##### `git_status`

```typescript
// Parameters
{ paths?: string[] }

// Response
{
  branch: string | null;
  head_commit: string;
  is_clean: boolean;
  staged: Array<{ path: string; status: string; old_path?: string }>;
  modified: Array<{ path: string; status: string }>;
  untracked: string[];
  conflicts: Array<{ path: string; ancestor_oid?: string; ours_oid?: string; theirs_oid?: string }>;
  state: "none" | "merge" | "revert" | "cherrypick" | "rebase" | "bisect";
}
```

##### `git_diff`

```typescript
// Parameters
{
  base?: string;       // Commit/ref to diff against
  target?: string;     // Target ref (default: working tree)
  staged?: boolean;    // Diff staged changes
  paths?: string[];    // Scope to paths
}

// Response
{
  files: Array<{
    path: string;
    status: "added" | "modified" | "deleted" | "renamed" | "copied";
    old_path?: string;
    binary: boolean;
    hunks: Array<{
      old_start: number;
      old_lines: number;
      new_start: number;
      new_lines: number;
      header: string;
      lines: Array<{ origin: "+" | "-" | " "; content: string; old_lineno?: number; new_lineno?: number }>;
    }>;
  }>;
  stats: { files_changed: number; insertions: number; deletions: number };
}
```

##### `git_commit`

```typescript
// Parameters
{
  message: string;
  paths?: string[];    // Specific paths (default: all staged)
  author?: { name: string; email: string };
  allow_empty?: boolean;
}

// Response
{ oid: string; short_oid: string }
```

##### `git_log`

```typescript
// Parameters
{
  ref?: string;        // Starting ref (default: HEAD)
  limit?: number;      // Max commits (default: 50)
  since?: string;      // ISO date
  until?: string;      // ISO date
  paths?: string[];    // Filter to paths
  cursor?: string;     // Pagination
}

// Response
{
  commits: Array<{
    oid: string;
    short_oid: string;
    message: string;
    author: { name: string; email: string; time: string };
    parents: string[];
  }>;
  pagination: { next_cursor?: string };
}
```

##### `git_merge`

```typescript
// Parameters
{
  ref: string;         // Branch/ref to merge
  message?: string;    // Merge commit message
}

// Response
{
  success: boolean;
  fastforward: boolean;
  commit?: string;
  conflicts: Array<{ path: string; ancestor_oid?: string; ours_oid?: string; theirs_oid?: string }>;
}
```

##### `git_rebase_plan`

```typescript
// Parameters
{
  upstream: string;    // Upstream ref to rebase onto
  onto?: string;       // Optional: rebase onto different base
}

// Response
{
  upstream: string;
  onto: string;
  steps: Array<{
    action: "pick";
    commit_sha: string;
    message: string;
  }>;
}
```

##### `git_rebase_execute`

```typescript
// Parameters
{
  plan: {
    upstream: string;
    onto: string;
    steps: Array<{
      action: "pick" | "reword" | "edit" | "squash" | "fixup" | "drop";
      commit_sha: string;
      message?: string;  // For reword/squash
    }>;
  };
}

// Response
{
  success: boolean;
  completed_steps: number;
  total_steps: number;
  state: "done" | "conflict" | "edit_pause" | "aborted";
  conflict_paths?: string[];
  current_commit?: string;
  new_head?: string;
}
```

##### Other Git Tools

The remaining git tools follow similar patterns:

| Tool | Key Parameters | Response Summary |
|------|---------------|------------------|
| `git_blame` | `path`, `line_range?` | Line authorship with commit info |
| `git_show` | `ref` | Commit details with diff |
| `git_branches` | - | List of branches with tracking |
| `git_tags` | - | List of tags |
| `git_remotes` | - | List of remotes with URLs |
| `git_stage` | `paths` | Staging result |
| `git_unstage` | `paths` | Unstaging result |
| `git_discard` | `paths` | Discard result |
| `git_amend` | `message?` | Amended commit OID |
| `git_create_branch` | `name`, `ref?` | New branch info |
| `git_checkout` | `ref`, `create?` | Checkout result |
| `git_delete_branch` | `name`, `force?` | Deletion result |
| `git_reset` | `ref`, `mode` | Reset result |
| `git_cherrypick` | `commit` | Cherry-pick result |
| `git_revert` | `commit` | Revert result |
| `git_stash_push` | `message?`, `include_untracked?` | Stash commit |
| `git_stash_pop` | `index?` | Pop result |
| `git_stash_list` | - | List of stash entries |
| `git_fetch` | `remote?`, `prune?` | Updated refs |
| `git_push` | `remote?`, `force?` | Push result |
| `git_pull` | `remote?` | Pull result |
| `git_rebase_continue` | - | Rebase state |
| `git_rebase_abort` | - | Abort confirmation |
| `git_rebase_skip` | - | Skip result |
| `git_submodules` | - | List with status |
| `git_submodule_init` | `paths?` | Init result |
| `git_submodule_update` | `paths?`, `recursive?` | Update result |
| `git_submodule_add` | `url`, `path`, `branch?` | New submodule info |
| `git_submodule_remove` | `path` | Removal result |
| `git_worktrees` | - | List of worktrees |
| `git_worktree_add` | `path`, `ref` | New worktree info |
| `git_worktree_remove` | `name`, `force?` | Removal result |

---

#### Refactor Tools (`refactor_*`)

Semantic refactoring via SCIP index.

**Parameters:**

```typescript
{
  action: "rename" | "move" | "delete" | "preview" | "apply" | "cancel";
  
  // For rename
  symbol?: string;                  // Symbol name or path:line:col
  new_name?: string;
  
  // For move
  from_path?: string;
  to_path?: string;
  
  // For delete
  target?: string;                  // Symbol or path
  
  // For apply/cancel
  refactor_id?: string;             // From preview response
  
  // Options
  include_comments?: boolean;       // Sweep comments/docs (default true)
  contexts?: string[];              // Specific contexts (default all)
  session_id?: string;
}
```

**Response:**

```typescript
{
  refactor_id: string;
  status: "previewed" | "applied" | "cancelled" | "divergence";
  preview?: {
    files_affected: number;
    edits: Array<{
      path: string;
      hunks: Array<{ old: string; new: string; line: number }>;
      semantic: boolean;            // SCIP-based vs comment sweep
    }>;
    contexts_used: string[];
  };
  applied?: {
    delta: MutationDelta;           // Same as write_files
    validation?: {
      diagnostics_before: number;
      diagnostics_after: number;
    };
  };
  divergence?: {
    conflicting_hunks: Array<{
      path: string;
      contexts: string[];
      hunks: Array<{ context: string; content: string }>;
    }>;
    resolution_options: string[];
  };
  _session: SessionState;
}
```

---

#### Test Tools (`test_*`)

---

#### `semantic_diff`

Structural change summary from index facts. Compares definitions between two states and reports what changed structurally with blast-radius enrichment.

**Modes:**
- **Git mode** (default): `base`/`target` are git refs (commit, branch, tag)
- **Epoch mode**: `base="epoch:N"`, `target="epoch:M"`

**Parameters:**

```typescript
{
  base?: string;           // Default "HEAD". Git ref or "epoch:N"
  target?: string | null;  // Default null (working tree). Git ref or "epoch:M"
  paths?: string[] | null; // Limit to specific file paths
  cursor?: string | null;  // Pagination cursor from previous response
}
```

**Response:**

```typescript
{
  summary: string;                    // e.g. "5 added, 2 signature changed"
  breaking_summary: string | null;    // e.g. "3 breaking changes: foo, bar, baz"
  files_analyzed: number;
  base: string;                       // Resolved base description
  target: string;                     // Resolved target description
  structural_changes: StructuralChange[];
  non_structural_changes: FileChangeInfo[];  // Files without grammar support
  agentic_hint: string;               // Priority-ordered action list (computed from ALL changes)
  pagination: {                       // Pagination metadata
    next_cursor?: string;             // Cursor for next page (absent if last page)
    total_estimate?: number;          // Total structural changes count
  };
}
```

**FileChangeInfo:**

```typescript
{
  path: string;
  status: string;                     // "added", "modified", "deleted", "renamed"
  category: string;                   // "prod", "test", "build", "config", "docs"
  language?: string;                  // Detected language family
}
```

**StructuralChange:**

```typescript
{
  path: string;
  kind: string;                       // "function", "class", "variable", etc.
  name: string;
  qualified_name?: string;            // Dot-separated path (e.g., "MyClass.method")
  change: "added" | "removed" | "signature_changed" | "body_changed" | "renamed";
  structural_severity: "breaking" | "non_breaking";
  behavior_change_risk: "low" | "medium" | "high" | "unknown";
  entity_id?: string;                 // Stable def_uid from index
  old_signature?: string;
  new_signature?: string;
  old_name?: string;                  // For renames
  start_line?: number;
  start_col?: number;
  end_line?: number;
  end_col?: number;
  lines_changed?: number;             // Count of changed lines in entity span
  delta_tags?: string[];              // e.g. ["parameters_changed", "minor_change"]
  change_preview?: string;            // First N changed lines within span
  impact?: ImpactInfo;
  nested_changes?: StructuralChange[]; // Methods nested under their class
}
```

**Delta Tag Taxonomy:**
- `symbol_added`, `symbol_removed`, `symbol_renamed`
- `parameters_changed`, `return_type_changed`, `signature_changed`
- `minor_change` (≤3 lines), `body_logic_changed`, `major_change` (>20 lines)

**ImpactInfo (blast-radius enrichment):**

```typescript
{
  reference_count?: number;           // Total RefFact-based cross-reference count
  ref_tiers?: {                       // Reference counts by resolution tier
    proven: number;                   // Same-file lexical bind, certain
    strong: number;                   // Cross-file with explicit import trace
    anchored: number;                 // Ambiguous but grouped in anchor group
    unknown: number;                  // Cannot classify
  };
  reference_basis: string;            // "ref_facts_resolved" | "ref_facts_partial" | "unknown"
  referencing_files?: string[];       // Files containing references
  importing_files?: string[];         // Files importing this symbol
  affected_test_files?: string[];     // Test files that may need updating
  confidence: "high" | "low";
  visibility?: string;                // "public" | "private" | "protected" | "internal"
  is_static?: boolean;
}
```

**Identity & Classification:**
- Identity key: `(kind, lexical_path)` for cross-state symbol correspondence
- Rename detection: same kind + same `signature_hash` across added/removed sets
- Enrichment is fail-open: each annotation (refs, imports, tests) independently wrapped

**Agentic hint priority:**
1. Signature changes with references (callers may need updating, includes tier breakdown)
2. Removed symbols (broken references)
3. Body changes with behavior risk assessment (review for correctness)
4. Affected test files (re-run)

---

#### Test Tools (`test_*`)

Test discovery and execution.

**Parameters:**

```typescript
{
  action: "discover" | "run" | "status" | "cancel";
  
  // For discover
  paths?: string[];                 // Scope discovery
  
  // For run
  targets?: string[];               // Specific targets (default all)
  filter?: {
    pattern?: string;               // Test name pattern
    tags?: string[];                // Test tags/markers
    failed_only?: boolean;          // Re-run failures
  };
  parallelism?: number;             // Worker count (default auto)
  timeout_sec?: number;             // Per-target timeout
  fail_fast?: boolean;              // Stop on first failure
  
  // For status/cancel
  run_id?: string;
  
  session_id?: string;
}
```

**Response:**

```typescript
{
  action: "discover" | "run" | "status" | "cancel";
  
  // discover
  targets?: Array<{
    target_id: string;
    path: string;
    language: string;
    runner: string;
    estimated_cost: number;
    test_count?: number;
  }>;
  
  // run / status
  run_id?: string;
  status?: "running" | "completed" | "cancelled" | "failed";
  progress?: {
    total: number;
    completed: number;
    passed: number;
    failed: number;
    skipped: number;
  };
  results?: Array<{
    target_id: string;
    status: "passed" | "failed" | "skipped" | "error";
    duration_ms: number;
    failure?: {
      message: string;
      stack?: string;
      output?: string;
    };
  }>;
  summary?: {
    total_duration_ms: number;
    flaky: string[];
    new_failures: string[];
    fixed: string[];
  };
  
  _session: SessionState;
}
```

---

#### Session Tools (`session_*`)

Session and task lifecycle management.

**Parameters:**

```typescript
{
  action: "status" | "new" | "close" | "configure";
  
  // For new
  limits?: {
    max_mutations?: number;
    max_test_runs?: number;
    max_duration_sec?: number;
  };
  
  // For close
  reason?: "success" | "failed" | "abandoned";
  
  // For configure (update limits mid-task)
  limits_delta?: {
    add_mutations?: number;
    add_test_runs?: number;
  };
  
  session_id?: string;
}
```

**Response:**

```typescript
{
  action: "status" | "new" | "close" | "configure";
  task: {
    task_id: string;
    state: "OPEN" | "CLOSED_SUCCESS" | "CLOSED_FAILED" | "CLOSED_INTERRUPTED";
    limits: {
      max_mutations: number;
      max_test_runs: number;
      max_duration_sec: number;
    };
    counters: {
      mutations: number;
      test_runs: number;
      elapsed_sec: number;
    };
    fingerprints: {
      last_mutation: string | null;
      last_failure: string | null;
      repeated_failure_count: number;
    };
    timeline: Array<{
      timestamp: string;
      op_type: string;
      success: boolean;
      fingerprint?: string;
    }>;
  };
  _session: SessionState;
}
```

---

#### `status`

Server health, index state, and session info.

**Parameters:**

```typescript
{
  include?: Array<"daemon" | "index" | "session" | "indexers" | "config">;
  session_id?: string;
}
```

**Response:**

```typescript
{
  daemon: {
    version: string;
    uptime_sec: number;
    pid: number;
    port: number;
    memory_mb: number;
  };
  index: {
    version: number;
    commit: string;
    file_count: number;
    symbol_count: number;
    last_updated: string;
    overlay_files: number;
    healthy: boolean;
  };
  session: SessionState;
  indexers: {
    languages: Array<{
      language: string;
      indexer: string;
      status: "ready" | "running" | "not_installed";
      memory_mb?: number;
    }>;
    pending_install: string[];
  };
  config: {
    repo_root: string;
    config_sources: string[];
    active_contexts: string[];
  };
  _session: SessionState;
}
```

---

### 23.8 REST Endpoints (Operator)

Non-MCP endpoints for operators and monitoring.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness check (returns 200 if alive) |
| `/ready` | GET | Readiness check (returns 200 if index loaded) |
| `/metrics` | GET | Prometheus-format metrics (see section 13) |
| `/status` | GET | JSON status (same as `status` tool) |
| `/dashboard` | GET | Observability dashboard (see section 13) |

**Response header:** All responses include `X-CodePlane-Repo` header with the server's repository path.

**Example:**

```bash
curl http://127.0.0.1:$(cat .codeplane/port)/health
# Response includes: X-CodePlane-Repo: /path/to/repo
```

---

### 23.9 Error Handling

All MCP tools use the error schema defined in section 4.2.

**MCP-specific error wrapping:**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "CodePlane error",
    "data": {
      "code": 4001,
      "error": "REFACTOR_DIVERGENCE",
      "message": "Contexts disagree on rename target",
      "retryable": false,
      "details": { ... },
      "_session": { ... }
    }
  }
}
```

**Budget exceeded handling:**

When task budget is exceeded, all subsequent mutating operations return:

```json
{
  "code": 6001,
  "error": "TASK_BUDGET_EXCEEDED",
  "message": "Mutation budget exceeded (20/20)",
  "retryable": false,
  "details": {
    "budget_type": "mutations",
    "limit": 20,
    "current": 20
  },
  "_session": { ... }
}
```

Client must close task and open new one, or configure additional budget.

---

### 23.10 MCP Server Configuration

CodePlane registers as an MCP server. Client configuration example:

**Claude Desktop / Cursor:**

```json
{
  "mcpServers": {
    "codeplane": {
      "transport": "http",
      "url": "http://127.0.0.1:${port}"
    }
  }
}
```

**Response header:** All responses include `X-CodePlane-Repo` header with the server's repository path. Clients can use this to verify they're talking to the correct server.

**Dynamic discovery:**

Clients read `.codeplane/port` for the port number.

---

### 23.11 Versioning

- API version included in `/status` response
- Breaking changes increment major version
- Tools may gain optional parameters without version bump
- Deprecated tools return warning in `meta.warnings`

Current version: `1.0.0`
