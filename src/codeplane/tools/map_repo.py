"""map_repo tool - Repository structure from the index.

Queries the existing index to build a mental model of the repository.
Does NOT scan the filesystem - reflects only what's indexed.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlmodel import col, func, select

from codeplane.core.languages import is_test_file
from codeplane.index._internal.ignore import matches_glob
from codeplane.index.models import (
    Context,
    DefFact,
    ExportEntry,
    ExportSurface,
    File,
    ImportFact,
    ProbeStatus,
)

if TYPE_CHECKING:
    from sqlmodel import Session

IncludeOption = Literal[
    "structure", "languages", "entry_points", "dependencies", "test_layout", "public_api"
]


@dataclass
class DirectoryNode:
    """A node in the directory tree."""

    name: str
    path: str
    is_dir: bool
    children: list[DirectoryNode] = field(default_factory=list)
    file_count: int = 0
    line_count: int | None = None  # Only for files


@dataclass
class LanguageStats:
    """Statistics for a language name."""

    language: str
    file_count: int
    percentage: float


@dataclass
class EntryPoint:
    """An entry point definition from the index."""

    path: str
    kind: str  # function, class, method
    name: str
    qualified_name: str | None


@dataclass
class IndexedDependencies:
    """Dependencies extracted from ImportFact."""

    external_modules: list[str]  # Unique source_literal values
    import_count: int


@dataclass
class TestLayout:
    """Test file layout from index."""

    test_files: list[str]
    test_count: int


@dataclass
class PublicSymbol:
    """A public API symbol from ExportEntry."""

    name: str
    def_uid: str | None
    certainty: str
    evidence: str | None


@dataclass
class StructureInfo:
    """Repository structure from indexed files."""

    root: str
    tree: list[DirectoryNode]
    file_count: int
    contexts: list[str]  # Valid context root paths


@dataclass
class MapRepoResult:
    """Result of map_repo tool."""

    structure: StructureInfo | None = None
    languages: list[LanguageStats] | None = None
    entry_points: list[EntryPoint] | None = None
    dependencies: IndexedDependencies | None = None
    test_layout: TestLayout | None = None
    public_api: list[PublicSymbol] | None = None


class RepoMapper:
    """Maps repository structure from the index."""

    def __init__(self, session: Session, repo_root: Path) -> None:
        self._session = session
        self._repo_root = repo_root

    def map(
        self,
        include: list[IncludeOption] | None = None,
        depth: int = 3,
        limit: int = 100,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        respect_gitignore: bool = True,  # deprecated, ignored
    ) -> MapRepoResult:
        """Map the repository from indexed data.

        The index is the authority: files in the File table already passed
        ignore checks during indexing.  Only user-provided include/exclude
        globs are applied at query time.  ``respect_gitignore`` is accepted
        for backward compatibility but ignored (the index already respects it).
        """
        # Deprecation warning for respect_gitignore
        if respect_gitignore is not True:
            warnings.warn(
                "respect_gitignore parameter is deprecated and ignored. "
                "The index already respects .gitignore during indexing.",
                DeprecationWarning,
                stacklevel=2,
            )
        del respect_gitignore  # Ensure it's not used accidentally

        if include is None:
            include = ["structure", "languages", "entry_points"]

        # --- single query, single filter pass (Change 2) ----------------
        needs_file_data = {"structure", "languages", "test_layout"} & set(include)
        filtered_files: list[tuple[str, str | None, int | None]] | None = None
        if needs_file_data:
            filtered_files = self._load_filtered_files(include_globs, exclude_globs)

        result = MapRepoResult()

        if "structure" in include and filtered_files is not None:
            result.structure, _truncated, _file_count = self._build_structure(
                depth, limit, filtered_files
            )

        if "languages" in include and filtered_files is not None:
            result.languages = self._analyze_languages(limit, filtered_files)

        if "entry_points" in include:
            result.entry_points = self._find_entry_points(limit, include_globs, exclude_globs)

        if "dependencies" in include:
            result.dependencies = self._extract_dependencies(limit)

        if "test_layout" in include and filtered_files is not None:
            result.test_layout = self._analyze_test_layout(limit, filtered_files)

        if "public_api" in include:
            result.public_api = self._extract_public_api(limit)

        return result

    def _should_include_path(
        self,
        path: str,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
    ) -> bool:
        """Check if a path should be included based on glob filters.

        No IgnoreChecker — the index already filtered at ingest time.
        """
        # Check exclude globs
        if exclude_globs:
            for pattern in exclude_globs:
                if matches_glob(path, pattern):
                    return False

        # Check include globs (empty = include all)
        if include_globs:
            return any(matches_glob(path, pattern) for pattern in include_globs)

        return True

    def _load_filtered_files(
        self,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
    ) -> list[tuple[str, str | None, int | None]]:
        """Single query for file data, filtered once.

        Returns list of (path, language_family, line_count) tuples.
        All sections that need File data share this result set.
        """
        stmt = select(File.path, File.language_family, File.line_count)
        rows = list(self._session.exec(stmt).all())
        return [
            (path, lang, lines)
            for path, lang, lines in rows
            if self._should_include_path(path, include_globs, exclude_globs)
        ]

    def _build_structure(
        self,
        depth: int,
        limit: int,
        filtered_files: list[tuple[str, str | None, int | None]],
    ) -> tuple[StructureInfo, bool, int]:
        """Build directory tree from pre-filtered file data.

        Returns:
            Tuple of (structure, truncated, total_file_count)
        """
        total_count = len(filtered_files)
        truncated = total_count > limit

        # Apply limit
        limited = filtered_files[:limit]
        path_to_lines: dict[str, int | None] = {path: lines for path, _, lines in limited}

        # Get valid contexts
        ctx_stmt = select(Context.root_path).where(Context.probe_status == ProbeStatus.VALID.value)
        contexts = list(self._session.exec(ctx_stmt).all())

        # Build tree
        root_node = DirectoryNode(
            name=self._repo_root.name,
            path=".",
            is_dir=True,
        )

        dir_nodes: dict[str, DirectoryNode] = {".": root_node}

        for path_str, line_count in path_to_lines.items():
            parts = Path(path_str).parts
            if len(parts) > depth + 1:
                continue

            # Ensure parent directories exist
            current_path = "."
            parent_node = root_node

            for part in parts[:-1]:
                current_path = str(Path(current_path) / part)
                if current_path not in dir_nodes:
                    node = DirectoryNode(
                        name=part,
                        path=current_path,
                        is_dir=True,
                    )
                    dir_nodes[current_path] = node
                    parent_node.children.append(node)
                parent_node = dir_nodes[current_path]

            # Add file node
            file_node = DirectoryNode(
                name=parts[-1],
                path=path_str,
                is_dir=False,
                line_count=line_count,
            )
            parent_node.children.append(file_node)
            parent_node.file_count += 1

        # Sort children
        def sort_nodes(node: DirectoryNode) -> None:
            node.children.sort(key=lambda n: (not n.is_dir, n.name.lower()))
            for child in node.children:
                if child.is_dir:
                    sort_nodes(child)

        sort_nodes(root_node)

        return (
            StructureInfo(
                root=str(self._repo_root),
                tree=root_node.children,
                file_count=len(path_to_lines),
                contexts=contexts,
            ),
            truncated,
            total_count,
        )

    def _analyze_languages(
        self,
        limit: int,
        filtered_files: list[tuple[str, str | None, int | None]],
    ) -> list[LanguageStats]:
        """Analyze language distribution from pre-filtered file data."""
        lang_counts: dict[str, int] = {}
        for _, lang, _ in filtered_files:
            if lang is not None:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

        total = sum(lang_counts.values())
        if total == 0:
            return []

        stats = [
            LanguageStats(
                language=lang,
                file_count=count,
                percentage=round(count / total * 100, 1),
            )
            for lang, count in lang_counts.items()
        ]

        return sorted(stats, key=lambda s: s.file_count, reverse=True)[:limit]

    def _find_entry_points(
        self,
        limit: int,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
    ) -> list[EntryPoint]:
        """Find entry point definitions from DefFact."""
        # Get top-level definitions (functions, classes) that look like entry points
        entry_kinds = ("function", "class", "method")
        entry_names = ("main", "cli", "app", "run", "start", "serve", "execute")

        stmt = (
            select(DefFact, File.path)
            .join(File, col(DefFact.file_id) == col(File.id))
            .where(
                col(DefFact.kind).in_(entry_kinds),
                col(DefFact.name).in_(entry_names),
            )
            .limit(limit * 2)  # Over-fetch since we filter
        )
        defs = list(self._session.exec(stmt).all())

        # Also get __main__ module definitions
        main_stmt = (
            select(DefFact, File.path)
            .join(File, col(DefFact.file_id) == col(File.id))
            .where(col(File.path).contains("__main__"))
            .limit(limit)
        )
        main_defs = list(self._session.exec(main_stmt).all())

        all_defs = defs + main_defs
        seen: set[str] = set()
        entry_points: list[EntryPoint] = []

        for d, path in all_defs:
            if d.def_uid in seen:
                continue
            if not self._should_include_path(path, include_globs, exclude_globs):
                continue
            if len(entry_points) >= limit:
                break

            seen.add(d.def_uid)
            entry_points.append(
                EntryPoint(
                    path=path,
                    kind=d.kind,
                    name=d.name,
                    qualified_name=d.qualified_name,
                )
            )

        return entry_points

    def _extract_dependencies(self, limit: int = 100) -> IndexedDependencies:
        """Extract external dependencies from ImportFact.source_literal."""
        count_col = func.count()
        stmt = (
            select(ImportFact.source_literal, count_col)
            .where(col(ImportFact.source_literal).isnot(None))
            .group_by(ImportFact.source_literal)
            .order_by(count_col.desc())
            .limit(limit)
        )
        results = list(self._session.exec(stmt).all())

        # Filter to likely external modules (no relative imports)
        external = [source for source, _ in results if source and not source.startswith(".")]

        total_imports = sum(count for _, count in results)

        return IndexedDependencies(
            external_modules=external,
            import_count=total_imports,
        )

    def _analyze_test_layout(
        self,
        limit: int,
        filtered_files: list[tuple[str, str | None, int | None]],
    ) -> TestLayout:
        """Analyze test files from pre-filtered file data."""
        test_files: list[str] = []
        for path, _, _ in filtered_files:
            if is_test_file(path):
                test_files.append(path)

        return TestLayout(
            test_files=sorted(test_files[:limit]),
            test_count=len(test_files),
        )

    def _extract_public_api(self, limit: int = 100) -> list[PublicSymbol]:
        """Extract public API from ExportEntry."""
        stmt = (
            select(ExportEntry)
            .join(ExportSurface, col(ExportEntry.surface_id) == col(ExportSurface.surface_id))
            .limit(limit)
        )
        entries = list(self._session.exec(stmt).all())

        return [
            PublicSymbol(
                name=e.exported_name,
                def_uid=e.def_uid,
                certainty=e.certainty,
                evidence=e.evidence_kind,
            )
            for e in entries
        ]
