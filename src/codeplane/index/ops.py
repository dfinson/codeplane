"""High-level orchestration of the indexing engine.

This module implements the IndexCoordinator - the entry point for all index
operations. It enforces critical serialization invariants:

- reconcile_lock: Only ONE reconcile() at a time (prevents RepoState corruption)
- tantivy_write_lock: Only ONE Tantivy write batch at a time (prevents crashes)

The Coordinator owns component lifecycles and coordinates the indexing pipeline:
Discovery -> Authority -> Membership -> Probe -> Router -> Index
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sqlmodel import select

from codeplane.index._internal.db import (
    Database,
    EpochManager,
    EpochStats,
    IndexRecovery,
    IntegrityChecker,
    IntegrityReport,
    Reconciler,
    create_additional_indexes,
)
from codeplane.index._internal.discovery import (
    ContextDiscovery,
    ContextProbe,
    ContextRouter,
    MembershipResolver,
    Tier1AuthorityFilter,
)
from codeplane.index._internal.indexing import FactQueries, LexicalIndex, StructuralIndexer
from codeplane.index._internal.parsing import TreeSitterParser
from codeplane.index._internal.state import FileStateService
from codeplane.index.models import (
    CandidateContext,
    Certainty,
    Context,
    ContextMarker,
    DefFact,
    File,
    ProbeStatus,
    RefFact,
)
from codeplane.tools.map_repo import IncludeOption, MapRepoResult, RepoMapper

if TYPE_CHECKING:
    from codeplane.index.models import FileState


def _matches_glob(rel_path: str, pattern: str) -> bool:
    """Check if a path matches a glob pattern, with ** support."""
    import fnmatch

    if fnmatch.fnmatch(rel_path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(rel_path, pattern[3:])


@dataclass
class InitResult:
    """Result of coordinator initialization."""

    contexts_discovered: int
    contexts_valid: int
    contexts_failed: int
    contexts_detached: int
    files_indexed: int
    errors: list[str]
    files_by_ext: dict[str, int] = field(default_factory=dict)  # extension -> file count


@dataclass
class IndexStats:
    """Statistics from an indexing operation."""

    files_processed: int
    files_added: int
    files_updated: int
    files_removed: int
    symbols_indexed: int
    duration_seconds: float


@dataclass
class SearchResult:
    """Result from a search operation."""

    path: str
    line: int
    column: int | None
    snippet: str
    score: float


class SearchMode:
    """Search mode enum."""

    TEXT = "text"
    SYMBOL = "symbol"
    PATH = "path"


class IndexCoordinator:
    """
    High-level orchestration with serialization guarantees.

    SERIALIZATION:
    - _reconcile_lock: Only ONE reconcile() at a time
    - _tantivy_write_lock: Only ONE Tantivy write batch at a time

    These locks prevent:
    - RepoState corruption from concurrent reconciliations
    - Tantivy crashes from multiple writers

    Usage::

        coordinator = IndexCoordinator(repo_root, db_path, tantivy_path)
        result = await coordinator.initialize()

        # Search (thread-safe, no locks needed)
        results = await coordinator.search("query", SearchMode.TEXT)

        # Reindex (acquires locks automatically)
        stats = await coordinator.reindex_incremental([Path("a.py")])
    """

    def __init__(
        self,
        repo_root: Path,
        db_path: Path,
        tantivy_path: Path,
        *,
        quiet: bool = False,
    ) -> None:
        """Initialize coordinator with paths."""
        self.repo_root = repo_root
        self.db_path = db_path
        self.tantivy_path = tantivy_path
        self._quiet = quiet

        # Database
        self.db = Database(db_path)

        # Serialization locks
        self._reconcile_lock = threading.Lock()
        self._tantivy_write_lock = threading.Lock()

        # Consistency gating
        self._fresh_event = asyncio.Event()

        # Components (initialized lazily in initialize())
        self._lexical: LexicalIndex | None = None
        self._parser: TreeSitterParser | None = None
        self._router: ContextRouter | None = None
        self._structural: StructuralIndexer | None = None
        self._facts: FactQueries | None = None
        self._state: FileStateService | None = None
        self._reconciler: Reconciler | None = None
        self._epoch_manager: EpochManager | None = None

        self._initialized = False

    async def initialize(self) -> InitResult:
        """
        Full initialization: discover, probe, index.

        Flow:
        1. Create database schema
        2. Create additional indexes
        3. Discover contexts (marker files)
        4. Apply Tier 1 authority filter
        5. Resolve membership (include/exclude specs)
        6. Probe contexts (validate with Tree-sitter)
        7. Persist contexts to database
        8. Initialize router
        9. Index all files
        10. Publish initial epoch
        """
        errors: list[str] = []

        # Step 1-2: Database setup
        self.db.create_all()
        create_additional_indexes(self.db.engine)

        # Initialize components
        self._parser = TreeSitterParser()
        self._lexical = LexicalIndex(self.tantivy_path)
        self._epoch_manager = EpochManager(self.db, self._lexical)

        # Step 3: Discover contexts
        discovery = ContextDiscovery(self.repo_root)
        discovery_result = discovery.discover_all()
        all_candidates = discovery_result.candidates

        # Extract root fallback context before filtering (it bypasses normal flow)
        root_fallback = next(
            (c for c in all_candidates if getattr(c, "is_root_fallback", False)),
            None,
        )
        regular_candidates = [
            c for c in all_candidates if not getattr(c, "is_root_fallback", False)
        ]

        # Step 4: Apply authority filter (only to regular candidates)
        authority = Tier1AuthorityFilter(self.repo_root)
        authority_result = authority.apply(regular_candidates)
        pending_candidates = authority_result.pending
        detached_candidates = authority_result.detached

        # Step 5: Resolve membership
        membership = MembershipResolver()
        membership_result = membership.resolve(pending_candidates)
        resolved_candidates = membership_result.contexts

        # Step 6: Probe contexts (validate each has parseable files)
        from codeplane.core.progress import progress

        probe = ContextProbe(self.repo_root, parser=self._parser)
        probed_candidates: list[CandidateContext] = []

        probe_iter: Iterable[CandidateContext] = resolved_candidates
        if not self._quiet:
            probe_iter = progress(
                resolved_candidates, desc="Validating contexts", unit="contexts", force=True
            )

        for candidate in probe_iter:
            probe_result = probe.validate(candidate)
            if probe_result.valid:
                candidate.probe_status = ProbeStatus.VALID
            elif probe_result.reason and "empty" in probe_result.reason.lower():
                candidate.probe_status = ProbeStatus.EMPTY
            else:
                candidate.probe_status = ProbeStatus.FAILED
            probed_candidates.append(candidate)

        # Add root fallback back (already marked VALID, bypasses probing)
        if root_fallback is not None:
            probed_candidates.append(root_fallback)

        # Step 7: Persist contexts
        contexts_valid = 0
        contexts_failed = 0

        with self.db.session() as session:
            for candidate in probed_candidates:
                # Use special name for root fallback context
                if getattr(candidate, "is_root_fallback", False):
                    name = "_root"
                else:
                    name = candidate.root_path or "root"

                context = Context(
                    name=name,
                    language_family=candidate.language_family.value,
                    root_path=candidate.root_path,
                    tier=candidate.tier,
                    probe_status=candidate.probe_status.value,
                    include_spec=json.dumps(candidate.include_spec)
                    if candidate.include_spec
                    else None,
                    exclude_spec=json.dumps(candidate.exclude_spec)
                    if candidate.exclude_spec
                    else None,
                )
                session.add(context)
                session.flush()

                # Add markers (root fallback has none)
                for marker_path in candidate.markers:
                    marker = ContextMarker(
                        context_id=context.id,
                        marker_path=marker_path,
                        marker_tier="tier1" if candidate.tier == 1 else "tier2",
                        detected_at=time.time(),
                    )
                    session.add(marker)

                if candidate.probe_status == ProbeStatus.VALID:
                    contexts_valid += 1
                elif candidate.probe_status == ProbeStatus.FAILED:
                    contexts_failed += 1

            # Persist detached contexts
            for candidate in detached_candidates:
                context = Context(
                    name=candidate.root_path or "root",
                    language_family=candidate.language_family.value,
                    root_path=candidate.root_path,
                    tier=candidate.tier,
                    probe_status=ProbeStatus.DETACHED.value,
                )
                session.add(context)

            session.commit()

        # Step 8: Initialize router
        self._router = ContextRouter()

        # Initialize remaining components
        self._structural = StructuralIndexer(self.db, self.repo_root)
        self._state = FileStateService(self.db)
        self._reconciler = Reconciler(self.db, self.repo_root)

        # Establish baseline reconciler state (HEAD, .cplignore hash)
        # This prevents spurious change detection on first incremental call
        self._reconciler.reconcile(paths=[])

        # Initialize fact queries
        # Note: FactQueries needs a session, so we create per-request
        self._facts = None  # Created on demand in session context

        # Step 9: Index all files
        files_indexed, indexed_paths, files_by_ext = await self._index_all_files()

        # Reload index so searcher sees committed changes
        if self._lexical is not None:
            self._lexical.reload()

        # Step 10: Publish initial epoch with indexed file paths
        if self._epoch_manager is not None:
            self._epoch_manager.publish_epoch(
                files_indexed=files_indexed,
                indexed_paths=indexed_paths,
            )

        self._initialized = True
        self._fresh_event.set()

        return InitResult(
            contexts_discovered=len(all_candidates),
            contexts_valid=contexts_valid,
            contexts_failed=contexts_failed,
            contexts_detached=len(detached_candidates),
            files_indexed=files_indexed,
            errors=errors,
            files_by_ext=files_by_ext,
        )

    async def load_existing(self) -> bool:
        """Load existing index without re-indexing.

        Use this when starting daemon on an already-initialized repo.
        Performs reconciliation to detect stale files per SPEC §5.5.

        Returns True if index loaded successfully, False if index doesn't exist.
        """
        if self._initialized:
            return True

        # Check if index exists
        if not self.db_path.exists():
            return False

        # Initialize components
        self._parser = TreeSitterParser()
        self._lexical = LexicalIndex(self.tantivy_path)
        self._epoch_manager = EpochManager(self.db, self._lexical)

        # Initialize router from existing contexts
        self._router = ContextRouter()

        # Load existing contexts and populate router
        with self.db.session() as session:
            contexts = session.exec(select(Context)).all()
            if not contexts:
                return False  # No contexts = not initialized

            # Router would be populated from contexts here
            # (Currently router doesn't need initialization data)

        # Initialize remaining components
        self._structural = StructuralIndexer(self.db, self.repo_root)
        self._state = FileStateService(self.db)
        self._reconciler = Reconciler(self.db, self.repo_root)

        # Skip reconciliation on load - reindex_full handles this if needed
        # The old reconcile(paths=[]) was causing hangs on cross-filesystem mounts

        self._facts = None  # Created on demand in session context

        # Reload lexical index to pick up existing data
        if self._lexical is not None:
            self._lexical.reload()

        self._initialized = True
        self._fresh_event.set()
        return True

    async def reindex_incremental(self, changed_paths: list[Path]) -> IndexStats:
        """
        Incremental reindex for changed files.

        SERIALIZED: Acquires reconcile_lock and tantivy_write_lock.

        If .cplignore changes, triggers a full reindex to apply new patterns.
        """
        self._fresh_event.clear()
        try:
            return await self._reindex_incremental_impl(changed_paths)
        finally:
            self._fresh_event.set()

    async def _reindex_incremental_impl(self, changed_paths: list[Path]) -> IndexStats:
        """
        Incremental reindex for changed files.

        SERIALIZED: Acquires reconcile_lock and tantivy_write_lock.

        If .cplignore changes, triggers a full reindex to apply new patterns.
        """
        if not self._initialized:
            msg = "Coordinator not initialized"
            raise RuntimeError(msg)

        start_time = time.time()
        files_updated = 0
        files_removed = 0
        symbols_indexed = 0

        with self._reconcile_lock:
            # Reconcile changes
            if self._reconciler is not None:
                reconcile_result = self._reconciler.reconcile(changed_paths)

                # If .cplignore changed, do full reindex to apply new patterns
                if reconcile_result.cplignore_changed:
                    return await self._reindex_for_cplignore_change()

            # Update Tantivy
            with self._tantivy_write_lock:
                for path in changed_paths:
                    full_path = self.repo_root / path
                    if full_path.exists():
                        content = self._safe_read_text(full_path)
                        symbols = self._extract_symbols(full_path)
                        if self._lexical is not None:
                            self._lexical.add_file(
                                str(path),
                                content,
                                context_id=0,
                                symbols=symbols,
                            )
                        files_updated += 1
                        symbols_indexed += len(symbols)
                    else:
                        if self._lexical is not None:
                            self._lexical.remove_file(str(path))
                        files_removed += 1

            # Reload index so searcher sees committed changes
            if self._lexical is not None:
                self._lexical.reload()

            # Update structural index
            await self._update_structural_index(changed_paths)

        duration = time.time() - start_time

        return IndexStats(
            files_processed=len(changed_paths),
            files_added=0,
            files_updated=files_updated,
            files_removed=files_removed,
            symbols_indexed=symbols_indexed,
            duration_seconds=duration,
        )

    async def _reindex_for_cplignore_change(self) -> IndexStats:
        """Handle .cplignore change by computing file diff and updating index.

        Removes files that are now ignored and adds files that are now included.
        Must be called while holding _reconcile_lock.
        """
        start_time = time.time()
        files_added = 0
        files_removed = 0

        # Get currently indexed files from database
        with self.db.session() as session:
            file_stmt = select(File.path)
            indexed_paths = set(session.exec(file_stmt).all())

        # Get files that should be indexed under current .cplignore rules
        should_index: set[str] = set()
        file_to_context: dict[str, int] = {}  # Map file path to context ID

        with self.db.session() as session:
            ctx_stmt = select(Context).where(
                Context.probe_status == ProbeStatus.VALID.value,
                Context.enabled == True,  # noqa: E712
            )
            contexts = list(session.exec(ctx_stmt).all())

        # Walk filesystem once, apply cplignore
        all_files = self._walk_all_files()

        for context in contexts:
            context_root = self.repo_root / context.root_path
            if not context_root.exists():
                continue
            include_globs = context.get_include_globs()
            exclude_globs = context.get_exclude_globs()
            context_id = context.id or 1

            for file_path in self._filter_files_for_context(
                all_files, context_root, include_globs, exclude_globs
            ):
                rel_path = str(file_path.relative_to(self.repo_root))
                if rel_path not in should_index:
                    should_index.add(rel_path)
                    file_to_context[rel_path] = context_id

        # Compute diff
        to_remove = indexed_paths - should_index
        to_add = should_index - indexed_paths

        # Remove files that are now ignored
        with self._tantivy_write_lock:
            for rel_path in to_remove:
                if self._lexical is not None:
                    self._lexical.remove_file(rel_path)
                files_removed += 1

            # Add files that are now included
            for rel_path in to_add:
                full_path = self.repo_root / rel_path
                if full_path.exists():
                    try:
                        content = self._safe_read_text(full_path)
                        symbols = self._extract_symbols(full_path)
                        ctx_id = file_to_context.get(rel_path, 1)
                        if self._lexical is not None:
                            self._lexical.add_file(
                                rel_path, content, context_id=ctx_id, symbols=symbols
                            )
                        files_added += 1
                    except (OSError, UnicodeDecodeError):
                        continue

        # Reload index
        if self._lexical is not None:
            self._lexical.reload()

        # Pre-create File records for added files before structural indexing
        # This ensures FKs are valid within the same transaction
        file_id_map: dict[str, int] = {}
        if to_add:
            import hashlib

            with self.db.session() as session:
                for rel_path in to_add:
                    full_path = self.repo_root / rel_path
                    if not full_path.exists():
                        continue
                    # Compute content hash
                    content_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()
                    # Detect language
                    ext = full_path.suffix.lower()
                    lang_map = {
                        ".py": "python",
                        ".pyi": "python",
                        ".js": "javascript",
                        ".jsx": "javascript",
                        ".ts": "javascript",
                        ".tsx": "javascript",
                        ".go": "go",
                        ".rs": "rust",
                    }
                    lang = lang_map.get(ext)

                    file_record = File(
                        path=rel_path,
                        content_hash=content_hash,
                        language_family=lang,
                    )
                    session.add(file_record)
                    session.flush()  # Get ID without committing
                    if file_record.id is not None:
                        file_id_map[rel_path] = file_record.id
                session.commit()

        # Update structural index for added files, grouped by context
        if to_add and self._structural is not None:
            # Group files by context_id
            by_context: dict[int, list[str]] = {}
            for rel_path in to_add:
                ctx_id = file_to_context.get(rel_path, 1)
                if ctx_id not in by_context:
                    by_context[ctx_id] = []
                by_context[ctx_id].append(rel_path)

            for ctx_id, paths in by_context.items():
                self._structural.index_files(paths, context_id=ctx_id, file_id_map=file_id_map)

        # Remove structural facts for removed files
        if to_remove:
            self._remove_structural_facts_for_paths(list(to_remove))

        # Remove File records for removed paths
        if to_remove:
            with self.db.bulk_writer() as writer:
                for rel_path in to_remove:
                    writer.delete_where(File, "path = :p", {"p": rel_path})

        duration = time.time() - start_time

        return IndexStats(
            files_processed=len(to_add) + len(to_remove),
            files_added=files_added,
            files_updated=0,
            files_removed=files_removed,
            symbols_indexed=0,
            duration_seconds=duration,
        )

    def _remove_structural_facts_for_paths(self, paths: list[str]) -> None:
        """Remove all structural facts for the given file paths."""
        with self.db.session() as session:
            from sqlalchemy import text

            for str_path in paths:
                file = session.exec(select(File).where(File.path == str_path)).first()
                if file and file.id is not None:
                    file_id = file.id
                    session.exec(
                        text("DELETE FROM def_facts WHERE file_id = :fid").bindparams(fid=file_id)
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM ref_facts WHERE file_id = :fid").bindparams(fid=file_id)
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM scope_facts WHERE file_id = :fid").bindparams(fid=file_id)
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM import_facts WHERE file_id = :fid").bindparams(
                            fid=file_id
                        )
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM local_bind_facts WHERE file_id = :fid").bindparams(
                            fid=file_id
                        )
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM dynamic_access_sites WHERE file_id = :fid").bindparams(
                            fid=file_id
                        )
                    )  # type: ignore[call-overload]
            session.commit()

    async def reindex_full(self) -> IndexStats:
        """
        Full repository reindex - idempotent and incremental.
        """
        self._fresh_event.clear()
        try:
            return await self._reindex_full_impl()
        finally:
            self._fresh_event.set()

    async def _reindex_full_impl(self) -> IndexStats:
        """
        Full repository reindex - idempotent and incremental.

        Checks for stale files (indexed_at == NULL) and only processes those.
        Safe to call at any time; returns immediately if index is up-to-date.

        SERIALIZED: Acquires reconcile_lock and tantivy_write_lock.
        """
        if not self._initialized:
            msg = "Coordinator not initialized"
            raise RuntimeError(msg)

        start_time = time.time()
        files_added = 0
        files_updated = 0
        files_removed = 0

        with self._reconcile_lock:
            # Check for stale files FIRST (fast DB query) before expensive reconcile
            with self.db.session() as session:
                stale_stmt = select(File).where(File.indexed_at == None)  # noqa: E711
                stale_files = list(session.exec(stale_stmt).all())

            # If no stale files, index is already up-to-date - skip reconcile
            if not stale_files:
                duration = time.time() - start_time
                return IndexStats(
                    files_processed=0,
                    files_added=0,
                    files_updated=0,
                    files_removed=0,
                    symbols_indexed=0,
                    duration_seconds=duration,
                )

            # Process stale files
            with self._tantivy_write_lock:
                for file_record in stale_files:
                    full_path = self.repo_root / file_record.path
                    if full_path.exists():
                        try:
                            content = self._safe_read_text(full_path)
                            symbols = self._extract_symbols(full_path)
                            if self._lexical is not None:
                                self._lexical.add_file(
                                    file_record.path,
                                    content,
                                    context_id=0,
                                    symbols=symbols,
                                )
                            # Mark as indexed
                            with self.db.session() as session:
                                f = session.get(File, file_record.id)
                                if f:
                                    f.indexed_at = time.time()
                                    session.commit()
                            files_added += 1
                        except (OSError, UnicodeDecodeError):
                            continue
                    else:
                        # File was deleted - remove from index
                        if self._lexical is not None:
                            self._lexical.remove_file(file_record.path)
                        with self.db.session() as session:
                            f = session.get(File, file_record.id)
                            if f:
                                session.delete(f)
                                session.commit()
                        files_removed += 1

            # Reload index so searcher sees committed changes
            if self._lexical is not None:
                self._lexical.reload()

            # Update structural index for changed files
            if stale_files:
                await self._update_structural_index(
                    [Path(f.path) for f in stale_files if (self.repo_root / f.path).exists()]
                )

            # Publish epoch
            if self._epoch_manager is not None:
                self._epoch_manager.publish_epoch(
                    files_indexed=files_added + files_updated,
                    indexed_paths=[f.path for f in stale_files],
                )

        duration = time.time() - start_time

        return IndexStats(
            files_processed=len(stale_files),
            files_added=files_added,
            files_updated=files_updated,
            files_removed=files_removed,
            symbols_indexed=0,
            duration_seconds=duration,
        )

    async def wait_for_freshness(self) -> None:
        """Block unti index is fresh (no pending writes)."""
        if not self._initialized:
            msg = "Coordinator not initialized"
            raise RuntimeError(msg)
        await self._fresh_event.wait()

    async def search(
        self,
        query: str,
        mode: str = SearchMode.TEXT,
        limit: int = 100,
    ) -> list[SearchResult]:
        """
        Search the index. Thread-safe, no locks needed.

        Args:
            query: Search query string
            mode: SearchMode.TEXT, SYMBOL, or PATH
            limit: Maximum results to return

        Returns:
            List of SearchResult objects
        """
        await self.wait_for_freshness()
        if self._lexical is None:
            return []

        # Use appropriate search method based on mode
        if mode == SearchMode.SYMBOL:
            search_results = self._lexical.search_symbols(query, limit=limit)
        elif mode == SearchMode.PATH:
            search_results = self._lexical.search_path(query, limit=limit)
        else:
            search_results = self._lexical.search(query, limit=limit)

        return [
            SearchResult(
                path=hit.file_path,
                line=hit.line,
                column=hit.column,
                snippet=hit.snippet,
                score=hit.score,
            )
            for hit in search_results.results
        ]

    async def get_def(
        self,
        name: str,
        path: str | None = None,  # noqa: ARG002 - reserved for future use
        context_id: int | None = None,
    ) -> DefFact | None:
        """Get definition by name. Thread-safe.

        Args:
            name: Definition name to find
            path: Optional file path filter (reserved)
            context_id: Optional context filter (unit_id)

        Returns:
            DefFact if found, None otherwise
        """
        await self.wait_for_freshness()
        with self.db.session() as session:
            stmt = select(DefFact).where(DefFact.name == name)
            if context_id is not None:
                stmt = stmt.where(DefFact.unit_id == context_id)
            return session.exec(stmt).first()

    async def get_references(
        self,
        def_fact: DefFact,
        _context_id: int,
        *,
        limit: int = 100,
    ) -> list[RefFact]:
        """Get references to a definition. Thread-safe.

        Args:
            def_fact: DefFact to find references for
            _context_id: Context to search in (reserved for future use)
            limit: Maximum number of results (bounded query)

        Returns:
            List of RefFact objects
        """
        await self.wait_for_freshness()
        with self.db.session() as session:
            facts = FactQueries(session)
            return facts.list_refs_by_def_uid(def_fact.def_uid, limit=limit)

    async def get_file_state(self, file_id: int, context_id: int) -> FileState:
        """Get computed file state for mutation gating."""
        await self.wait_for_freshness()
        if self._state is None:
            from codeplane.index.models import FileState, Freshness

            return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNCERTAIN)

        return self._state.get_file_state(file_id, context_id)

    async def map_repo(
        self,
        include: list[IncludeOption] | None = None,
        depth: int = 3,
    ) -> MapRepoResult:
        """Build repository mental model from indexed data.

        Queries the existing index - does NOT scan filesystem.

        Args:
            include: Sections to include. Defaults to structure, languages, entry_points.
                Options: structure, languages, entry_points, dependencies, test_layout, public_api
            depth: Directory tree depth (default 3)

        Returns:
            MapRepoResult with requested sections populated.
        """
        await self.wait_for_freshness()
        with self.db.session() as session:
            mapper = RepoMapper(session, self.repo_root)
            return mapper.map(include=include, depth=depth)

    async def verify_integrity(self) -> IntegrityReport:
        """Verify index integrity (FK violations, missing files, Tantivy sync).

        Returns:
            IntegrityReport with passed=True if healthy, issues list if not.
        """
        checker = IntegrityChecker(self.db, self.repo_root, self._lexical)
        return checker.verify()

    async def recover(self) -> None:
        """Wipe and prepare for full reindex.

        Per SPEC.md §5.8: On CPL index corruption, wipe and reindex.
        After calling this, call initialize() to rebuild.
        """
        recovery = IndexRecovery(self.db, self.tantivy_path)
        recovery.wipe_all()
        self._initialized = False
        self._lexical = None

    def get_current_epoch(self) -> int:
        """Return current epoch ID, or 0 if none published."""
        if self._epoch_manager is None:
            return 0
        return self._epoch_manager.get_current_epoch()

    def publish_epoch(self, files_indexed: int = 0, commit_hash: str | None = None) -> EpochStats:
        """Atomically publish a new epoch. See SPEC.md §7.6."""
        if self._epoch_manager is None:
            raise RuntimeError("Coordinator not initialized")
        return self._epoch_manager.publish_epoch(files_indexed, commit_hash)

    def await_epoch(self, target_epoch: int, timeout_seconds: float = 5.0) -> bool:
        """Block until epoch >= target, or timeout. Returns True if reached."""
        if self._epoch_manager is None:
            return False
        return self._epoch_manager.await_epoch(target_epoch, timeout_seconds)

    def close(self) -> None:
        """Close all resources."""
        self._lexical = None
        self._initialized = False
        # Dispose DB engine to release file handles
        if hasattr(self, "db") and self.db is not None:
            self.db.engine.dispose()

    async def _index_all_files(self) -> tuple[int, list[str], dict[str, int]]:
        """Index all files in valid contexts.

        Populates both:
        - Tantivy (lexical search)
        - SQLite fact tables (DefFact, RefFact, etc.)

        Returns:
            Tuple of (count of files indexed, list of indexed file paths, files by language).
        """
        from codeplane.core.progress import progress, status
        from codeplane.index._internal.discovery.language_detect import detect_language_family

        if self._lexical is None or self._parser is None:
            return 0, [], {}

        count = 0
        indexed_paths: list[str] = []

        with self._tantivy_write_lock:
            # Get all valid contexts, separating root fallback from others
            with self.db.session() as session:
                stmt = select(Context).where(
                    Context.probe_status == ProbeStatus.VALID.value,
                    Context.enabled == True,  # noqa: E712
                )
                all_contexts = list(session.exec(stmt).all())

            # Separate root fallback (tier=3) from specific contexts
            specific_contexts = [c for c in all_contexts if c.tier != 3]
            root_context = next((c for c in all_contexts if c.tier == 3), None)

            # Walk filesystem ONCE - applies PRUNABLE_DIRS and cplignore
            if not self._quiet:
                status("Discovering files...", style="none", indent=4)

            all_files = self._walk_all_files()

            files_to_index: list[tuple[Path, str, int, str | None]] = []
            # (full_path, rel_str, ctx_id, language_family)
            claimed_paths: set[str] = set()

            # First pass: match files to specific contexts (tier 1/2/ambient)
            for context in specific_contexts:
                context_root = self.repo_root / context.root_path
                if not context_root.exists():
                    continue

                include_globs = context.get_include_globs()
                exclude_globs = context.get_exclude_globs()
                context_id = context.id or 0

                for file_path in self._filter_files_for_context(
                    all_files, context_root, include_globs, exclude_globs
                ):
                    rel_path = file_path.relative_to(self.repo_root)
                    rel_str = str(rel_path)

                    if rel_str in claimed_paths:
                        continue
                    claimed_paths.add(rel_str)
                    files_to_index.append((file_path, rel_str, context_id, context.language_family))

            # Second pass: assign unclaimed files to root fallback context
            if root_context is not None:
                root_context_id = root_context.id or 0
                exclude_globs = root_context.get_exclude_globs()

                for file_path in self._filter_unclaimed_files(all_files, exclude_globs):
                    rel_path = file_path.relative_to(self.repo_root)
                    rel_str = str(rel_path)

                    if rel_str in claimed_paths:
                        continue

                    # Detect language from extension (may be None for unknown types)
                    # Lexical index indexes ALL text files; language is optional
                    lang_family = detect_language_family(file_path)
                    lang_value = lang_family.value if lang_family else None
                    claimed_paths.add(rel_str)
                    files_to_index.append((file_path, rel_str, root_context_id, lang_value))

            # Third pass: index files with progress bar
            context_files: dict[int, list[str]] = {}
            files_by_ext: dict[str, int] = {}

            file_iter: Iterable[tuple[Path, str, int, str | None]] = files_to_index
            if not self._quiet:
                file_iter = progress(files_to_index, desc="Indexing", unit="files")

            for file_path, rel_str, context_id, _lang_family in file_iter:
                try:
                    content = self._safe_read_text(file_path)
                    symbols = self._extract_symbols(file_path)
                    self._lexical.add_file(
                        rel_str,
                        content,
                        context_id=context_id,
                        symbols=symbols,
                    )
                    count += 1
                    indexed_paths.append(rel_str)
                    context_files.setdefault(context_id, []).append(rel_str)
                    # Track by file extension
                    ext = file_path.suffix.lower() or file_path.name.lower()
                    files_by_ext[ext] = files_by_ext.get(ext, 0) + 1
                except (OSError, UnicodeDecodeError):
                    continue

            # Run structural indexer for each context
            if self._structural is not None:
                struct_iter: Iterable[tuple[int, list[str]]] = context_files.items()
                if not self._quiet:
                    struct_iter = progress(
                        list(context_files.items()), desc="Extracting symbols", unit="contexts"
                    )
                for context_id, file_paths in struct_iter:
                    if file_paths:
                        self._structural.index_files(file_paths, context_id)

        return count, indexed_paths, files_by_ext

    async def _update_structural_index(self, changed_paths: list[Path]) -> None:
        """Update structural index for changed files.

        Clears existing facts for changed files, then re-extracts.
        Groups files by context and indexes each group with its context_id.
        """
        if self._structural is None:
            return

        # Convert to string paths
        str_paths = [str(p) for p in changed_paths if (self.repo_root / p).exists()]
        if not str_paths:
            return

        # Load contexts for routing
        with self.db.session() as session:
            from sqlalchemy import text

            contexts = session.exec(
                select(Context).where(Context.probe_status == ProbeStatus.VALID.value)
            ).all()

            # Build file -> context_id mapping
            file_to_context: dict[str, int] = {}
            for ctx in contexts:
                if ctx.id is None:
                    continue
                ctx_root = ctx.root_path
                # NOTE: include_globs and exclude_globs available for future glob matching
                # TODO(#XXX): Apply proper glob matching from include/exclude specs

                for str_path in str_paths:
                    # Check if file is under this context root
                    if not str_path.startswith(ctx_root):
                        continue
                    # For now, accept all files under context root
                    if str_path not in file_to_context:
                        file_to_context[str_path] = ctx.id

            # Delete existing facts for these files before re-indexing
            for str_path in str_paths:
                file = session.exec(select(File).where(File.path == str_path)).first()
                if file and file.id is not None:
                    file_id = file.id
                    # Delete facts for this file using raw SQL
                    session.exec(
                        text("DELETE FROM def_facts WHERE file_id = :fid").bindparams(fid=file_id)
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM ref_facts WHERE file_id = :fid").bindparams(fid=file_id)
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM scope_facts WHERE file_id = :fid").bindparams(fid=file_id)
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM import_facts WHERE file_id = :fid").bindparams(
                            fid=file_id
                        )
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM local_bind_facts WHERE file_id = :fid").bindparams(
                            fid=file_id
                        )
                    )  # type: ignore[call-overload]
                    session.exec(
                        text("DELETE FROM dynamic_access_sites WHERE file_id = :fid").bindparams(
                            fid=file_id
                        )
                    )  # type: ignore[call-overload]
            session.commit()

        # Group files by context_id and re-index
        context_files: dict[int, list[str]] = {}
        for str_path, ctx_id in file_to_context.items():
            if ctx_id not in context_files:
                context_files[ctx_id] = []
            context_files[ctx_id].append(str_path)

        for ctx_id, paths in context_files.items():
            self._structural.index_files(paths, context_id=ctx_id)

    def _clear_all_structural_facts(self) -> None:
        """Clear all structural facts from the database.

        Used before full reindex to avoid duplicate key violations.
        """
        with self.db.session() as session:
            from sqlalchemy import text

            # Clear all fact tables
            session.exec(text("DELETE FROM def_facts"))  # type: ignore[call-overload]
            session.exec(text("DELETE FROM ref_facts"))  # type: ignore[call-overload]
            session.exec(text("DELETE FROM scope_facts"))  # type: ignore[call-overload]
            session.exec(text("DELETE FROM import_facts"))  # type: ignore[call-overload]
            session.exec(text("DELETE FROM local_bind_facts"))  # type: ignore[call-overload]
            session.exec(text("DELETE FROM dynamic_access_sites"))  # type: ignore[call-overload]
            session.commit()

    def _extract_symbols(self, file_path: Path) -> list[str]:
        """Extract symbol names from a file."""
        if self._parser is None:
            return []

        try:
            content = file_path.read_bytes()
            result = self._parser.parse(file_path, content)
            if result is None:
                return []

            symbols = self._parser.extract_symbols(result)
            return [s.name for s in symbols]
        except (OSError, UnicodeDecodeError, ValueError):
            # ValueError: unsupported file extension
            return []

    def _safe_read_text(self, path: Path) -> str:
        """Read file text, treating binary/encoding errors as empty content."""
        try:
            return path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return ""

    def _load_cplignore_patterns(self) -> list[str]:
        """Load ignore patterns from .codeplane/.cplignore.

        This file must exist (created by `cpl init`).
        """
        cplignore_path = self.repo_root / ".codeplane" / ".cplignore"
        if not cplignore_path.exists():
            msg = f".codeplane/.cplignore not found at {cplignore_path}. Run `cpl init` first."
            raise FileNotFoundError(msg)

        content = self._safe_read_text(cplignore_path)
        patterns: list[str] = []

        for line in content.splitlines():
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue
            # Normalize directory patterns
            if line.endswith("/"):
                patterns.append(f"{line}**")
            else:
                patterns.append(line)

        return patterns

    def _walk_all_files(self) -> list[str]:
        """Walk filesystem once, return all indexable file paths (relative to repo root).

        Applies PRUNABLE_DIRS pruning and cplignore filtering.
        Does NOT use git - indexes any file on disk that isn't in .cplignore.
        """
        from codeplane.index._internal.ignore import PRUNABLE_DIRS

        cplignore_patterns = self._load_cplignore_patterns()

        def should_ignore(rel_str: str) -> bool:
            if rel_str.startswith(".codeplane") or ".codeplane/" in rel_str:
                return True
            for pattern in cplignore_patterns:
                if pattern.startswith("!"):
                    if _matches_glob(rel_str, pattern[1:]):
                        return False
                    continue
                if _matches_glob(rel_str, pattern):
                    return True
            return False

        all_files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            # Prune dirs in-place to skip expensive subtrees
            dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS]

            for filename in filenames:
                full_path = Path(dirpath) / filename
                rel_str = str(full_path.relative_to(self.repo_root)).replace("\\", "/")

                if not should_ignore(rel_str):
                    all_files.append(rel_str)

        return all_files

    def _filter_files_for_context(
        self,
        all_files: list[str],
        context_root: Path,
        include_globs: list[str],
        exclude_globs: list[str],
    ) -> list[Path]:
        """Filter pre-walked files for a specific context."""
        # Compute context prefix relative to repo root
        try:
            context_prefix = str(context_root.relative_to(self.repo_root)).replace("\\", "/")
            if context_prefix == ".":
                context_prefix = ""
        except ValueError:
            context_prefix = ""

        files: list[Path] = []
        for rel_str_repo in all_files:
            # Filter to files under context root
            if context_prefix:
                if not rel_str_repo.startswith(context_prefix + "/"):
                    continue
                rel_str = rel_str_repo[len(context_prefix) + 1 :]
            else:
                rel_str = rel_str_repo

            # Check exclude globs
            excluded = False
            for pattern in exclude_globs:
                if _matches_glob(rel_str, pattern):
                    excluded = True
                    break
            if excluded:
                continue

            # Check include globs (empty = include all)
            if include_globs:
                matched = False
                for pattern in include_globs:
                    if _matches_glob(rel_str, pattern):
                        matched = True
                        break
                if not matched:
                    continue

            full_path = self.repo_root / rel_str_repo
            if full_path.is_file():
                files.append(full_path)

        return files

    def _filter_unclaimed_files(
        self,
        all_files: list[str],
        exclude_globs: list[str],
    ) -> list[Path]:
        """Filter pre-walked files for root fallback context."""
        files: list[Path] = []
        for rel_str in all_files:
            # Check exclude globs
            excluded = False
            for pattern in exclude_globs:
                if _matches_glob(rel_str, pattern):
                    excluded = True
                    break
            if excluded:
                continue

            full_path = self.repo_root / rel_str
            if full_path.is_file():
                files.append(full_path)

        return files


__all__ = [
    "IndexCoordinator",
    "IndexStats",
    "InitResult",
    "SearchMode",
    "SearchResult",
]
