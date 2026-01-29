# CodePlane Semantic Index Architecture (v8.7)

## Table of Contents

- [Design Principles](#design-principles)
- [Layer Architecture](#layer-architecture)
- [File Scope (What Gets Indexed)](#file-scope-what-gets-indexed)
  - [Inclusion Rules](#inclusion-rules)
  - [Symlink Policy](#symlink-policy)
  - [Indexable Files Cache](#indexable-files-cache)
- [File Change Detection Architecture](#file-change-detection-architecture)
- [Git HEAD Tripwire](#git-head-tripwire)
- [Reconciliation Flow](#reconciliation-flow)
  - [DIRTY Triggers Refresh](#dirty-triggers-refresh)
  - [New File Refresh](#new-file-refresh-with-churn-guard)
  - [Refresh Enqueue (Monotonic Scope Lattice)](#refresh-enqueue-monotonic-scope-lattice)
  - [Refresh Worker (Fresh HEAD at Import Time)](#refresh-worker-fresh-head-at-import-time)
  - [Bulk Reconcile (Optimized)](#bulk-reconcile-optimized)
- [SQLite Schema](#sqlite-schema)
- [File State Model](#file-state-model)
  - [Freshness Axis](#freshness-axis)
  - [Certainty Axis](#certainty-axis-orthogonal-to-freshness)
  - [Combined State Matrix](#combined-state-matrix)
  - [State Computation](#state-computation-per-request-memoization)
- [Witness Packet Schema](#witness-packet-schema)
- [Decision Capsules (Micro-Queries)](#decision-capsules-micro-queries)
- [Syntactic Slice Engine](#syntactic-slice-engine)
- [Mutation Gate](#mutation-gate)
- [Two-Phase Rename](#two-phase-rename)
- [Verification Hooks](#verification-hooks)
- [Agent-Friendly Diff Proposals](#agent-friendly-diff-proposals)
- [Ambiguity Signature Cache](#ambiguity-signature-cache)
- [Daemon Startup](#daemon-startup)
- [Summary](#summary)

---

## Design Principles

1. **Two layers, explicit contracts** - Syntactic layer is always-on. Semantic layer is produced by one-shot indexer jobs.
2. **Never guess** - If we can't prove it, we refuse, block, or return a decision problem. Heuristics never drive mutation identity.
3. **Edits require semantic identity** - Cross-file mutations require fresh semantic index for target context.
4. **Always respond** - Every endpoint returns a valid outcome: edits, blocked, needs_decision, refused, or unsupported.
5. **No persistent semantic engines** - Semantic layer comes from batch indexer jobs only, never live LSPs.
6. **Stale reads re-align** - Navigation returns fresh coordinates via syntactic re-alignment.
7. **Dirty ≠ Stale ≠ Ambiguous** - Distinguish "needs re-index" from "definitely invalid" from "semantically uncertain."
8. **Escape hatches exist** - Agents can force syntactic fallback when blocking is unacceptable.
9. **Watchers hint, hashes decide** - File events are hints; content hashing is the source of truth.
10. **DIRTY triggers refresh** - Any file becoming DIRTY enqueues refresh; PENDING_CHECK follows naturally.
11. **Leverage agents for decisions** - When ambiguity is enumerable, return a bounded decision problem the agent can solve.

---

## Layer Architecture

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
    │ Tree-sitter parsing           │ │ Indexer jobs (batch, no LSP): │
    │ Import graph heuristics       │ │   scip-go, scip-typescript,   │
    │ Local symbol extraction       │ │   scip-java, scip-dotnet,     │
    │ Tantivy full-text search      │ │   scip-python, scip-clang     │
    │ Syntactic interface hashing   │ │   rust-analyzer scip (batch)  │
    │ Syntactic slicing (def-use)   │ │                               │
    ├───────────────────────────────┤ ├───────────────────────────────┤
    │ Confidence: syntactic         │ │ Confidence: semantic          │
    │ Mutation: local scope only    │ │ Mutation: cross-file allowed  │
    │ Latency: <100ms               │ │ Latency: job queue (async)    │
    └───────────────────────────────┘ └───────────────────────────────┘

    PROHIBITED: Live LSP queries, LSP-to-index adapters, persistent language servers.

---

## File Scope (What Gets Indexed)

### Inclusion Rules

    Both syntactic and semantic layers operate on:
        Git-tracked files
        + CPL-tracked files (git-ignored but cpl-whitelisted via !pattern in .cplignore)
    
    Both layers skip:
        CPL-ignored files (regardless of git status)

### Symlink Policy

    POLICY SUMMARY:
    - We do not traverse into symlinked directories
    - Symlink files are validated: target must resolve inside repo
    - If valid, we index the TARGET content (transparently via normal file reads)
    
    HARD RULES:
    
    1. All directory walking MUST use followlinks=False
       - os.walk(..., followlinks=False)
       - pathlib with follow_symlinks=False
       - Symlinked directories are opaque and never recursed into
       - This is a SECURITY requirement
    
    2. Symlink files are validated via realpath()
       - Target must be inside repo root
       - If outside: reject (security)
       - If inside: allow (read follows symlink transparently)
    
    3. Symlinked directories are never indexed
       - They appear as entries but we don't descend
       - is_safe_path() returns False for symlink-to-directory
    
    def is_safe_path(path: str) -> bool:
        """
        Validate path is safe to index.
        """
        try:
            resolved = os.path.realpath(path)
            
            # Must resolve inside repo
            if not resolved.startswith(repo_root + os.sep):
                return False
            
            # Symlinked directories are rejected (would require traversal)
            if os.path.islink(path) and os.path.isdir(resolved):
                return False
            
            return True
        except OSError:
            return False
    
    def cpl_overlay_files() -> List[str]:
        """
        Enumerate CPL-tracked overlay files.
        MUST NOT follow symlinks during directory traversal.
        """
        result = []
        for root, dirs, files in os.walk(repo_root, followlinks=False):
            # Skip .git and other excluded directories
            dirs[:] = [d for d in dirs if not should_skip_dir(d)]
            
            for f in files:
                path = os.path.join(root, f)
                if is_cpl_overlay(path):
                    result.append(path)
        
        return result

### Indexable Files Cache

    _indexable_files_cache = {
        "files": set(),
        "last_updated": 0,
        "head_at_update": None
    }
    
    def is_indexable(path: str) -> bool:
        """CHEAP membership test for hot path. Never forces refresh."""
        return path in _indexable_files_cache["files"]
    
    def get_indexable_files(force_refresh=False) -> Set[str]:
        """Full refresh only on HEAD change / startup."""
        if not force_refresh and _indexable_files_cache["files"]:
            return _indexable_files_cache["files"]
        
        result = set()
        
        for path in git_ls_files():
            if not cpl_ignored(path) and is_safe_path(path):
                result.add(path)
        
        for path in cpl_overlay_files():
            if not cpl_ignored(path) and is_safe_path(path):
                result.add(path)
        
        _indexable_files_cache["files"] = result
        _indexable_files_cache["last_updated"] = now()
        _indexable_files_cache["head_at_update"] = git_rev_parse_head()
        
        return result
    
    def update_indexable_cache_incrementally(event_type: str, path: str):
        """Called by watcher for file create/delete/rename."""
        if event_type == "created":
            if not cpl_ignored(path) and is_safe_path(path):
                _indexable_files_cache["files"].add(path)
        elif event_type == "deleted":
            _indexable_files_cache["files"].discard(path)

---

## File Change Detection Architecture

    ┌─────────────────┐     paths      ┌─────────────────┐
    │  File Watcher   │ ──────────────▶│  Debounce Queue │
    │  (watchdog)     │                │  (200-500ms)    │
    └─────────────────┘                └────────┬────────┘
                                                │
    ┌─────────────────┐                         ▼
    │  Safety Net     │ paths     ┌─────────────────────┐
    │  Polling        │ ─────────▶│    Reconciler       │
    │  (30-120s)      │           │  (hash = truth)     │
    └─────────────────┘           └────────┬────────────┘
                                           │
    ┌─────────────────┐                     ▼
    │  HEAD Tripwire  │ ────────▶ bulk_reconcile_indexed_files()
    │  (5-10s timer)  │           + get_indexable_files(force_refresh=True)
    └─────────────────┘

---

## Git HEAD Tripwire

    def check_head_tripwire():
        current_head = git_rev_parse_head()
        stored = repo_state.get(1)
        
        if stored is None or stored.last_seen_head != current_head:
            log.info("head_changed", old=stored.last_seen_head, new=current_head)
            get_indexable_files(force_refresh=True)
            bulk_reconcile_indexed_files()
            repo_state.upsert(
                id=1,
                last_seen_head=current_head,
                last_seen_index_mtime=stat('.git/index').mtime,
                checked_at=now()
            )

---

## Reconciliation Flow

    def handle_change(path: str):
        if not is_indexable(path):
            return
        
        current_hash = compute_content_hash(path)
        file = files.get_by_path(path)
        
        if file and file.content_hash == current_hash:
            return
        
        if file:
            file.update(
                content_hash=current_hash,
                syntactic_interface_hash=compute_syntactic_interface_hash(path)
            )
            mark_dirty_and_enqueue_refresh(file)
            mark_importers_pending(file.id)
            reparse_syntactic(file)
            tantivy.update_document(path, read_file(path))
        else:
            file = files.create(
                path=path,
                language=detect_language(path),
                content_hash=current_hash,
                syntactic_interface_hash=compute_syntactic_interface_hash(path)
            )
            enqueue_refresh_for_new_file(file)
            parse_syntactic(file)
            tantivy.add_document(path, read_file(path))

### DIRTY Triggers Refresh

    def mark_dirty_and_enqueue_refresh(file):
        contexts_with_facts = file_semantic_facts.where(file_id=file.id).select(context_id)
        
        for context_id in contexts_with_facts:
            enqueue_refresh_if_needed(context_id, trigger="file_dirty")
        
        if not contexts_with_facts:
            enqueue_refresh_for_new_file(file)

### New File Refresh (With Churn Guard)

    def enqueue_refresh_for_new_file(file):
        if not file.language:
            return
        
        relevant_contexts = contexts.where(
            language=file.language,
            enabled=True
        )
        
        for context in relevant_contexts:
            enqueue_refresh_if_needed(context.id, trigger="new_file")

### Refresh Enqueue (Monotonic Scope Lattice)

    def enqueue_refresh_if_needed(
        context_id: int, 
        trigger: str,
        scope: RefreshScope | None = None
    ) -> int | None:
        """
        Enqueue a refresh job with optional scoping.
        
        INVARIANT: Scope is monotonic per (context_id, head).
        - Same HEAD: merge scope (widen only), never supersede
        - Different HEAD: supersede existing jobs
        
        scope can be:
            None - full context refresh (broadest)
            RefreshScope(files=[...]) - only refresh specific files
            RefreshScope(packages=[...]) - only refresh specific packages
            RefreshScope(changed_since=timestamp) - only files changed after timestamp
        """
        current_head = git_rev_parse_head()
        
        existing = refresh_jobs.where(
            context_id=context_id,
            status__in=['queued', 'running']
        ).first()
        
        if existing is None:
            # No existing job - create new
            return create_refresh_job(context_id, scope, current_head, trigger)
        
        if existing.head_at_enqueue != current_head:
            # HEAD changed - supersede existing, start fresh
            existing.update(status='superseded', superseded_reason='head_changed')
            return create_refresh_job(context_id, scope, current_head, trigger)
        
        # Same HEAD - merge scopes (widen only), never supersede
        merged = merge_scopes(
            RefreshScope.from_json(existing.scope) if existing.scope else None,
            scope
        )
        
        if merged == existing.scope:
            # New scope is subset of existing - no action needed
            return None
        
        # Scope widened - update existing job metadata
        if existing.status == 'queued':
            # Job not yet started - update in place
            existing.update(scope=merged.to_json() if merged else None)
        else:
            # Job is running - note for potential re-queue after completion
            # (or just let it complete and enqueue follow-up if needed)
            pass
        
        return existing.id
    
    def create_refresh_job(context_id, scope, head, trigger) -> int:
        job = refresh_jobs.create(
            context_id=context_id,
            status='queued',
            trigger_reason=trigger,
            head_at_enqueue=head,
            scope=scope.to_json() if scope else None,
            created_at=now()
        )
        return job.id
    
    def merge_scopes(a: RefreshScope | None, b: RefreshScope | None) -> RefreshScope | None:
        """
        Return the union (widest) of two scopes.
        None means full refresh (broadest possible).
        """
        # None (full refresh) absorbs everything
        if a is None or b is None:
            return None
        
        # Merge file lists
        merged_files = set(a.files or []) | set(b.files or [])
        
        # Merge package lists
        merged_packages = set(a.packages or []) | set(b.packages or [])
        
        # changed_since: take the earlier timestamp (wider range)
        merged_since = None
        if a.changed_since and b.changed_since:
            merged_since = min(a.changed_since, b.changed_since)
        elif a.changed_since or b.changed_since:
            merged_since = a.changed_since or b.changed_since
        
        return RefreshScope(
            files=sorted(merged_files) if merged_files else None,
            packages=sorted(merged_packages) if merged_packages else None,
            changed_since=merged_since
        )
    
    @dataclass
    class RefreshScope:
        files: list[str] | None = None
        packages: list[str] | None = None
        changed_since: float | None = None
        
        def to_json(self) -> str:
            return json.dumps(asdict(self))
        
        @classmethod
        def from_json(cls, s: str) -> "RefreshScope":
            return cls(**json.loads(s))

### Refresh Worker (Fresh HEAD at Import Time)

    def run_refresh_job(job_id: int):
        """
        Execute a refresh job.
        
        COMMIT ORDER (critical for correctness):
        1. Claim job (queued → running)
        2. Run indexer
        3. Re-read HEAD and check not superseded (MUST be fresh read)
        4. Import output (transactional)
        5. Mark job completed
        
        CRITICAL: HEAD check at step 3 must use a FRESH git_rev_parse_head() call,
        not a value captured at function start. HEAD can change during indexer run.
        """
        job = refresh_jobs.get(job_id)
        
        # Step 1: Atomic claim (queued → running)
        rows_updated = refresh_jobs.update(
            status='running',
            started_at=now()
        ).where(
            id=job_id,
            status='queued'
        ).execute()
        
        if rows_updated == 0:
            log.info("job_claim_failed", job_id=job_id)
            return
        
        output_path = None
        
        try:
            # Step 2: Run indexer (with scope if provided)
            # This may take seconds to minutes
            scope = RefreshScope.from_json(job.scope) if job.scope else None
            output_path = run_indexer(job.context_id, scope=scope)
            
            # Step 3: CRITICAL - Fresh HEAD read and supersede check
            # This MUST happen immediately before import decision
            job.refresh()  # Reload job state from DB
            current_head = git_rev_parse_head()  # FRESH read, not cached
            
            if job.status == 'superseded':
                log.info("job_superseded_before_import", job_id=job_id)
                cleanup_output(output_path)
                return
            
            if job.head_at_enqueue != current_head:
                # HEAD moved during indexing - output is stale
                log.info("job_head_changed_during_run", 
                         job_id=job_id, 
                         enqueued_head=job.head_at_enqueue,
                         current_head=current_head)
                refresh_jobs.update(
                    status='superseded',
                    superseded_reason='head_changed_during_run'
                ).where(id=job_id).execute()
                cleanup_output(output_path)
                return
            
            # Step 4: Import output (all-or-nothing)
            # This should be transactional - either all facts imported or none
            with db.transaction():
                import_scip_output(output_path, job.context_id)
            
            # Step 5: Mark completed AFTER successful import
            # Atomic check: still running and same head
            rows_updated = refresh_jobs.update(
                status='completed',
                finished_at=now(),
                output_path=output_path,
                output_hash=hash_file(output_path)
            ).where(
                id=job_id,
                status='running',
                head_at_enqueue=job.head_at_enqueue  # Extra guard
            ).execute()
            
            if rows_updated == 0:
                # Job was superseded during import - rollback would be ideal
                # but import already committed. Log warning.
                log.warn("job_superseded_during_import", job_id=job_id)
            
        except Exception as e:
            log.error("job_failed", job_id=job_id, error=str(e))
            refresh_jobs.update(
                status='failed',
                finished_at=now(),
                error=str(e)
            ).where(
                id=job_id,
                status='running'
            ).execute()
            
            if output_path:
                cleanup_output(output_path)

### Bulk Reconcile (Optimized)

    def bulk_reconcile_indexed_files():
        db_files = {f.path: f for f in files.all()}
        current_indexable = get_indexable_files(force_refresh=True)
        
        to_check = []
        to_delete = []
        to_add = []
        
        for path in current_indexable:
            if path in db_files:
                to_check.append(path)
            else:
                to_add.append(path)
        
        for path in db_files:
            if path not in current_indexable:
                to_delete.append(path)
        
        # Delete no-longer-indexable files (no hashing needed)
        for path in to_delete:
            handle_deletion(path)
        
        # Hash in parallel
        with ThreadPoolExecutor(max_workers=8) as pool:
            check_results = list(pool.map(
                lambda p: (p, compute_content_hash(p) if path_exists(p) else None),
                to_check
            ))
            add_results = list(pool.map(
                lambda p: (p, compute_content_hash(p) if path_exists(p) else None),
                to_add
            ))
        
        for path, current_hash in check_results:
            if current_hash is None:
                handle_deletion(path)
            elif db_files[path].content_hash != current_hash:
                handle_change(path)
        
        for path, current_hash in add_results:
            if current_hash is not None:
                handle_change(path)

---

## SQLite Schema

    files(
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE,
        language TEXT,
        content_hash TEXT NOT NULL,
        syntactic_interface_hash TEXT,
        indexed_at REAL
    )

    file_semantic_facts(
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts(id) ON DELETE CASCADE,
        semantic_interface_hash TEXT,
        content_hash_at_index TEXT,
        ambiguity_flags TEXT,  -- JSON: which ambiguity types detected
        refreshed_at REAL,
        PRIMARY KEY (file_id, context_id)
    )

    contexts(
        id INTEGER PRIMARY KEY,
        name TEXT,
        language TEXT,
        config_hash TEXT,
        tool_version TEXT,
        repo_state TEXT,
        enabled BOOLEAN DEFAULT TRUE,
        refreshed_at REAL
    )

    refresh_jobs(
        id INTEGER PRIMARY KEY,
        context_id INTEGER REFERENCES contexts,
        status TEXT NOT NULL,
        scope TEXT,  -- JSON: RefreshScope for scoped refresh
        trigger_reason TEXT,
        head_at_enqueue TEXT,
        created_at REAL,
        started_at REAL,
        finished_at REAL,
        output_path TEXT,
        output_hash TEXT,
        error TEXT,
        superseded_reason TEXT
    )

    repo_state(
        id INTEGER PRIMARY KEY DEFAULT 1,
        last_seen_head TEXT,
        last_seen_index_mtime REAL,
        checked_at REAL
    )

    symbols(
        id INTEGER PRIMARY KEY,
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts,
        name TEXT,
        qualified_name TEXT,
        kind TEXT,
        line INTEGER,
        column INTEGER,
        end_line INTEGER,
        end_column INTEGER,
        signature TEXT,
        container_chain TEXT,
        layer TEXT NOT NULL
    )

    symbol_interface_hashes(
        symbol_id INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts(id) ON DELETE CASCADE,
        interface_hash TEXT,
        PRIMARY KEY (symbol_id, context_id)
    )

    occurrences(
        id INTEGER PRIMARY KEY,
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        symbol_id INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts,
        start_line INTEGER,
        start_col INTEGER,
        end_line INTEGER,
        end_col INTEGER,
        role TEXT,
        layer TEXT NOT NULL,
        file_content_hash_at_index TEXT,
        anchor_before TEXT,  -- tokens before occurrence for verification
        anchor_after TEXT    -- tokens after occurrence for verification
    )

    edges(
        id INTEGER PRIMARY KEY,
        source_file INTEGER REFERENCES files(id) ON DELETE CASCADE,
        target_file INTEGER REFERENCES files(id) ON DELETE CASCADE,
        dependency_type TEXT NOT NULL,
        relevant_symbol_id INTEGER REFERENCES symbols(id) ON DELETE SET NULL,
        context_id INTEGER REFERENCES contexts,
        layer TEXT NOT NULL
    )

    exports(
        file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
        symbol_id INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
        context_id INTEGER REFERENCES contexts,
        public_name TEXT,
        is_reexport BOOLEAN DEFAULT FALSE,
        reexport_source TEXT,
        layer TEXT NOT NULL
    )

    decision_cache(
        id INTEGER PRIMARY KEY,
        ambiguity_signature TEXT NOT NULL,  -- hash of (symbol, ambiguity_type, relevant_slice_hashes)
        repo_head TEXT NOT NULL,
        file_hashes TEXT NOT NULL,  -- JSON: {path: hash} for relevant files
        decision TEXT NOT NULL,  -- JSON: the decision made
        proof_payload TEXT NOT NULL,  -- JSON: evidence that justified decision
        created_at REAL,
        UNIQUE(ambiguity_signature, repo_head)
    )

---

## File State Model

### Freshness Axis

    CLEAN: Semantic data matches current content, deps confirmed unchanged.
    DIRTY: Content changed, refresh already enqueued.
    STALE: Dependency interface confirmed changed.
    PENDING_CHECK: Dependency is DIRTY, interface change unknown.

### Certainty Axis (Orthogonal to Freshness)

    CERTAIN: Semantic identity proven, no ambiguity.
    AMBIGUOUS: Semantic index fresh but language semantics ambiguous 
               (dynamic dispatch, reflection, multiple definitions).

### Combined State Matrix

    Freshness × Certainty → Mutation Behavior
    
    CLEAN + CERTAIN    → Automatic semantic edits allowed
    CLEAN + AMBIGUOUS  → Return needs_decision with candidates
    DIRTY + *          → Block, wait for refresh
    STALE + *          → Block, wait for refresh
    PENDING_CHECK + *  → Block, wait for dependency resolution

### State Computation (Per-Request Memoization)

    def get_file_state(file_id, context_id, memo=None, visiting=None) -> FileState:
        if memo is None:
            memo = {}
        if visiting is None:
            visiting = set()
        
        key = (file_id, context_id)
        
        if key in memo:
            return memo[key]
        
        if key in visiting:
            return FileState(freshness="cycle", certainty="unknown")
        
        visiting.add(key)
        
        try:
            facts = file_semantic_facts.get(file_id, context_id)
            file = files.get(file_id)
            
            if not facts:
                result = FileState(freshness="unindexed", certainty="unknown")
            elif facts.content_hash_at_index != file.content_hash:
                result = FileState(freshness="dirty", certainty="unknown")
            else:
                freshness = "clean"
                for dep_file_id in get_dependency_file_ids(file_id, context_id):
                    dep_state = get_file_state(dep_file_id, context_id, memo, visiting)
                    
                    if dep_state.freshness == "cycle":
                        continue
                    if dep_state.freshness == "stale":
                        freshness = "stale"
                        break
                    elif dep_state.freshness == "dirty" and freshness == "clean":
                        freshness = "pending_check"
                
                # Determine certainty from ambiguity flags
                certainty = "certain"
                if facts.ambiguity_flags:
                    flags = json.loads(facts.ambiguity_flags)
                    if flags:
                        certainty = "ambiguous"
                
                result = FileState(freshness=freshness, certainty=certainty)
            
            memo[key] = result
            return result
        finally:
            visiting.discard(key)
    
    @dataclass
    class FileState:
        freshness: str  # clean, dirty, stale, pending_check, unindexed, cycle
        certainty: str  # certain, ambiguous, unknown

---

## Witness Packet Schema

    @dataclass
    class WitnessPacket:
        """
        Structured evidence for blocked or needs_decision responses.
        Stable schema for agent consumption.
        """
        # What we checked
        bounds: ScanBounds
        
        # What we found
        facts: list[WitnessFact]
        
        # What failed (for blocked responses)
        invariants_failed: list[str]
        
        # Candidate sets (for needs_decision)
        candidate_sets: dict[str, CandidateSet]
        
        # What would collapse ambiguity
        disambiguation_checklist: list[DisambiguationItem]
    
    @dataclass
    class ScanBounds:
        """Explicit limits on what was scanned."""
        files_scanned: list[str]
        contexts_queried: list[str]
        time_budget_ms: int
        truncated: bool  # True if scan hit limits
    
    @dataclass
    class WitnessFact:
        """A single piece of evidence."""
        fact_type: str  # "definition", "reference", "import", "scope_chain", etc.
        location: Location
        content: str
        provenance: str  # "semantic" | "syntactic" | "text" | "runtime_observed"
        confidence: float  # 0.0 - 1.0
    
    @dataclass
    class CandidateSet:
        """A named set of candidates with deterministic membership."""
        name: str
        description: str
        members: list[Candidate]
        membership_rule: str  # Human-readable rule for membership
    
    @dataclass
    class Candidate:
        """A single candidate in a decision problem."""
        id: str
        description: str
        evidence: list[WitnessFact]
        apply_plan: RefactorPlan  # Exact edits if this candidate is chosen
        risk_level: str  # "low" | "medium" | "high"
        risk_reasons: list[str]
    
    @dataclass
    class DisambiguationItem:
        """What additional fact would collapse to 1 option."""
        question: str  # "Which of these definitions is in scope at cursor?"
        fact_needed: str  # "scope_resolution"
        how_to_verify: str  # "Check import chain from line 15"

---

## Decision Capsules (Micro-Queries)

    Decision capsules are pre-packaged questions that agents can answer
    by reading code, not by receiving more context dumps.
    
    @dataclass
    class DecisionCapsule:
        """
        A bounded, verifiable decision problem.
        """
        capsule_type: str
        inputs: dict
        candidate_outputs: list[CapsuleOutput]
        verification_method: str  # How agent can verify by reading code
        stop_rule: str  # When to stop searching
    
    @dataclass
    class CapsuleOutput:
        """One possible answer to the capsule question."""
        id: str
        value: Any
        supporting_evidence: list[Location]
    
    # Example capsule types:
    
    def capsule_scope_resolution(symbol: str, cursor: Location) -> DecisionCapsule:
        """Which of these N definitions is in scope at cursor?"""
        return DecisionCapsule(
            capsule_type="scope_resolution",
            inputs={
                "symbol": symbol,
                "cursor": cursor,
                "scope_chain": get_scope_chain(cursor),
                "ast_context": get_ast_ancestors(cursor, depth=3)
            },
            candidate_outputs=[
                CapsuleOutput(id=f"def_{i}", value=d, supporting_evidence=[d.location])
                for i, d in enumerate(find_all_definitions(symbol))
            ],
            verification_method="Check import statements and scope nesting",
            stop_rule="First definition that is importable and not shadowed"
        )
    
    def capsule_receiver_resolution(call_site: Location) -> DecisionCapsule:
        """Which of these M receivers can reach this call?"""
        return DecisionCapsule(
            capsule_type="receiver_resolution",
            inputs={
                "call_site": call_site,
                "assignment_slice": get_def_use_slice(call_site)
            },
            candidate_outputs=[...],
            verification_method="Trace assignments backward from call site",
            stop_rule="All assignments that flow to receiver position"
        )
    
    def capsule_context_membership(file: str) -> DecisionCapsule:
        """Which of these K contexts include this file?"""
        return DecisionCapsule(
            capsule_type="context_membership",
            inputs={
                "file": file,
                "context_configs": [c.config for c in contexts.all()]
            },
            candidate_outputs=[...],
            verification_method="Check context config include/exclude patterns",
            stop_rule="All contexts whose patterns match file"
        )

---

## Syntactic Slice Engine

    When semantic confidence is low, produce deterministic slices
    that reduce agent search space without guessing.
    
    class SyntacticSliceEngine:
        """
        Deterministic slicing of syntax/lexical structure.
        NOT semantic inference - just structured extraction.
        """
        
        def def_use_slice(self, file_id: int, variable: str, scope: Range) -> DefUseSlice:
            """
            Assignments to a variable within a function scope.
            """
            tree = get_tree_sitter_tree(file_id)
            
            definitions = []
            uses = []
            
            for node in traverse_scope(tree, scope):
                if is_assignment_to(node, variable):
                    definitions.append(Location.from_node(node))
                elif is_use_of(node, variable):
                    uses.append(Location.from_node(node))
            
            return DefUseSlice(
                variable=variable,
                scope=scope,
                definitions=definitions,
                uses=uses,
                flow_edges=compute_local_flow(definitions, uses)
            )
        
        def import_resolution_slice(self, file_id: int) -> ImportSlice:
            """
            Import statements + aliasing + nearby symbol tables.
            """
            tree = get_tree_sitter_tree(file_id)
            
            return ImportSlice(
                imports=extract_imports(tree),
                aliases=extract_import_aliases(tree),
                local_symbols=extract_local_symbol_table(tree)
            )
        
        def minimal_dependency_chain(self, symbol_id: int, context_id: int) -> list[DependencyLink]:
            """
            Minimal chain of dependencies for affected symbol.
            """
            chain = []
            visited = set()
            
            def trace(sid):
                if sid in visited:
                    return
                visited.add(sid)
                
                for edge in edges.where(target_symbol=sid, context_id=context_id):
                    chain.append(DependencyLink(
                        from_symbol=edge.source_symbol,
                        to_symbol=sid,
                        dependency_type=edge.dependency_type
                    ))
                    trace(edge.source_symbol)
            
            trace(symbol_id)
            return chain
    
    @dataclass
    class DefUseSlice:
        variable: str
        scope: Range
        definitions: list[Location]
        uses: list[Location]
        flow_edges: list[tuple[Location, Location]]
    
    @dataclass
    class ImportSlice:
        imports: list[ImportStatement]
        aliases: dict[str, str]  # alias -> original
        local_symbols: dict[str, Location]  # name -> definition

---

## Mutation Gate

    Semantic write allowed ⟺ all affected files are CLEAN + CERTAIN
    
    Response outcomes:
        ok           - edits applied
        ok_syntactic - edits applied via syntactic fallback
        blocked      - non-CLEAN files, with witness packet
        needs_decision - CLEAN but AMBIGUOUS, with candidates
        refused      - operation cannot be performed
        unsupported  - operation not implemented for this language
    
    def semantic_mutation(file, line, col, operation, target_context, mode="semantic"):
        if mode == "force_syntactic":
            return execute_syntactic_fallback(operation)
        
        symbol = find_symbol_at(file, line, col, target_context)
        affected_files = get_files_with_occurrences(symbol.id, target_context)
        
        states = get_file_states_batch([af.id for af in affected_files], target_context)
        
        # Check freshness first
        non_clean = [(af.path, states[af.id]) for af in affected_files 
                     if states[af.id].freshness != "clean"]
        
        if non_clean:
            return {
                "status": "blocked",
                "reason": "semantic_not_fresh",
                "witness": build_witness_packet(non_clean, operation),
                "can_force_syntactic": True,
                "suggested_refresh_scope": compute_minimal_refresh_scope(non_clean)
            }
        
        # Check certainty
        ambiguous = [(af.path, states[af.id]) for af in affected_files 
                     if states[af.id].certainty == "ambiguous"]
        
        if ambiguous:
            return build_needs_decision_response(symbol, ambiguous, operation, target_context)
        
        # All CLEAN + CERTAIN - proceed
        return generate_and_apply_edits(symbol, operation, target_context)
    
    def build_needs_decision_response(symbol, ambiguous_files, operation, context_id):
        """
        Build a structured decision problem for the agent.
        """
        # Compute candidates
        candidates = compute_candidates(symbol, ambiguous_files, context_id)
        
        # Check decision cache
        cache_key = compute_ambiguity_signature(symbol, ambiguous_files)
        cached = decision_cache.get(cache_key, repo_head=git_rev_parse_head())
        
        if cached and verify_cached_decision_still_valid(cached, ambiguous_files):
            # Offer cached decision as preferred option
            candidates = [
                Candidate(
                    id="cached",
                    description="Previously verified under same evidence",
                    evidence=json.loads(cached.proof_payload),
                    apply_plan=json.loads(cached.decision),
                    risk_level="low",
                    risk_reasons=[]
                )
            ] + candidates
        
        # Build decision capsules
        capsules = []
        for ambig_type in get_ambiguity_types(symbol, ambiguous_files):
            capsules.append(build_capsule_for_ambiguity(ambig_type, symbol))
        
        return {
            "status": "needs_decision",
            "symbol": symbol.qualified_name,
            "candidates": [c.to_dict() for c in candidates],
            "witness": build_witness_packet(ambiguous_files, operation),
            "decision_capsules": [c.to_dict() for c in capsules],
            "commit_endpoint": "/decisions/commit"
        }

---

## Two-Phase Rename

    Rename is the classic ambiguity case. Two-phase flow:
    
    Phase 1 (plan): Produce candidate occurrences grouped by equivalence class
    Phase 2 (commit): Apply only agent-selected groups with verification
    
    def rename_phase1_plan(symbol, new_name, context_id) -> RenamePlan:
        """
        Phase 1: Produce occurrence groups, no edits applied.
        """
        all_occurrences = find_all_occurrences(symbol, context_id)
        
        # Group by equivalence class
        groups = group_occurrences_by_equivalence(all_occurrences, context_id)
        
        return RenamePlan(
            plan_id=generate_plan_id(),
            symbol=symbol,
            new_name=new_name,
            groups=[
                OccurrenceGroup(
                    group_id=f"group_{i}",
                    description=describe_group(g),
                    occurrences=g.occurrences,
                    confidence=g.confidence,
                    provenance=g.provenance,  # "semantic" | "syntactic" | "heuristic"
                    semantic_symbol_id=g.symbol_id,
                    import_chain=g.import_chain,
                    edits_preview=generate_edits_preview(g.occurrences, new_name)
                )
                for i, g in enumerate(groups)
            ],
            affected_files=[occ.file for g in groups for occ in g.occurrences],
            created_at=now(),
            expires_at=now() + 300  # 5 minute TTL
        )
    
    def group_occurrences_by_equivalence(occurrences, context_id) -> list[EquivalenceGroup]:
        """
        Group occurrences by:
        - Syntactic scope
        - Import chain
        - Last-known semantic symbol ID
        """
        groups = defaultdict(list)
        
        for occ in occurrences:
            key = (
                occ.semantic_symbol_id or "unknown",
                compute_scope_key(occ),
                compute_import_chain_key(occ, context_id)
            )
            groups[key].append(occ)
        
        return [
            EquivalenceGroup(
                occurrences=occs,
                symbol_id=key[0],
                scope_key=key[1],
                import_chain=key[2],
                confidence=compute_group_confidence(occs),
                provenance=determine_provenance(occs)
            )
            for key, occs in groups.items()
        ]
    
    def rename_phase2_commit(plan_id, selected_group_ids, proof_payload) -> RenameResult:
        """
        Phase 2: Apply selected groups after verification.
        """
        plan = get_rename_plan(plan_id)
        
        if not plan or plan.expires_at < now():
            return {"status": "refused", "reason": "plan_expired"}
        
        selected_groups = [g for g in plan.groups if g.group_id in selected_group_ids]
        
        # Verify proof payload
        if not verify_proof_payload(selected_groups, proof_payload):
            return {"status": "refused", "reason": "proof_verification_failed"}
        
        # Re-verify anchors and hashes
        for group in selected_groups:
            for occ in group.occurrences:
                if not verify_occurrence_anchors(occ):
                    return {
                        "status": "blocked",
                        "reason": "occurrence_shifted",
                        "details": {"occurrence": occ.to_dict()}
                    }
        
        # Apply edits
        edits = []
        for group in selected_groups:
            edits.extend(group.edits_preview)
        
        result = apply_edits_atomically(edits)
        
        # Cache the decision for future
        cache_decision(plan, selected_group_ids, proof_payload)
        
        return {
            "status": "ok",
            "applied_groups": selected_group_ids,
            "edits": result.delta
        }

---

## Verification Hooks

    Agents prove choices back to CodePlane before edits apply.
    
    POST /decisions/commit
    
    @dataclass
    class DecisionCommitRequest:
        """
        Agent's proof that they chose correctly.
        """
        plan_id: str  # From needs_decision or rename_phase1
        selected_candidate_id: str
        proof: DecisionProof
    
    @dataclass
    class DecisionProof:
        """
        Evidence supporting the agent's choice.
        """
        # Location evidence
        file_line_evidence: list[FileLineEvidence]
        
        # Symbol identity (SCIP symbol string or qualified name)
        symbol_identity: str
        
        # Anchor tokens around each occurrence
        anchors: list[OccurrenceAnchor]
        
        # Optional: reasoning trace (not verified, just logged)
        reasoning: str | None = None
    
    @dataclass
    class FileLineEvidence:
        file: str
        line: int
        content_hash: str  # Hash of line content
        context_lines: list[str]  # 2-3 lines before/after
    
    @dataclass
    class OccurrenceAnchor:
        file: str
        line: int
        col: int
        anchor_before: str  # ~10 chars before
        anchor_after: str   # ~10 chars after
        occurrence_text: str
    
    def handle_decision_commit(request: DecisionCommitRequest) -> DecisionCommitResponse:
        """
        CRITICAL: Must re-validate full mutation gate before applying edits.
        Anchor/hash verification alone is insufficient.
        """
        plan = get_plan(request.plan_id)
        
        if not plan:
            return {"status": "refused", "reason": "plan_not_found"}
        
        if plan.expires_at < now():
            return {"status": "refused", "reason": "plan_expired"}
        
        candidate = get_candidate(plan, request.selected_candidate_id)
        
        if not candidate:
            return {"status": "refused", "reason": "candidate_not_found"}
        
        # CRITICAL: Re-validate mutation gate (not just anchors)
        # This is the core invariant: semantic writes require CLEAN + CERTAIN
        affected_files = plan.affected_files  # or derive from candidate.apply_plan
        states = get_file_states_batch(affected_files, plan.context_id)
        
        # Check freshness axis
        non_clean = [f for f in affected_files if states[f].freshness != "clean"]
        if non_clean:
            return {
                "status": "blocked",
                "reason": "files_not_clean",
                "non_clean_files": non_clean,
                "suggested_refresh_scope": RefreshScope(files=non_clean).to_json()
            }
        
        # Check certainty axis
        ambiguous = [f for f in affected_files if states[f].certainty == "ambiguous"]
        if ambiguous:
            # World changed - ambiguity status shifted
            return {
                "status": "needs_decision",
                "reason": "files_now_ambiguous",
                "ambiguous_files": ambiguous,
                "hint": "Re-fetch plan; ambiguity state changed since plan creation"
            }
        
        # Verify anchors match current file state
        for anchor in request.proof.anchors:
            if not verify_anchor(anchor):
                return {
                    "status": "blocked",
                    "reason": "anchor_mismatch",
                    "details": {"anchor": anchor.to_dict()}
                }
        
        # Verify file hashes
        for evidence in request.proof.file_line_evidence:
            current_hash = hash_line(evidence.file, evidence.line)
            if current_hash != evidence.content_hash:
                return {
                    "status": "blocked",
                    "reason": "file_changed",
                    "details": {"file": evidence.file, "line": evidence.line}
                }
        
        # All checks passed - apply the candidate's plan
        result = apply_edits_atomically(candidate.apply_plan.edits)
        
        # Cache for future
        cache_decision_from_proof(plan, request)
        
        return {
            "status": "ok",
            "applied": candidate.id,
            "delta": result.delta
        }

---

## Agent-Friendly Diff Proposals

    When force_syntactic is used, provide tiered risk information.
    
    def execute_syntactic_fallback(operation) -> SyntacticFallbackResult:
        all_edits = compute_syntactic_edits(operation)
        
        high_confidence = []
        risky = []
        
        for edit in all_edits:
            risk = assess_edit_risk(edit)
            
            if risk.level == "low":
                high_confidence.append(edit)
            else:
                risky.append(RiskyEdit(
                    edit=edit,
                    risk_level=risk.level,
                    risk_reasons=risk.reasons,
                    review_checklist=generate_review_checklist(edit, risk)
                ))
        
        return {
            "status": "ok_syntactic",
            "high_confidence_edits": [e.to_dict() for e in high_confidence],
            "risky_edits": [r.to_dict() for r in risky],
            "review_checklist": aggregate_review_checklist(risky),
            "auto_applied": [e.to_dict() for e in high_confidence],  # Only safe edits auto-applied
            "pending_review": [r.edit.to_dict() for r in risky]  # Risky edits need confirmation
        }
    
    def assess_edit_risk(edit) -> EditRisk:
        reasons = []
        
        # Check for shadowing
        if has_shadowing_at_location(edit.location):
            reasons.append("potential_shadowing")
        
        # Check for multiple definitions
        if count_definitions_in_scope(edit.symbol_name, edit.location) > 1:
            reasons.append("multiple_definitions_in_scope")
        
        # Check for dynamic imports
        if is_near_dynamic_import(edit.location):
            reasons.append("dynamic_import_nearby")
        
        # Check for reflection/metaprogramming
        if is_in_metaprogramming_context(edit.location):
            reasons.append("metaprogramming_context")
        
        level = "low" if not reasons else ("high" if len(reasons) > 1 else "medium")
        
        return EditRisk(level=level, reasons=reasons)
    
    def generate_review_checklist(edit, risk) -> list[str]:
        checklist = []
        
        if "potential_shadowing" in risk.reasons:
            checklist.append(f"Verify no shadowing at {edit.location}")
        
        if "multiple_definitions_in_scope" in risk.reasons:
            checklist.append(f"Confirm correct definition at {edit.location}")
        
        if "dynamic_import_nearby" in risk.reasons:
            checklist.append(f"Check dynamic imports don't affect {edit.symbol_name}")
        
        return checklist

---

## Ambiguity Signature Cache

    Cache agent decisions for repeating ambiguity patterns.
    
    def compute_ambiguity_signature(symbol, ambiguous_files) -> str:
        """
        Deterministic signature for an ambiguity scenario.
        """
        components = [
            symbol.qualified_name,
            symbol.kind,
            sorted([f.path for f in ambiguous_files]),
            get_ambiguity_type(symbol, ambiguous_files)
        ]
        return hashlib.sha256(json.dumps(components).encode()).hexdigest()
    
    def cache_decision(plan, selected_ids, proof_payload):
        """
        Store decision for potential replay.
        """
        signature = compute_ambiguity_signature(plan.symbol, plan.ambiguous_files)
        file_hashes = {f.path: f.content_hash for f in plan.ambiguous_files}
        
        decision_cache.upsert(
            ambiguity_signature=signature,
            repo_head=git_rev_parse_head(),
            file_hashes=json.dumps(file_hashes),
            decision=json.dumps({"selected_ids": selected_ids, "plan": plan.to_dict()}),
            proof_payload=json.dumps(proof_payload),
            created_at=now()
        )
    
    def verify_cached_decision_still_valid(cached, current_files) -> bool:
        """
        Check if cached decision applies to current state.
        """
        cached_hashes = json.loads(cached.file_hashes)
        
        for f in current_files:
            if f.path not in cached_hashes:
                return False
            if f.content_hash != cached_hashes[f.path]:
                return False
        
        return True

---

## Daemon Startup

    1. Load SQLite
    2. Check HEAD tripwire → bulk_reconcile_indexed_files() if HEAD changed
    3. Otherwise: bulk_reconcile_indexed_files()
    4. Start file watcher
    5. Start safety net polling (30-120s)
    6. Start HEAD tripwire timer (5-10s)
    7. Start debounce queue consumer
    8. Ready

---

## Summary

    FILE SCOPE
        Indexed: git-tracked + cpl-tracked
        Skipped: cpl-ignored
        Symlinks: realpath validation, target must be inside repo
        Directory walking: MUST use followlinks=False (security)
    
    INDEXABLE CACHE
        is_indexable(): cheap membership test
        get_indexable_files(): full refresh on HEAD change / startup only
        Watcher updates cache incrementally
    
    CHANGE DETECTION
        Watcher → debounce → reconciler
        Safety net polling (30-120s)
        HEAD tripwire (5-10s timer)
    
    REFRESH WORKER (Commit Order)
        1. Claim job (queued → running)
        2. Run indexer (with optional scope)
        3. Fresh HEAD read + supersede check (MUST be fresh, not cached)
        4. Import output (transactional)
        5. Mark completed AFTER import succeeds
        Never mark completed before import.
        Never compare against stale HEAD value.
    
    SCOPED REFRESH (Monotonic Lattice)
        Same HEAD: merge scope (widen only), never supersede
        Different HEAD: supersede existing jobs
        Scope widening: files ∪ files, packages ∪ packages, min(changed_since)
        None (full refresh) absorbs all scopes
    
    BULK RECONCILE
        Files no longer indexable: deleted without hashing
        Parallel hashing for files that need checking
    
    FILE STATE (Two Axes)
        Freshness: CLEAN | DIRTY | STALE | PENDING_CHECK
        Certainty: CERTAIN | AMBIGUOUS
        Combined determines mutation behavior
    
    MUTATION GATE
        CLEAN + CERTAIN → automatic semantic edits
        CLEAN + AMBIGUOUS → needs_decision with candidates
        Non-CLEAN → blocked with witness packet
        Escape hatch: force_syntactic (with risk tiers)
    
    DECISION COMMIT (Full Gate Re-Validation)
        1. Re-validate mutation gate (CLEAN + CERTAIN) - NOT just anchors
        2. If non-CLEAN: return blocked with suggested_refresh_scope
        3. If CLEAN but AMBIGUOUS: return needs_decision (state shifted)
        4. Verify anchors and file hashes
        5. Apply edits only after all checks pass
        Never bypass mutation gate via "proof" against stale plan.
    
    AGENT DECISION FLOW
        1. CodePlane returns needs_decision with:
           - Candidates (each with apply_plan)
           - Witness packet (evidence)
           - Decision capsules (micro-queries)
        2. Agent reasons over candidates
        3. Agent calls /decisions/commit with proof
        4. CodePlane re-validates mutation gate
        5. CodePlane verifies proof (anchors, hashes)
        6. Edits applied only after all verification passes
    
    TWO-PHASE RENAME
        Phase 1: plan → occurrence groups by equivalence class
        Phase 2: commit → agent selects groups, provides proof
        affected_files stored in plan for gate re-validation
    
    CACHING
        Ambiguity signatures cached with file hashes
        Cached decisions offered as "previously verified" option
        Invalid if files changed
    
    INVARIANTS
        Edits from semantic occurrences with valid hashes only
        Heuristics never determine mutation identity
        No persistent semantic engines
        Atomic job status transitions
        Import before completed (not after)
        followlinks=False on all directory walks
        Agents prove choices before edits apply
        Fresh HEAD read at import time (not function start)
        Mutation gate re-validated at decision commit (not just anchors)
        Scope monotonic per (context_id, head) - widen only, never narrow
