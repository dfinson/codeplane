# Index Module — Design Spec (v8.7)

## Table of Contents

- [Scope](#scope)
  - [Responsibilities](#responsibilities)
  - [From SPEC.md](#from-specmd)
- [Architecture](#architecture)
- [File Plan](#file-plan)
- [File State Model (Two Axes)](#file-state-model-two-axes)
  - [Freshness Axis](#freshness-axis-index-currency)
  - [Certainty Axis](#certainty-axis-semantic-confidence)
  - [Combined State → Mutation Behavior](#combined-state--mutation-behavior)
  - [State Computation](#state-computation)
- [Refresh Job Worker](#refresh-job-worker)
  - [Commit Order (Critical)](#commit-order-critical)
  - [Critical Invariant: Fresh HEAD at Import Time](#critical-invariant-fresh-head-at-import-time)
- [Scoped Refresh (Monotonic Lattice)](#scoped-refresh-monotonic-lattice)
  - [RefreshScope](#refreshscope)
  - [Scope Lattice](#scope-lattice)
  - [Critical Invariant: Monotonic Widening](#critical-invariant-monotonic-widening)
- [SQLite Schema](#sqlite-schema)
- [Key Interfaces](#key-interfaces)
- [Correctness Invariants](#correctness-invariants)
- [Dependencies](#dependencies)

---

## Scope

The index module builds and queries the hybrid two-layer index: syntactic (always-on) and semantic (SCIP batch jobs).

### Responsibilities

- Lexical index via Tantivy (paths, identifiers, content)
- Structural metadata in SQLite (symbols, relations, files, contexts)
- Symbol graph construction and traversal
- File state tracking (freshness + certainty axes)
- Refresh job management with HEAD-aware deduplication
- Scoped refresh with monotonic lattice
- Reconciliation with filesystem (change detection)
- Repo map generation

### From SPEC.md

- §5: Repository reconciliation
- §7: Indexing & retrieval architecture
- §7.2: Lexical index (Tantivy)
- §7.3: Structural metadata (SQLite)
- §7.5: Semantic Layer (SCIP Batch Indexers)
- §7.6: Graph index
- §7.8: Atomic update protocol
- §8.4: Context Discovery & Membership (authoritative source for language families, ownership model, discovery phases, membership rules, and ContextRouter)

---

## Architecture

    ┌─────────────────────────────────────────────────────────────────┐
    │                        SQLite (facts store)                     │
    │   files, symbols, occurrences, edges, exports, contexts, jobs   │
    │   file_semantic_facts, symbol_interface_hashes, repo_state      │
    │   decision_cache                                                │
    └─────────────────────────────────────────────────────────────────┘
                    ▲                               ▲
                    │                               │
    ┌───────────────┴───────────────┐ ┌─────────────┴─────────────────┐
    │     SYNTACTIC LAYER           │ │      SEMANTIC LAYER           │
    │     (always-on)               │ │      (one-shot indexer jobs)  │
    ├───────────────────────────────┤ ├───────────────────────────────┤
    │ Tree-sitter parsing           │ │ SCIP indexers (batch):        │
    │ Import graph heuristics       │ │   scip-go, scip-typescript,   │
    │ Local symbol extraction       │ │   scip-java, scip-dotnet,     │
    │ Tantivy full-text search      │ │   scip-python, scip-clang     │
    │ Syntactic interface hashing   │ │   rust-analyzer scip          │
    │ Syntactic slicing (def-use)   │ │                               │
    ├───────────────────────────────┤ ├───────────────────────────────┤
    │ Confidence: syntactic         │ │ Confidence: semantic          │
    │ Mutation: local scope only    │ │ Mutation: cross-file allowed  │
    │ Latency: <100ms               │ │ Latency: job queue (async)    │
    └───────────────────────────────┘ └───────────────────────────────┘

    PROHIBITED: Live LSP queries, persistent language servers

---

## File Plan

    index/
    ├── __init__.py
    ├── lexical.py       # Tantivy wrapper
    ├── structural.py    # SQLite metadata (symbols, relations, files)
    ├── graph.py         # Symbol graph traversal
    ├── reconcile.py     # Change detection, Git blob hash comparison
    ├── coordinator.py   # High-level search, reindex orchestration
    ├── refresh.py       # Refresh job worker, scoped refresh
    ├── state.py         # File state computation (freshness + certainty)
    └── schema.sql       # SQLite schema

---

## File State Model (Two Axes)

### Freshness Axis (Index Currency)

| State | Meaning | Refresh Behavior |
|-------|---------|------------------|
| CLEAN | Semantic data matches content, deps confirmed | No action |
| DIRTY | Content changed, refresh enqueued | Re-index |
| STALE | Dependency interface confirmed changed | Re-index |
| PENDING_CHECK | Dependency dirty, interface change unknown | Wait |

### Certainty Axis (Semantic Confidence)

| State | Meaning |
|-------|---------|
| CERTAIN | Semantic identity proven, no ambiguity |
| AMBIGUOUS | Index fresh but language semantics uncertain |

### Combined State → Mutation Behavior

| Freshness | Certainty | Behavior |
|-----------|-----------|----------|
| CLEAN | CERTAIN | Automatic semantic edits |
| CLEAN | AMBIGUOUS | Return needs_decision |
| DIRTY/STALE/PENDING_CHECK | * | Block with witness packet |

### State Computation

    @dataclass
    class FileState:
        freshness: str  # clean, dirty, stale, pending_check, unindexed
        certainty: str  # certain, ambiguous, unknown

    def get_file_state(file_id: int, context_id: int, memo: dict = None) -> FileState:
        """
        Compute file state with per-request memoization.
        Handles dependency cycles gracefully.
        """
        if memo is None:
            memo = {}
        
        key = (file_id, context_id)
        if key in memo:
            return memo[key]
        
        facts = file_semantic_facts.get(file_id, context_id)
        file = files.get(file_id)
        
        if not facts:
            return FileState("unindexed", "unknown")
        
        if facts.content_hash_at_index != file.content_hash:
            return FileState("dirty", "unknown")
        
        # Check dependencies
        freshness = "clean"
        for dep_id in get_dependency_file_ids(file_id, context_id):
            dep_state = get_file_state(dep_id, context_id, memo)
            if dep_state.freshness == "stale":
                freshness = "stale"
                break
            elif dep_state.freshness == "dirty" and freshness == "clean":
                freshness = "pending_check"
        
        # Check certainty from ambiguity flags
        certainty = "certain"
        if facts.ambiguity_flags:
            flags = json.loads(facts.ambiguity_flags)
            if flags:
                certainty = "ambiguous"
        
        result = FileState(freshness, certainty)
        memo[key] = result
        return result

---

## Refresh Job Worker

### Commit Order (Critical)

    1. Claim job (queued → running, atomic WHERE clause)
    2. Run SCIP indexer (may take seconds to minutes)
    3. Fresh HEAD read + supersede check (MUST be fresh, not cached)
    4. Import output into semantic index (transactional)
    5. Mark completed AFTER import succeeds

### Critical Invariant: Fresh HEAD at Import Time

    def run_refresh_job(job_id: int):
        # Step 1: Claim
        rows = refresh_jobs.update(status='running').where(id=job_id, status='queued').execute()
        if rows == 0:
            return  # Already claimed
        
        job = refresh_jobs.get(job_id)
        
        # Step 2: Run indexer
        scope = RefreshScope.from_json(job.scope) if job.scope else None
        output_path = run_indexer(job.context_id, scope)
        
        # Step 3: CRITICAL - Fresh HEAD read
        job.refresh()  # Reload from DB
        current_head = git_rev_parse_head()  # FRESH read
        
        if job.status == 'superseded':
            cleanup_output(output_path)
            return
        
        if job.head_at_enqueue != current_head:
            # HEAD changed during indexing - output is stale
            refresh_jobs.update(status='superseded').where(id=job_id).execute()
            cleanup_output(output_path)
            return
        
        # Step 4: Import (transactional)
        with db.transaction():
            import_scip_output(output_path, job.context_id)
        
        # Step 5: Mark completed
        refresh_jobs.update(status='completed').where(id=job_id, status='running').execute()

---

## Scoped Refresh (Monotonic Lattice)

### RefreshScope

    @dataclass
    class RefreshScope:
        files: list[str] | None = None       # Specific files
        packages: list[str] | None = None    # Specific packages
        changed_since: float | None = None   # Files changed after timestamp
        
        # None means full refresh (broadest)

### Scope Lattice

    Narrowest                                          Broadest
       │                                                   │
       ▼                                                   ▼
    files([a.py]) ⊂ files([a.py, b.py]) ⊂ packages([pkg]) ⊂ FULL (None)

    changed_since(T1) ⊂ changed_since(T0)  where T0 < T1

### Critical Invariant: Monotonic Widening

For the same (context_id, head):
- **Never supersede** existing queued/running job
- **Merge scope (widen only)**
- Only supersede on HEAD change

    def enqueue_refresh_if_needed(context_id: int, scope: RefreshScope | None) -> int | None:
        current_head = git_rev_parse_head()
        
        existing = refresh_jobs.where(
            context_id=context_id,
            status__in=['queued', 'running']
        ).first()
        
        if existing is None:
            return create_job(context_id, scope, current_head)
        
        if existing.head_at_enqueue != current_head:
            # HEAD changed - supersede
            existing.update(status='superseded')
            return create_job(context_id, scope, current_head)
        
        # Same HEAD - merge scopes (widen only)
        merged = merge_scopes(existing.scope, scope)
        
        if merged == existing.scope:
            return None  # Already covered
        
        if existing.status == 'queued':
            existing.update(scope=merged.to_json())
        
        return existing.id

    def merge_scopes(a: RefreshScope | None, b: RefreshScope | None) -> RefreshScope | None:
        """Return union (widest) of two scopes. None = full refresh."""
        if a is None or b is None:
            return None
        
        return RefreshScope(
            files=sorted(set(a.files or []) | set(b.files or [])) or None,
            packages=sorted(set(a.packages or []) | set(b.packages or [])) or None,
            changed_since=min(a.changed_since, b.changed_since) if a.changed_since and b.changed_since else (a.changed_since or b.changed_since)
        )

---

## SQLite Schema

    -- Core file tracking
    CREATE TABLE files (
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE NOT NULL,
        language TEXT,
        content_hash TEXT NOT NULL,
        syntactic_interface_hash TEXT,
        indexed_at REAL
    );

    -- Semantic facts per (file, context)
    CREATE TABLE file_semantic_facts (
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts(id) ON DELETE CASCADE,
        semantic_interface_hash TEXT,
        content_hash_at_index TEXT,
        ambiguity_flags TEXT,  -- JSON: detected ambiguity types
        refreshed_at REAL,
        PRIMARY KEY (file_id, context_id)
    );

    -- Semantic contexts
    CREATE TABLE contexts (
        id INTEGER PRIMARY KEY,
        name TEXT,
        language TEXT,
        config_hash TEXT,
        tool_version TEXT,
        enabled BOOLEAN DEFAULT TRUE,
        refreshed_at REAL
    );

    -- Refresh job queue
    CREATE TABLE refresh_jobs (
        id INTEGER PRIMARY KEY,
        context_id INTEGER REFERENCES contexts,
        status TEXT NOT NULL,  -- queued, running, completed, superseded, failed
        scope TEXT,  -- JSON: RefreshScope
        trigger_reason TEXT,
        head_at_enqueue TEXT NOT NULL,
        created_at REAL,
        started_at REAL,
        finished_at REAL,
        superseded_reason TEXT,
        error TEXT
    );

    -- Repository state tracking
    CREATE TABLE repo_state (
        id INTEGER PRIMARY KEY DEFAULT 1,
        last_seen_head TEXT,
        last_seen_index_mtime REAL,
        checked_at REAL
    );

    -- Symbol definitions
    CREATE TABLE symbols (
        id INTEGER PRIMARY KEY,
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts,
        name TEXT NOT NULL,
        qualified_name TEXT,
        kind TEXT NOT NULL,
        line INTEGER NOT NULL,
        column INTEGER,
        signature TEXT,
        layer TEXT NOT NULL  -- 'syntactic' or 'semantic'
    );

    -- Symbol occurrences (references)
    CREATE TABLE occurrences (
        id INTEGER PRIMARY KEY,
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        symbol_id INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts,
        start_line INTEGER,
        start_col INTEGER,
        end_line INTEGER,
        end_col INTEGER,
        role TEXT,  -- definition, reference, import
        layer TEXT NOT NULL,
        anchor_before TEXT,  -- For verification
        anchor_after TEXT
    );

    -- File dependency edges
    CREATE TABLE edges (
        id INTEGER PRIMARY KEY,
        source_file INTEGER REFERENCES files(id) ON DELETE CASCADE,
        target_file INTEGER REFERENCES files(id) ON DELETE CASCADE,
        dependency_type TEXT NOT NULL,
        context_id INTEGER REFERENCES contexts,
        layer TEXT NOT NULL
    );

    -- Decision cache for ambiguity replay
    CREATE TABLE decision_cache (
        id INTEGER PRIMARY KEY,
        ambiguity_signature TEXT NOT NULL,
        repo_head TEXT NOT NULL,
        file_hashes TEXT NOT NULL,  -- JSON: {path: hash}
        decision TEXT NOT NULL,     -- JSON: selected candidates
        proof_payload TEXT NOT NULL,
        created_at REAL,
        UNIQUE(ambiguity_signature, repo_head)
    );

---

## Key Interfaces

    # coordinator.py
    class IndexCoordinator:
        async def search(self, query: str, mode: SearchMode, scope: Scope) -> list[SearchResult]
        async def get_symbol(self, name: str, path: str | None) -> Symbol | None
        async def get_references(self, symbol: Symbol, context_id: int) -> list[Reference]
        async def get_map(self, include: list[str]) -> RepoMap
        async def reindex_incremental(self, changed_paths: list[Path]) -> IndexStats
        async def reindex_full(self) -> IndexStats

    # state.py
    class FileStateService:
        def get_file_state(self, file_id: int, context_id: int) -> FileState
        def get_file_states_batch(self, file_ids: list[int], context_id: int) -> dict[int, FileState]
        def check_mutation_gate(self, file_ids: list[int], context_id: int) -> MutationGateResult

    # refresh.py
    class RefreshJobService:
        def enqueue_refresh(self, context_id: int, scope: RefreshScope | None) -> int | None
        def run_job(self, job_id: int) -> None
        def get_job_status(self, job_id: int) -> RefreshJobStatus

---

## Correctness Invariants

| Invariant | Failure Mode | Fix |
|-----------|--------------|-----|
| Fresh HEAD at import | Stale SCIP data imported | Re-read HEAD immediately before import |
| Monotonic scope | Lost coverage, thrashing | Only widen for same HEAD, supersede only on HEAD change |
| Atomic job transitions | Race conditions | Use WHERE clause in UPDATE |
| Import before completed | Completed jobs with no data | Never mark completed until import succeeds |

---

## Dependencies

- tantivy — Tantivy Python bindings
- tree-sitter — Parsing for symbol extraction
- tree-sitter-languages — Grammar bundles
- Standard library sqlite3
