"""Import graph — reverse import queries over ImportFact data.

Provides three operations backed by the structural index:

1. ``affected_tests(changed_files)`` — which test files import the changed modules?
2. ``imported_sources(test_files)`` — which source modules does a test import?
3. ``uncovered_modules()`` — which source modules have zero test imports?

All queries use ``ImportFact.source_literal`` for module-level precision
(not ``imported_name`` which is symbol-level and noisy).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import ColumnElement, or_
from sqlmodel import col, select

from codeplane.core.languages import is_test_file
from codeplane.index._internal.indexing.module_mapping import (
    build_module_index,
    path_to_module,
    resolve_module_to_path,
)
from codeplane.index.models import File, ImportFact

if TYPE_CHECKING:
    from sqlmodel import Session


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


@dataclass
class ImpactMatch:
    """A single test file matched by the import graph."""

    test_file: str
    source_modules: list[str]  # modules it imports that were in the changed set
    confidence: Literal["high", "low"]
    reason: str


@dataclass
class ImpactConfidence:
    """Confidence assessment for an import graph query."""

    tier: Literal["complete", "partial"]
    resolved_ratio: float  # 0.0–1.0
    unresolved_files: list[str]  # changed files that couldn't map to modules
    null_source_count: int  # ImportFacts with NULL source_literal in test scope
    reasoning: str


@dataclass
class ImportGraphResult:
    """Result of an affected_tests query."""

    matches: list[ImpactMatch]
    confidence: ImpactConfidence
    changed_modules: list[str]  # dotted module names derived from changed files

    @property
    def test_files(self) -> list[str]:
        """All test file paths (convenience)."""
        return [m.test_file for m in self.matches]

    @property
    def high_confidence_tests(self) -> list[str]:
        return [m.test_file for m in self.matches if m.confidence == "high"]

    @property
    def low_confidence_tests(self) -> list[str]:
        return [m.test_file for m in self.matches if m.confidence == "low"]


@dataclass
class CoverageSourceResult:
    """Result of an imported_sources query."""

    source_dirs: list[str]  # deduplicated source directories for --cov=
    source_modules: list[str]  # raw source_literal values
    confidence: Literal["complete", "partial"]
    null_import_count: int  # imports with no source_literal


@dataclass
class CoverageGap:
    """A source module with no test imports."""

    module: str  # dotted module name
    file_path: str | None  # resolved file path, if available


# ---------------------------------------------------------------------------
# ImportGraph
# ---------------------------------------------------------------------------


class ImportGraph:
    """Reverse import graph queries over the structural index.

    All queries operate on ``ImportFact.source_literal`` for module-level
    precision.  The graph is built lazily on first query and cached.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._module_index: dict[str, str] | None = None  # module_key -> file_path
        self._file_paths: list[str] | None = None
        self._test_file_paths: list[str] | None = None
        self._test_file_set: set[str] | None = None  # O(1) membership checks

    def _ensure_caches(self) -> None:
        """Build module index, file path list, and test file list on first use."""
        if self._module_index is not None:
            return
        stmt = select(File.path)
        paths = list(self._session.exec(stmt).all())
        self._file_paths = [p for p in paths if p is not None]
        self._module_index = build_module_index(self._file_paths)
        self._test_file_paths = [fp for fp in self._file_paths if is_test_file(fp)]
        self._test_file_set = set(self._test_file_paths)

    # -----------------------------------------------------------------
    # 1. affected_tests: changed files → test files
    # -----------------------------------------------------------------

    def affected_tests(self, changed_files: list[str]) -> ImportGraphResult:
        """Find test files affected by changed files.

        Changed source files are traced through the import graph to find tests
        that import them.  Changed test files are included directly as
        high-confidence matches (a modified test is inherently affected).

        Args:
            changed_files: File paths that changed (relative to repo root).

        Returns:
            ImportGraphResult with matches and confidence.
        """
        self._ensure_caches()
        assert self._module_index is not None

        # Step 0: Partition — test files in changed_files are directly affected.
        # The import graph traces source→test imports but cannot discover that
        # a test file changed.  We include them as high-confidence matches at
        # the end (Step 5b).  Source files go through the normal graph walk;
        # test files are excluded from module resolution (Step 1) because they
        # are not importable in most languages and would pollute unresolved/ratio.
        assert self._test_file_set is not None
        direct_test_files = [f for f in changed_files if f in self._test_file_set]
        direct_test_set = set(direct_test_files)
        source_changed_files = [f for f in changed_files if f not in direct_test_set]

        # Step 1: Convert changed file paths to module names.
        # Strategy:
        #   a) Look up declared_module in the File table (covers Java, Kotlin,
        #      Scala, C#, Go, Haskell, Elixir, Julia, Ruby, PHP).
        #   b) Fall back to path_to_module() for Python (filesystem = module).
        #   c) Files that resolve via neither path land in unresolved.
        changed_modules: list[str] = []
        unresolved: list[str] = []

        # Batch-fetch declared_module for source files only (test files are
        # handled by Step 5b and don't need module resolution).
        declared_map: dict[str, str] = {}
        if source_changed_files:
            decl_stmt = select(File.path, File.declared_module).where(
                col(File.path).in_(source_changed_files),
                File.declared_module != None,  # noqa: E711
            )
            for path, decl in self._session.exec(decl_stmt).all():
                if decl:
                    declared_map[path] = decl

        for fp in source_changed_files:
            if fp in declared_map:
                changed_modules.append(declared_map[fp])
            else:
                # Fall back to path_to_module (works for Python)
                mod = path_to_module(fp)
                if mod:
                    changed_modules.append(mod)
                else:
                    unresolved.append(fp)
        if not changed_files:
            return ImportGraphResult(
                matches=[],
                confidence=ImpactConfidence(
                    tier="complete",
                    resolved_ratio=1.0,
                    unresolved_files=[],
                    null_source_count=0,
                    reasoning="no files provided",
                ),
                changed_modules=[],
            )

        # Step 2: Also generate the "short" module forms
        # e.g. src.codeplane.refactor.ops -> also match codeplane.refactor.ops
        search_modules: set[str] = set()
        for mod in changed_modules:
            search_modules.add(mod)
            # Strip src. prefix if present
            if mod.startswith("src."):
                search_modules.add(mod[4:])

        assert self._test_file_paths is not None

        matched_rows: list[tuple[str, str | None]] = []
        null_in_tests = 0
        # like_prefixes tracks child module patterns (e.g. "kotlinx.serialization.json.")
        # used both for the SQL query and for confidence determination in Step 5.
        like_prefixes: list[str] = []

        # Step 3: Module-name-based matching (Python, declaration-based langs)
        # Only runs if we have module names to search for.
        if changed_modules:
            # Three match types pushed into the query:
            #   a) Exact: source_literal == search_mod
            #   b) Parent: source_literal is a parent prefix of search_mod
            #      (test imports a parent package that re-exports from the changed module)
            #   c) Child: source_literal starts with search_mod + "."
            #      (test imports a submodule of the changed module)
            #
            # For (a) and (b), pre-compute all exact + parent module names into an IN set.
            # For (c), use SQL LIKE per search module.
            exact_or_parent: set[str] = set()
            for mod in search_modules:
                exact_or_parent.add(mod)  # exact match
                # Detect separator from module format
                if "::" in mod:
                    sep = "::"
                elif "/" in mod and "." not in mod:
                    sep = "/"
                else:
                    sep = "."
                like_prefixes.append(f"{mod}{sep}")  # child match
                # Parent matches: all prefixes of this module
                parts = mod.split(sep)
                for i in range(1, len(parts)):
                    exact_or_parent.add(sep.join(parts[:i]))

            match_conditions: list[ColumnElement[bool]] = [
                col(ImportFact.source_literal).in_(list(exact_or_parent)),
            ]
            for prefix in like_prefixes:
                match_conditions.append(col(ImportFact.source_literal).startswith(prefix))

            # Single query: only test files, only matching modules
            stmt = (
                select(File.path, ImportFact.source_literal)
                .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                .where(col(File.path).in_(self._test_file_paths))
                .where(or_(*match_conditions))
            )
            matched_rows = list(self._session.exec(stmt).all())

        # Count NULL source_literals in test files for confidence.
        # Use DISTINCT to count unique test files, not duplicate rows.
        null_stmt = (
            select(File.path)
            .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
            .where(col(File.path).in_(self._test_file_paths))
            .where(ImportFact.source_literal == None)  # noqa: E711
        )
        null_in_tests = len(set(self._session.exec(null_stmt).all()))

        # Step 3b: Transitive resolved_path-based matching.
        # Walk the import graph outward from changed files. A test file
        # that imports a barrel (re-export) file which in turn imports the
        # changed file should still be matched.  We do BFS up to a
        # reasonable depth to avoid runaway traversal.
        _MAX_HOPS = 5
        frontier: set[str] = set(changed_files)
        visited: set[str] = set(frontier)
        all_rp_rows: list[tuple[str, str | None]] = []

        for _hop in range(_MAX_HOPS):
            if not frontier:
                break
            rp_stmt = (
                select(File.path, ImportFact.source_literal)
                .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                .where(col(ImportFact.resolved_path).in_(list(frontier)))
            )
            rp_rows = list(self._session.exec(rp_stmt).all())
            if not rp_rows:
                break

            next_frontier: set[str] = set()
            for importer_path, src_literal in rp_rows:
                if importer_path in self._test_file_paths:
                    # This is a test file — record the match
                    all_rp_rows.append((importer_path, src_literal))
                elif importer_path not in visited:
                    # Non-test file that imports something in our frontier —
                    # it might be a barrel/re-export, so keep tracing
                    next_frontier.add(importer_path)
                    # Also record it as a potential source_literal match
                    # (needed for non-test importers that get traced through)

            visited.update(next_frontier)
            frontier = next_frontier

        # Track test files matched via resolved_path — these are deterministic
        # file-path matches and should always be high confidence.
        resolved_path_tests: set[str] = set()
        if all_rp_rows:
            for test_path, _sl in all_rp_rows:
                resolved_path_tests.add(test_path)
            matched_rows.extend(all_rp_rows)

        # Step 3c: Same-directory affinity for Go.
        # Go test files (*_test.go) in the same directory as a changed .go
        # file are part of the same package — they compile together and test
        # the source without explicit imports.  The import graph has no edge
        # linking them, so we match by directory proximity.
        go_changed = [f for f in changed_files if f.endswith(".go") and not is_test_file(f)]
        if go_changed and self._test_file_paths:
            go_test_by_dir: dict[str, list[str]] = {}
            for tp in self._test_file_paths:
                if tp.endswith(".go"):
                    d = tp.rsplit("/", 1)[0] if "/" in tp else "."
                    go_test_by_dir.setdefault(d, []).append(tp)

            already_matched = {row[0] for row in matched_rows}
            for cf in go_changed:
                d = cf.rsplit("/", 1)[0] if "/" in cf else "."
                mod = declared_map.get(cf) or path_to_module(cf) or cf
                for tp in go_test_by_dir.get(d, []):
                    if tp not in already_matched:
                        matched_rows.append((tp, mod))
                        resolved_path_tests.add(tp)  # high confidence
                        already_matched.add(tp)

        # Step 3d: Swift module affinity.
        # Swift uses module-level imports ("import Algorithms").  The module
        # name corresponds to the directory under Sources/ in SwiftPM layout.
        # If a changed .swift source file is under Sources/<Module>/, any test
        # file that imports that module name is affected.
        swift_changed = [f for f in changed_files if f.endswith(".swift") and not is_test_file(f)]
        if swift_changed and self._test_file_paths:
            # Extract module names from changed file paths
            swift_modules: set[str] = set()
            for cf in swift_changed:
                # SwiftPM layout: Sources/<ModuleName>/...
                parts = cf.split("/")
                if len(parts) >= 3 and parts[0].lower() == "sources":
                    swift_modules.add(parts[1])

            if swift_modules:
                # Find test files that import any of these modules
                swift_test_stmt = (
                    select(File.path, ImportFact.source_literal)
                    .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
                    .where(col(File.path).in_(self._test_file_paths))
                    .where(col(ImportFact.source_literal).in_(list(swift_modules)))
                )
                swift_rows = list(self._session.exec(swift_test_stmt).all())
                already_matched = {row[0] for row in matched_rows}
                for test_path, src_literal in swift_rows:
                    if test_path not in already_matched:
                        matched_rows.append((test_path, src_literal))
                        resolved_path_tests.add(test_path)  # high confidence
                        already_matched.add(test_path)

        # Step 4: Group matches by test file
        matches_by_file: dict[str, list[str]] = {}
        for file_path, source_literal in matched_rows:
            if source_literal is not None:
                matches_by_file.setdefault(file_path, []).append(source_literal)

        # Step 5: Build results with confidence
        matches: list[ImpactMatch] = []
        for test_path, src_mods in sorted(matches_by_file.items()):
            # High confidence: exact module match, child module match
            #   (imports something FROM the changed module), resolved_path
            #   match, or Go same-directory.
            # Low confidence: only parent prefix match — test imports a
            #   parent package that may or may not include the changed code.
            unique_mods = sorted(set(src_mods))
            is_direct = (
                any(m in search_modules for m in unique_mods) or test_path in resolved_path_tests
            )
            is_child = not is_direct and any(
                any(m.startswith(prefix) for prefix in like_prefixes) for m in unique_mods
            )
            confidence: Literal["high", "low"] = "high" if is_direct or is_child else "low"
            if is_direct:
                reason = f"directly imports {', '.join(unique_mods)}"
            elif is_child:
                reason = f"imports child of changed module: {', '.join(unique_mods)}"
            else:
                reason = f"imports parent module {', '.join(unique_mods)}"
            matches.append(
                ImpactMatch(
                    test_file=test_path,
                    source_modules=unique_mods,
                    confidence=confidence,
                    reason=reason,
                )
            )

        # Step 5b: Include directly-changed test files that weren't already
        # found through import tracing.  A changed test file is the strongest
        # possible signal — it *is* the affected test.
        already_matched = {m.test_file for m in matches}
        for tf in direct_test_files:
            if tf not in already_matched:
                matches.append(
                    ImpactMatch(
                        test_file=tf,
                        source_modules=[],
                        confidence="high",
                        reason="test file directly changed",
                    )
                )

        # Confidence: resolved_path covers ALL changed files regardless of
        # whether they have a declared_module or Python path_to_module mapping.
        # So module-name "unresolved" files are still searchable via
        # resolved_path — the only true gap is null source_literals in tests.
        # Ratio is based on source files only (test files don't need module
        # resolution — they are matched directly by Step 5b).
        resolved_ratio = (
            (len(changed_modules) / len(source_changed_files)) if source_changed_files else 1.0
        )
        # Tier is "complete" when all match paths are covered; resolved_path
        # query covers files that have no module name, so they're not truly
        # unresolved for matching purposes.
        tier: Literal["complete", "partial"] = "complete" if null_in_tests == 0 else "partial"

        reason_parts: list[str] = []
        if unresolved:
            reason_parts.append(
                f"{len(unresolved)} files have no module name (still matched via resolved_path)"
            )
        if null_in_tests:
            reason_parts.append(f"{null_in_tests} test imports have no source_literal")
        reasoning = (
            "; ".join(reason_parts) if reason_parts else "all files resolved, all imports traced"
        )

        return ImportGraphResult(
            matches=matches,
            confidence=ImpactConfidence(
                tier=tier,
                resolved_ratio=resolved_ratio,
                unresolved_files=unresolved,
                null_source_count=null_in_tests,
                reasoning=reasoning,
            ),
            changed_modules=sorted(search_modules),
        )

    # -----------------------------------------------------------------
    # 2. imported_sources: test files → source modules (for --cov scoping)
    # -----------------------------------------------------------------

    def imported_sources(self, test_files: list[str]) -> CoverageSourceResult:
        """Given test files, find source modules they import.

        Used to auto-scope ``--cov=`` arguments.

        Args:
            test_files: Test file paths.

        Returns:
            CoverageSourceResult with source directories.
        """
        self._ensure_caches()
        assert self._module_index is not None

        if not test_files:
            return CoverageSourceResult(
                source_dirs=[],
                source_modules=[],
                confidence="complete",
                null_import_count=0,
            )

        # Query imports for these test files
        stmt = (
            select(File.path, ImportFact.source_literal)
            .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
            .where(col(File.path).in_(test_files))
        )
        rows = list(self._session.exec(stmt).all())

        source_modules: set[str] = set()
        null_count = 0
        for _file_path, source_literal in rows:
            if source_literal is None:
                null_count += 1
                continue
            # Only include project-internal modules (skip stdlib, third-party)
            resolved = resolve_module_to_path(source_literal, self._module_index)
            if resolved and not is_test_file(resolved):
                source_modules.add(source_literal)

        # Convert modules to directories
        source_dirs: set[str] = set()
        for mod in source_modules:
            resolved_path = resolve_module_to_path(mod, self._module_index)
            if resolved_path:
                # Use parent directory, not the file itself
                parts = resolved_path.rsplit("/", 1)
                source_dirs.add(parts[0] if len(parts) > 1 else resolved_path)

        confidence: Literal["complete", "partial"] = "complete" if null_count == 0 else "partial"

        return CoverageSourceResult(
            source_dirs=sorted(source_dirs),
            source_modules=sorted(source_modules),
            confidence=confidence,
            null_import_count=null_count,
        )

    # -----------------------------------------------------------------
    # 3. uncovered_modules: source modules with zero test imports
    # -----------------------------------------------------------------

    def uncovered_modules(self) -> list[CoverageGap]:
        """Find source modules that no test file imports.

        Returns:
            List of CoverageGap for each uncovered module.
        """
        self._ensure_caches()
        assert self._module_index is not None
        assert self._file_paths is not None

        # All source modules: files not in test paths (use cached set for O(1) lookup)
        test_set = self._test_file_set
        assert test_set is not None
        all_source_modules: set[str] = set()
        for fp in self._file_paths:
            if fp not in test_set:
                mod = path_to_module(fp)
                if mod:
                    all_source_modules.add(mod)

        # Modules imported by test files — scope query to test files only
        test_file_paths = self._test_file_paths
        assert test_file_paths is not None
        stmt = (
            select(ImportFact.source_literal)
            .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
            .where(ImportFact.source_literal != None)  # noqa: E711
            .where(col(File.path).in_(test_file_paths))
        )
        covered_modules: set[str] = {s for s in self._session.exec(stmt).all() if s is not None}

        # Also consider short-form matches (src.X matches X)
        covered_short: set[str] = set()
        for mod in covered_modules:
            covered_short.add(mod)
            if mod.startswith("src."):
                covered_short.add(mod[4:])

        # Find uncovered source modules
        gaps: list[CoverageGap] = []
        for mod in sorted(all_source_modules):
            short = mod[4:] if mod.startswith("src.") else mod
            if mod not in covered_short and short not in covered_short:
                file_path = resolve_module_to_path(mod, self._module_index)
                display_module = short if short else mod
                gaps.append(CoverageGap(module=display_module, file_path=file_path))

        return gaps
