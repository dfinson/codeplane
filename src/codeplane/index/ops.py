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
    ProbeStatus,
    RefFact,
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

        # Initialize fact queries
        # Note: FactQueries needs a session, so we create per-request
        self._facts = None  # Created on demand in session context

        # Step 9: Index all files
        files_indexed, indexed_paths = await self._index_all_files()

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

            # Rebuild Tantivy index - clear first, then index
            # Note: _index_all_files acquires tantivy_write_lock internally
            if self._lexical is not None:
                with self._tantivy_write_lock:
                    self._lexical.clear()

            files_indexed, indexed_paths = await self._index_all_files()

            # Reload index so searcher sees committed changes
            if self._lexical is not None:
                self._lexical.reload()

            # Publish epoch with indexed paths
            if self._epoch_manager is not None:
                self._epoch_manager.publish_epoch(
                    files_indexed=files_indexed,
                    indexed_paths=indexed_paths,
                )

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
        with self.db.session() as session:
            facts = FactQueries(session)
            return facts.list_refs_by_def_uid(def_fact.def_uid, limit=limit)

    async def get_file_state(self, file_id: int, context_id: int) -> FileState:
        """Get computed file state for mutation gating."""
        if self._state is None:
            from codeplane.index.models import FileState, Freshness

            return FileState(freshness=Freshness.UNINDEXED, certainty=Certainty.UNCERTAIN)

        return self._state.get_file_state(file_id, context_id)

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

    async def _index_all_files(self) -> tuple[int, list[str]]:
        """Index all files in valid contexts.

        Returns:
            Tuple of (count of files indexed, list of indexed file paths).
        """
        if self._lexical is None or self._parser is None:
            return 0, []

        count = 0
        indexed_paths: list[str] = []

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
                        indexed_paths.append(str(rel_path))
                    except (OSError, UnicodeDecodeError):
                        continue

        return count, indexed_paths

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

    def _load_cplignore_patterns(self) -> list[str]:
        """Load ignore patterns from .codeplane/.cplignore.

        This file must exist (created by `cpl init`).
        """
        cplignore_path = self.repo_root / ".codeplane" / ".cplignore"
        if not cplignore_path.exists():
            msg = f".codeplane/.cplignore not found at {cplignore_path}. Run `cpl init` first."
            raise FileNotFoundError(msg)

        content = cplignore_path.read_text()
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

    def _find_files(
        self,
        root: Path,
        include_globs: list[str],
        exclude_globs: list[str],
    ) -> list[Path]:
        """Find files matching include/exclude patterns, respecting .cplignore."""
        import fnmatch

        files: list[Path] = []

        # Load ignore patterns from .cplignore.template (bundled source of truth)
        cplignore_patterns = self._load_cplignore_patterns()

        def matches_glob(rel_path: str, pattern: str) -> bool:
            """Match path against glob pattern, handling ** correctly."""
            if fnmatch.fnmatch(rel_path, pattern):
                return True

            # If pattern starts with **/, also match without the prefix
            if pattern.startswith("**/"):
                suffix = pattern[3:]
                if fnmatch.fnmatch(rel_path, suffix):
                    return True

            return False

        def should_ignore(path: Path) -> bool:
            """Check if path should be ignored based on .cplignore patterns."""
            try:
                rel_path = path.relative_to(self.repo_root)
            except ValueError:
                return True

            rel_str = str(rel_path)

            # Always exclude .codeplane directory
            if rel_str.startswith(".codeplane") or ".codeplane/" in rel_str:
                return True

            for pattern in cplignore_patterns:
                # Handle negation patterns
                if pattern.startswith("!"):
                    if matches_glob(rel_str, pattern[1:]):
                        return False
                    continue

                # Standard matching
                if matches_glob(rel_str, pattern):
                    return True

                # Also match against any parent directory
                for parent in rel_path.parents:
                    parent_str = str(parent)
                    if matches_glob(parent_str, pattern):
                        return True
                    if matches_glob(parent_str + "/", pattern):
                        return True

            return False

        for path in root.rglob("*"):
            if not path.is_file():
                continue

            # Check .cplignore patterns
            if should_ignore(path):
                continue

            rel_str = str(path.relative_to(root))

            # Check exclude globs
            excluded = False
            for pattern in exclude_globs:
                if matches_glob(rel_str, pattern):
                    excluded = True
                    break

            if excluded:
                continue

            # Check include globs (empty = include all)
            if not include_globs:
                files.append(path)
            else:
                for pattern in include_globs:
                    if matches_glob(rel_str, pattern):
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
