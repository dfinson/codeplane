"""File-to-context routing with gating invariant.

This module implements SPEC.md §8.4.5: File-to-Context Routing.
The router assigns files to contexts, ensuring each file has
exactly one owner per language name.

Key invariants:
1. Deepest match wins (most specific context)
2. Must match include_spec
3. Must not match exclude_spec
4. One owner per name per file
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from codeplane.index._internal.discovery.membership import is_inside
from codeplane.index.models import CandidateContext, LanguageFamily

if TYPE_CHECKING:
    from codeplane.index.models import Context


@dataclass
class FileRoute:
    """Routing result for a single file."""

    file_path: str
    context_root: str | None = None
    language_family: LanguageFamily | None = None
    routed: bool = False
    reason: str = ""


@dataclass
class RoutingResult:
    """Result of routing files to contexts."""

    routes: list[FileRoute] = field(default_factory=list)
    unrouted_count: int = 0
    routed_count: int = 0


class ContextRouter:
    """
    Routes files to their owning contexts.

    Implements SPEC.md §8.4.5 routing rules:
    - Deepest context root wins
    - File must match include_spec
    - File must not match exclude_spec
    - Each file has one owner per name

    Usage::

        router = ContextRouter()
        result = router.route_files(files, contexts)

        for route in result.routes:
            if route.routed:
                print(f"{route.file_path} -> {route.context_root}")
    """

    def __init__(self) -> None:
        self._extension_to_family = self._build_extension_map()

    def _build_extension_map(self) -> dict[str, LanguageFamily]:
        """Map file extensions to language families."""
        mappings: dict[str, LanguageFamily] = {}

        ext_families: list[tuple[LanguageFamily, list[str]]] = [
            (LanguageFamily.PYTHON, [".py", ".pyi"]),
            (LanguageFamily.JAVASCRIPT, [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"]),
            (LanguageFamily.GO, [".go"]),
            (LanguageFamily.RUST, [".rs"]),
            # JVM languages
            (LanguageFamily.JAVA, [".java"]),
            (LanguageFamily.KOTLIN, [".kt", ".kts"]),
            (LanguageFamily.SCALA, [".scala", ".sc"]),
            (LanguageFamily.GROOVY, [".groovy", ".gradle"]),
            # .NET languages
            (LanguageFamily.CSHARP, [".cs"]),
            (LanguageFamily.FSHARP, [".fs", ".fsx", ".fsi"]),
            (LanguageFamily.VBNET, [".vb"]),
            (LanguageFamily.C_CPP, [".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"]),
            (LanguageFamily.OBJC, [".m", ".mm"]),
            (LanguageFamily.MATLAB, [".mlx"]),  # .m is ambiguous
            (LanguageFamily.SWIFT, [".swift"]),
            (LanguageFamily.PHP, [".php"]),
            (LanguageFamily.RUBY, [".rb"]),
            (LanguageFamily.ELIXIR, [".ex", ".exs"]),
            (LanguageFamily.HASKELL, [".hs", ".lhs"]),
            (LanguageFamily.SQL, [".sql"]),
            (LanguageFamily.TERRAFORM, [".tf", ".tfvars"]),
            (LanguageFamily.MARKDOWN, [".md", ".mdx"]),
            (LanguageFamily.JSON, [".json", ".jsonc"]),
            (LanguageFamily.YAML, [".yaml", ".yml"]),
            (LanguageFamily.TOML, [".toml"]),
            (LanguageFamily.PROTOBUF, [".proto"]),
            (LanguageFamily.GRAPHQL, [".graphql", ".gql"]),
        ]

        for family, exts in ext_families:
            for ext in exts:
                mappings[ext] = family

        return mappings

    def route_files(self, file_paths: list[str], contexts: list[CandidateContext]) -> RoutingResult:
        """
        Route files to their owning contexts.

        Args:
            file_paths: List of relative file paths
            contexts: List of validated contexts

        Returns:
            RoutingResult with routes and statistics.
        """
        result = RoutingResult()

        # Build context lookup by name
        contexts_by_family: dict[LanguageFamily, list[CandidateContext]] = {}
        for ctx in contexts:
            if ctx.language_family not in contexts_by_family:
                contexts_by_family[ctx.language_family] = []
            contexts_by_family[ctx.language_family].append(ctx)

        # Sort contexts by root depth (deepest first) for deepest-match-wins
        for family_contexts in contexts_by_family.values():
            family_contexts.sort(key=lambda c: -c.root_path.count("/"))

        # Route each file
        for file_path in file_paths:
            route = self._route_file(file_path, contexts_by_family)
            result.routes.append(route)
            if route.routed:
                result.routed_count += 1
            else:
                result.unrouted_count += 1

        return result

    def _route_file(
        self,
        file_path: str,
        contexts_by_family: dict[LanguageFamily, list[CandidateContext]],
    ) -> FileRoute:
        """Route a single file to its context."""
        # Determine file's language name
        ext = Path(file_path).suffix.lower()
        family = self._extension_to_family.get(ext)

        if family is None:
            return FileRoute(file_path=file_path, routed=False, reason=f"Unknown extension: {ext}")

        # Get contexts for this name
        family_contexts = contexts_by_family.get(family, [])
        if not family_contexts:
            return FileRoute(
                file_path=file_path,
                language_family=family,
                routed=False,
                reason=f"No contexts for family: {family.value}",
            )

        # Find deepest matching context
        for ctx in family_contexts:  # Already sorted deepest first
            if self._matches_context(file_path, ctx):
                return FileRoute(
                    file_path=file_path,
                    context_root=ctx.root_path,
                    language_family=family,
                    routed=True,
                )

        return FileRoute(
            file_path=file_path,
            language_family=family,
            routed=False,
            reason="No matching context found",
        )

    def _matches_context(self, file_path: str, ctx: CandidateContext) -> bool:
        """Check if file matches context include/exclude specs."""
        # Must be inside context root
        if not is_inside(file_path, ctx.root_path):
            return False

        # Check exclude patterns first (more specific)
        if ctx.exclude_spec:
            rel_path = self._relative_to(file_path, ctx.root_path)
            for pattern in ctx.exclude_spec:
                if self._matches_pattern(rel_path, pattern):
                    return False

        # Check include patterns
        if ctx.include_spec:
            file_name = Path(file_path).name
            file_ext = Path(file_path).suffix
            for pattern in ctx.include_spec:
                if pattern.startswith("*."):
                    # Extension pattern
                    if file_ext == pattern[1:]:
                        return True
                elif fnmatch(file_name, pattern):
                    return True
            return False

        # No include_spec means all files accepted
        return True

    def _relative_to(self, path: str, root: str) -> str:
        """Get path relative to root."""
        if root == "":
            return path
        if path == root:
            return ""
        if path.startswith(root + "/"):
            return path[len(root) + 1 :]
        return path

    def _matches_pattern(self, path: str, pattern: str) -> bool:
        """Match path against glob-like pattern."""
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            return path == prefix or path.startswith(prefix + "/")
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            return path.startswith(prefix + "/") and "/" not in path[len(prefix) + 1 :]
        return fnmatch(path, pattern)

    def file_to_context(self, file_path: str, contexts: list[Context]) -> Context | None:
        """Route a file to its owning Context from the database.

        Unlike route_files() which works with CandidateContext, this method
        works with actual Context model instances from the database.

        Args:
            file_path: Relative file path
            contexts: List of Context instances from the database

        Returns:
            The owning Context, or None if no match.
        """
        ext = Path(file_path).suffix.lower()
        name = self._extension_to_family.get(ext)
        if name is None:
            return None

        # Filter to matching name and sort by depth (deepest first)
        name_str = name.value
        matching = [c for c in contexts if c.language_family == name_str]
        matching.sort(key=lambda c: -c.root_path.count("/"))

        for ctx in matching:
            candidate = CandidateContext(
                root_path=ctx.root_path,
                language_family=name,
                include_spec=ctx.get_include_globs(),
                exclude_spec=ctx.get_exclude_globs(),
            )
            if self._matches_context(file_path, candidate):
                return ctx

        return None


def route_single_file(file_path: str, contexts: list[CandidateContext]) -> FileRoute | None:
    """
    Route a single file to its context.

    Convenience function for routing one file without full batch processing.

    Args:
        file_path: Relative file path
        contexts: List of validated contexts

    Returns:
        FileRoute if routed, None if no match.
    """
    router = ContextRouter()
    result = router.route_files([file_path], contexts)
    return result.routes[0] if result.routes and result.routes[0].routed else None
