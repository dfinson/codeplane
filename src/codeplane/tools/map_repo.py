"""map_repo tool - Repository structure from the index.

Queries the existing index to build a mental model of the repository.
Does NOT scan the filesystem - reflects only what's indexed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sqlmodel import col, func, select

from codeplane.index._internal.ignore import IgnoreChecker, matches_glob
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
    # Pagination
    truncated: bool = False
    next_cursor: str | None = None
    total_estimate: int | None = None


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
        respect_gitignore: bool = True,
    ) -> MapRepoResult:
        """Map the repository from indexed data."""
        if include is None:
            include = ["structure", "languages", "entry_points"]

        # Build ignore checker if gitignore enabled
        ignore_checker: IgnoreChecker | None = None
        if respect_gitignore:
            ignore_checker = IgnoreChecker(self._repo_root, respect_gitignore=True)

        result = MapRepoResult()

        if "structure" in include:
            result.structure, truncated, file_count = self._build_structure(
                depth, limit, include_globs, exclude_globs, ignore_checker
            )
            result.truncated = truncated
            result.total_estimate = file_count

        if "languages" in include:
            result.languages = self._analyze_languages(
                limit, include_globs, exclude_globs, ignore_checker
            )

        if "entry_points" in include:
            result.entry_points = self._find_entry_points(
                limit, include_globs, exclude_globs, ignore_checker
            )

        if "dependencies" in include:
            result.dependencies = self._extract_dependencies(limit)

        if "test_layout" in include:
            result.test_layout = self._analyze_test_layout(
                limit, include_globs, exclude_globs, ignore_checker
            )

        if "public_api" in include:
            result.public_api = self._extract_public_api(limit)

        return result

    def _should_include_path(
        self,
        path: str,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        ignore_checker: IgnoreChecker | None,
    ) -> bool:
        """Check if a path should be included based on filters."""
        # Check gitignore
        if ignore_checker and ignore_checker.is_excluded_rel(path):
            return False

        # Check exclude globs
        if exclude_globs:
            for pattern in exclude_globs:
                if matches_glob(path, pattern):
                    return False

        # Check include globs (empty = include all)
        if include_globs:
            return any(matches_glob(path, pattern) for pattern in include_globs)

        return True

    def _build_structure(
        self,
        depth: int,
        limit: int,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        ignore_checker: IgnoreChecker | None,
    ) -> tuple[StructureInfo, bool, int]:
        """Build directory tree from indexed files.

        Returns:
            Tuple of (structure, truncated, total_file_count)
        """
        # Get all indexed file paths with line counts
        stmt = select(File.path, File.line_count)
        all_file_data = list(self._session.exec(stmt).all())

        # Filter based on globs and gitignore
        filtered_paths: list[tuple[str, int | None]] = []
        for path, line_count in all_file_data:
            if self._should_include_path(path, include_globs, exclude_globs, ignore_checker):
                filtered_paths.append((path, line_count))

        total_count = len(filtered_paths)
        truncated = total_count > limit

        # Apply limit
        limited_paths = filtered_paths[:limit]
        path_to_lines: dict[str, int | None] = dict(limited_paths)

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
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        ignore_checker: IgnoreChecker | None,
    ) -> list[LanguageStats]:
        """Analyze language distribution from File.language_family."""
        # Get all files with language info
        stmt = select(File.path, File.language_family).where(col(File.language_family).isnot(None))
        results = list(self._session.exec(stmt).all())

        # Filter and count
        lang_counts: dict[str, int] = {}
        for path, lang in results:
            if not self._should_include_path(path, include_globs, exclude_globs, ignore_checker):
                continue
            lang_str = lang or "unknown"
            lang_counts[lang_str] = lang_counts.get(lang_str, 0) + 1

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
        ignore_checker: IgnoreChecker | None,
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
            if not self._should_include_path(path, include_globs, exclude_globs, ignore_checker):
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
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        ignore_checker: IgnoreChecker | None,
    ) -> TestLayout:
        """Analyze test files from indexed paths."""
        # Find files with test patterns
        test_patterns = ("test_", "_test.py", "tests/", "test/")

        stmt = select(File.path)
        all_paths = list(self._session.exec(stmt).all())

        test_files: list[str] = []
        for path in all_paths:
            if not self._should_include_path(path, include_globs, exclude_globs, ignore_checker):
                continue
            path_lower = path.lower()
            if any(pattern in path_lower for pattern in test_patterns):
                test_files.append(path)
                if len(test_files) >= limit:
                    break

        return TestLayout(
            test_files=sorted(test_files),
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
