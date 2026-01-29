"""High-level orchestration of the indexing engine.

This module implements the IndexCoordinator - the entry point for all index
operations. It enforces critical serialization invariants:

- reconcile_lock: Only ONE reconcile() at a time (prevents RepoState corruption)
- tantivy_write_lock: Only ONE Tantivy write batch at a time (prevents crashes)

The Coordinator owns component lifecycles and coordinates the indexing pipeline:
Discovery -> Authority -> Membership -> Probe -> Router -> Index
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlmodel import select

from codeplane.index._internal.db import Database, Reconciler, create_additional_indexes
from codeplane.index._internal.discovery import (
    ContextDiscovery,
    ContextProbe,
    ContextRouter,
    MembershipResolver,
    Tier1AuthorityFilter,
)
from codeplane.index._internal.indexing import LexicalIndex, StructuralIndexer, SymbolGraph
from codeplane.index._internal.parsing import TreeSitterParser
from codeplane.index._internal.state import FileStateService, RefreshJobService
from codeplane.index.models import (
    CandidateContext,
    Context,
    ContextMarker,
    Occurrence,
    ProbeStatus,
    Symbol,
)

if TYPE_CHECKING:
    from codeplane.index.models import FileState


@dataclass
class InitResult:
    """Result of coordinator initialization."""

    contexts_discovered: int
    contexts_valid: int
    contexts_failed: int
    contexts_detached: int
    files_indexed: int
    errors: list[str]


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
    ) -> None:
        """Initialize coordinator with paths."""
        self.repo_root = repo_root
        self.db_path = db_path
        self.tantivy_path = tantivy_path

        # Database
        self.db = Database(db_path)

        # Serialization locks
        self._reconcile_lock = threading.Lock()
        self._tantivy_write_lock = threading.Lock()

        # Components (initialized lazily in initialize())
        self._lexical: LexicalIndex | None = None
        self._parser: TreeSitterParser | None = None
        self._router: ContextRouter | None = None
        self._structural: StructuralIndexer | None = None
        self._graph: SymbolGraph | None = None
        self._state: FileStateService | None = None
        self._reconciler: Reconciler | None = None
        self._refresh: RefreshJobService | None = None

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
        """
        errors: list[str] = []

        # Step 1-2: Database setup
        self.db.create_all()
        create_additional_indexes(self.db.engine)

        # Initialize components
        self._parser = TreeSitterParser()
        self._lexical = LexicalIndex(self.tantivy_path)
        self._refresh = RefreshJobService(self.db, self.repo_root, None)

        # Step 3: Discover contexts
        discovery = ContextDiscovery(self.repo_root)
        discovery_result = discovery.discover_all()
        all_candidates = discovery_result.candidates

        # Step 4: Apply authority filter
        authority = Tier1AuthorityFilter(self.repo_root)
        authority_result = authority.apply(all_candidates)
        pending_candidates = authority_result.pending
        detached_candidates = authority_result.detached

        # Step 5: Resolve membership
        membership = MembershipResolver()
        membership_result = membership.resolve(pending_candidates)
        resolved_candidates = membership_result.contexts

        # Step 6: Probe contexts
        probe = ContextProbe(self.repo_root, parser=self._parser)
        probed_candidates: list[CandidateContext] = []
        for candidate in resolved_candidates:
            probe_result = probe.validate(candidate)
            if probe_result.valid:
                candidate.probe_status = ProbeStatus.VALID
            elif probe_result.reason and "empty" in probe_result.reason.lower():
                candidate.probe_status = ProbeStatus.EMPTY
            else:
                candidate.probe_status = ProbeStatus.FAILED
            probed_candidates.append(candidate)

        # Step 7: Persist contexts
        contexts_valid = 0
        contexts_failed = 0

        with self.db.session() as session:
            for candidate in probed_candidates:
                context = Context(
                    name=candidate.root_path or "root",
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

                # Add markers
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

        # Initialize graph with session
        with self.db.session() as session:
            self._graph = SymbolGraph(session)

        # Step 9: Index all files
        files_indexed = await self._index_all_files()

        self._initialized = True

        return InitResult(
            contexts_discovered=len(all_candidates),
            contexts_valid=contexts_valid,
            contexts_failed=contexts_failed,
            contexts_detached=len(detached_candidates),
            files_indexed=files_indexed,
            errors=errors,
        )

    async def reindex_incremental(self, changed_paths: list[Path]) -> IndexStats:
        """
        Incremental reindex for changed files.

        SERIALIZED: Acquires reconcile_lock and tantivy_write_lock.
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
                self._reconciler.reconcile(changed_paths)

            # Update Tantivy
            with self._tantivy_write_lock:
                for path in changed_paths:
                    full_path = self.repo_root / path
                    if full_path.exists():
                        content = full_path.read_text()
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

    async def reindex_full(self) -> IndexStats:
        """
        Full reindex of entire repository.

        SERIALIZED: Acquires reconcile_lock and tantivy_write_lock.
        """
        if not self._initialized:
            msg = "Coordinator not initialized"
            raise RuntimeError(msg)

        start_time = time.time()

        with self._reconcile_lock:
            # Full reconcile
            if self._reconciler is not None:
                self._reconciler.reconcile(None)

            # Rebuild Tantivy index
            with self._tantivy_write_lock:
                if self._lexical is not None:
                    self._lexical.clear()

                files_indexed = await self._index_all_files()

        duration = time.time() - start_time

        return IndexStats(
            files_processed=files_indexed,
            files_added=files_indexed,
            files_updated=0,
            files_removed=0,
            symbols_indexed=0,
            duration_seconds=duration,
        )

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

    async def get_symbol(
        self,
        name: str,
        path: str | None = None,  # noqa: ARG002 - reserved for future use
        context_id: int | None = None,
    ) -> Symbol | None:
        """
        Get symbol by name. Thread-safe.

        Args:
            name: Symbol name to find
            path: Optional file path filter (reserved)
            context_id: Optional context filter

        Returns:
            Symbol if found, None otherwise
        """
        with self.db.session() as session:
            stmt = select(Symbol).where(Symbol.name == name)
            if context_id is not None:
                stmt = stmt.where(Symbol.context_id == context_id)
            return session.exec(stmt).first()

    async def get_references(
        self,
        symbol: Symbol,
        context_id: int,
    ) -> list[Occurrence]:
        """
        Get all references to a symbol. Thread-safe.

        Args:
            symbol: Symbol to find references for
            context_id: Context to search in

        Returns:
            List of Occurrence objects
        """
        if symbol.id is None:
            return []

        with self.db.session() as session:
            stmt = select(Occurrence).where(
                Occurrence.symbol_id == symbol.id,
                Occurrence.context_id == context_id,
            )
            return list(session.exec(stmt).all())

    async def get_file_state(self, file_id: int, context_id: int) -> FileState:
        """Get computed file state for mutation gating."""
        if self._state is None:
            from codeplane.index.models import Certainty, FileState, Freshness

            return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNKNOWN)

        return self._state.get_file_state(file_id, context_id)

    async def enqueue_refresh(
        self,
        context_id: int,
        trigger_reason: str = "manual",
    ) -> int | None:
        """
        Enqueue a semantic refresh job for a context.

        Returns job ID if created, None if already covered.
        """
        if self._refresh is None:
            return None

        return self._refresh.enqueue_refresh(context_id, None, trigger_reason)

    async def get_missing_tools(self) -> list[tuple[int, str, str]]:
        """
        Get contexts that failed due to missing SCIP tools.

        Returns list of (context_id, language_family, tool_name).
        Used for user confirmation loop.
        """
        if self._refresh is None:
            return []

        failures = self._refresh.get_missing_tool_failures()
        return [(ctx_id, fam.value, tool) for ctx_id, fam, tool in failures]

    def close(self) -> None:
        """Close all resources."""
        self._lexical = None
        self._initialized = False

    async def _index_all_files(self) -> int:
        """Index all files in valid contexts."""
        if self._lexical is None or self._parser is None:
            return 0

        count = 0

        with self._tantivy_write_lock:
            # Get all valid contexts
            with self.db.session() as session:
                stmt = select(Context).where(
                    Context.probe_status == ProbeStatus.VALID.value,
                    Context.enabled == True,  # noqa: E712
                )
                contexts = list(session.exec(stmt).all())

            # Index files for each context
            for context in contexts:
                context_root = self.repo_root / context.root_path
                if not context_root.exists():
                    continue

                include_globs = context.get_include_globs()
                exclude_globs = context.get_exclude_globs()

                # Find matching files
                for file_path in self._find_files(context_root, include_globs, exclude_globs):
                    rel_path = file_path.relative_to(self.repo_root)
                    try:
                        content = file_path.read_text()
                        symbols = self._extract_symbols(file_path)
                        self._lexical.add_file(
                            str(rel_path),
                            content,
                            context_id=context.id or 0,
                            symbols=symbols,
                        )
                        count += 1
                    except (OSError, UnicodeDecodeError):
                        continue

        return count

    async def _update_structural_index(self, changed_paths: list[Path]) -> None:
        """Update structural index for changed files."""
        if self._structural is None or self._parser is None:
            return

        # Parse and update each changed file
        for path in changed_paths:
            full_path = self.repo_root / path
            if not full_path.exists():
                continue

            try:
                content = full_path.read_bytes()
                result = self._parser.parse(full_path, content)
                if result is not None:
                    symbols = self._parser.extract_symbols(result)
                    # Symbols extracted for structural update
                    _ = symbols
            except (OSError, UnicodeDecodeError):
                continue

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
        except (OSError, UnicodeDecodeError):
            return []

    def _find_files(
        self,
        root: Path,
        include_globs: list[str],
        exclude_globs: list[str],
    ) -> list[Path]:
        """Find files matching include/exclude patterns."""
        import fnmatch

        files: list[Path] = []

        # Universal excludes
        excluded_parts = {
            "node_modules",
            "venv",
            "__pycache__",
            ".git",
            "target",
            "dist",
            "build",
            "vendor",
            ".codeplane",
        }

        for path in root.rglob("*"):
            if not path.is_file():
                continue

            # Check excluded parts
            parts = path.relative_to(root).parts
            if any(part in excluded_parts for part in parts):
                continue

            rel_str = str(path.relative_to(root))

            # Check exclude globs
            excluded = False
            for pattern in exclude_globs:
                if fnmatch.fnmatch(rel_str, pattern):
                    excluded = True
                    break

            if excluded:
                continue

            # Check include globs (empty = include all)
            if not include_globs:
                files.append(path)
            else:
                for pattern in include_globs:
                    if fnmatch.fnmatch(rel_str, pattern):
                        files.append(path)
                        break

        return files


__all__ = [
    "IndexCoordinator",
    "IndexStats",
    "InitResult",
    "SearchMode",
    "SearchResult",
]
