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

    def _ensure_caches(self) -> None:
        """Build module index and file path list on first use."""
        if self._module_index is not None:
            return
        stmt = select(File.path)
        paths = list(self._session.exec(stmt).all())
        self._file_paths = [p for p in paths if p is not None]
        self._module_index = build_module_index(self._file_paths)

    # -----------------------------------------------------------------
    # 1. affected_tests: changed files → test files
    # -----------------------------------------------------------------

    def affected_tests(self, changed_files: list[str]) -> ImportGraphResult:
        """Given changed source files, find test files that import them.

        Args:
            changed_files: File paths that changed (relative to repo root).

        Returns:
            ImportGraphResult with matches and confidence.
        """
        self._ensure_caches()
        assert self._module_index is not None

        # Step 1: Convert changed file paths to module names
        changed_modules: list[str] = []
        unresolved: list[str] = []
        for fp in changed_files:
            mod = path_to_module(fp)
            if mod:
                changed_modules.append(mod)
            else:
                unresolved.append(fp)

        if not changed_modules:
            # Empty input or all non-Python files
            early_tier: Literal["complete", "partial"] = (
                "complete" if not changed_files else "partial"
            )
            reasoning = (
                "no files provided"
                if not changed_files
                else "No changed files could be mapped to module names"
            )
            return ImportGraphResult(
                matches=[],
                confidence=ImpactConfidence(
                    tier=early_tier,
                    resolved_ratio=0.0 if changed_files else 1.0,
                    unresolved_files=unresolved,
                    null_source_count=0,
                    reasoning=reasoning,
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

        # Step 3: Query ImportFact for test files importing these modules
        # We need source_literal matching at module level (prefix match for submodule imports)
        stmt = (
            select(File.path, ImportFact.source_literal)
            .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
            .where(ImportFact.source_literal != None)  # noqa: E711
        )
        all_imports = list(self._session.exec(stmt).all())

        # Count NULL source_literals in test files for confidence
        null_stmt = (
            select(File.path)
            .join(ImportFact, ImportFact.file_id == File.id)  # type: ignore[arg-type]
            .where(ImportFact.source_literal == None)  # noqa: E711
        )
        null_paths = list(self._session.exec(null_stmt).all())
        null_in_tests = sum(1 for p in null_paths if is_test_file(p))

        # Step 4: Match — a test file is affected if it imports a module
        # that matches (or is a parent of) a changed module
        matches_by_file: dict[str, list[str]] = {}
        for file_path, source_literal in all_imports:
            if not is_test_file(file_path):
                continue
            if source_literal is None:
                continue
            for search_mod in search_modules:
                # Exact match or prefix match (importing a parent package)
                if source_literal == search_mod or search_mod.startswith(f"{source_literal}."):
                    matches_by_file.setdefault(file_path, []).append(source_literal)
                    break
                # Or the test imports a child of the changed module
                if source_literal.startswith(f"{search_mod}."):
                    matches_by_file.setdefault(file_path, []).append(source_literal)
                    break

        # Step 5: Build results with confidence
        matches: list[ImpactMatch] = []
        for test_path, src_mods in sorted(matches_by_file.items()):
            # High confidence: direct source_literal match
            # Low confidence: only parent/child prefix match (less certain)
            unique_mods = sorted(set(src_mods))
            has_exact = any(m in search_modules for m in unique_mods)
            confidence: Literal["high", "low"] = "high" if has_exact else "low"
            reason = (
                f"directly imports {', '.join(unique_mods)}"
                if has_exact
                else f"imports parent/child module {', '.join(unique_mods)}"
            )
            matches.append(
                ImpactMatch(
                    test_file=test_path,
                    source_modules=unique_mods,
                    confidence=confidence,
                    reason=reason,
                )
            )

        resolved_ratio = len(changed_modules) / len(changed_files) if changed_files else 1.0
        tier: Literal["complete", "partial"] = (
            "complete" if (resolved_ratio == 1.0 and null_in_tests == 0) else "partial"
        )

        parts: list[str] = []
        if unresolved:
            parts.append(f"{len(unresolved)} files could not be mapped to modules")
        if null_in_tests:
            parts.append(f"{null_in_tests} test imports have no source_literal")
        reasoning = "; ".join(parts) if parts else "all files resolved, all imports traced"

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

        # All source modules: files not in test paths
        all_source_modules: set[str] = set()
        for fp in self._file_paths:
            if not is_test_file(fp):
                mod = path_to_module(fp)
                if mod:
                    all_source_modules.add(mod)

        # Modules imported by test files
        # Single batch query: get all (source_literal, file_path) pairs,
        # then filter to find source_literals imported by at least one test file
        stmt = (
            select(ImportFact.source_literal, File.path)
            .join(File, ImportFact.file_id == File.id)  # type: ignore[arg-type]
            .where(ImportFact.source_literal != None)  # noqa: E711
        )
        all_import_rows = list(self._session.exec(stmt).all())

        # Collect modules that have test coverage via imports
        covered_modules: set[str] = set()
        for source_literal, importer_path in all_import_rows:
            if source_literal is None:
                continue
            if is_test_file(importer_path):
                covered_modules.add(source_literal)

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
