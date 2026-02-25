"""TreeSitterService -- singleton factory + generic extraction pipeline.

Replaces scattered ``TreeSitterParser()`` instantiations across the
codebase with a single shared instance that caches loaded grammars.

Provides a **data-driven** scope extractor (one function instead of three
near-identical methods) and pack-aware dispatch for all extraction
operations.

Usage::

    from codeplane.index._internal.parsing.service import tree_sitter_service

    # Parsing
    result = tree_sitter_service.parse(Path("main.py"), content)

    # Extraction (all pack-aware)
    symbols = tree_sitter_service.extract_symbols(result)
    scopes  = tree_sitter_service.extract_scopes(result)
    imports = tree_sitter_service.extract_imports(result, file_path)
    module  = tree_sitter_service.extract_declared_module(result, file_path)
    dynamic = tree_sitter_service.extract_dynamic_accesses(result)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codeplane.index._internal.parsing.packs import (
    _GENERIC_SCOPE_PATTERNS,
    get_pack,
)
from codeplane.index._internal.parsing.treesitter import (
    DynamicAccess,
    SyntacticImport,
    SyntacticScope,
    SyntacticSymbol,
    TreeSitterParser,
)


class TreeSitterService:
    """Singleton wrapper around :class:`TreeSitterParser`.

    * Manages a single shared parser instance (with grammar caching).
    * Delegates extraction to pack-aware generic methods where possible.
    * Falls back to ``TreeSitterParser`` hand-written methods for imports,
      declared-module, and dynamic-accesses (those genuinely need method-based
      extraction for complex grammars like Rust use-trees and C# namespaces).
    """

    _instance: TreeSitterService | None = None

    def __init__(self) -> None:
        self._parser = TreeSitterParser()

    @classmethod
    def get(cls) -> TreeSitterService:
        """Return the singleton service instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (mainly for testing)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Core parser access
    # ------------------------------------------------------------------

    @property
    def parser(self) -> TreeSitterParser:
        """Direct access to the underlying parser (for cases not yet migrated)."""
        return self._parser

    def parse(self, path: Path, content: bytes | None = None) -> Any:
        """Parse a file. Returns a :class:`ParseResult`."""
        return self._parser.parse(path, content)

    # ------------------------------------------------------------------
    # Symbol extraction (delegates to TreeSitterParser -- already query-based)
    # ------------------------------------------------------------------

    def extract_symbols(self, result: Any) -> list[SyntacticSymbol]:
        """Extract symbol definitions from a parse result."""
        return self._parser.extract_symbols(result)

    def extract_identifier_occurrences(self, result: Any) -> list[Any]:
        """Extract all identifier occurrences from a parse result."""
        return self._parser.extract_identifier_occurrences(result)

    def validate_code_file(self, result: Any) -> Any:
        """Validate a code file parse result."""
        return self._parser.validate_code_file(result)

    def compute_interface_hash(self, symbols: list[SyntacticSymbol]) -> str:
        """Compute interface hash from symbols."""
        return self._parser.compute_interface_hash(symbols)

    # ------------------------------------------------------------------
    # Scope extraction -- GENERIC (replaces 3 near-identical methods)
    # ------------------------------------------------------------------

    def extract_scopes(self, result: Any) -> list[SyntacticScope]:
        """Extract lexical scopes using pack-driven scope_types.

        Instead of ``_extract_python_scopes``, ``_extract_js_scopes``,
        ``_extract_generic_scopes`` with identical structure, this uses
        one walker parameterised by the ``scope_types`` dict from the pack.
        """
        pack = get_pack(result.language)
        if pack is not None and pack.scope_types:
            return _extract_scopes_generic(result.root_node, pack.scope_types)
        # Fallback for unknown languages: pattern-match node types
        return _extract_scopes_by_pattern(result.root_node, _GENERIC_SCOPE_PATTERNS)

    # ------------------------------------------------------------------
    # Import extraction -- delegates to TreeSitterParser methods
    # ------------------------------------------------------------------

    def extract_imports(self, result: Any, file_path: str) -> list[SyntacticImport]:
        """Extract imports (delegates to hand-written per-language methods)."""
        return self._parser.extract_imports(result, file_path)

    # ------------------------------------------------------------------
    # Declared module -- delegates to TreeSitterParser methods
    # ------------------------------------------------------------------

    def extract_declared_module(self, result: Any, file_path: str) -> str | None:
        """Extract language-level module/package/namespace declaration."""
        return self._parser.extract_declared_module(result, file_path)

    # ------------------------------------------------------------------
    # Dynamic access -- delegates to TreeSitterParser methods
    # ------------------------------------------------------------------

    def extract_dynamic_accesses(self, result: Any) -> list[DynamicAccess]:
        """Extract dynamic access patterns."""
        return self._parser.extract_dynamic_accesses(result)

    # ------------------------------------------------------------------
    # C# specific
    # ------------------------------------------------------------------

    def extract_csharp_namespace_types(self, root_node: Any) -> dict[str, list[str]]:
        """Extract C# namespace-to-types mapping."""
        return self._parser.extract_csharp_namespace_types(root_node)


# ======================================================================
# Generic scope walker -- replaces 3 near-identical methods (~170 lines)
# ======================================================================


def _extract_scopes_generic(
    root: Any,
    scope_types: dict[str, str],
) -> list[SyntacticScope]:
    """Walk tree and emit scopes driven by a ``scope_types`` dict.

    Args:
        root: Tree-sitter root node.
        scope_types: Mapping from ``node.type`` -> scope kind string
                     (e.g. ``{"class_definition": "class", ...}``).

    Returns:
        Flat list of ``SyntacticScope`` with file scope at index 0.
    """
    scopes: list[SyntacticScope] = []
    scope_counter = 0

    # File scope (always id=0, parent=None)
    scopes.append(
        SyntacticScope(
            scope_id=scope_counter,
            parent_scope_id=None,
            kind="file",
            start_line=root.start_point[0] + 1,
            start_col=root.start_point[1],
            end_line=root.end_point[0] + 1,
            end_col=root.end_point[1],
        )
    )

    def walk(node: Any, parent_scope_id: int) -> None:
        nonlocal scope_counter

        kind = scope_types.get(node.type)
        if kind is not None:
            scope_counter += 1
            scopes.append(
                SyntacticScope(
                    scope_id=scope_counter,
                    parent_scope_id=parent_scope_id,
                    kind=kind,
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
            )
            for child in node.children:
                walk(child, scope_counter)
        else:
            for child in node.children:
                walk(child, parent_scope_id)

    for child in root.children:
        walk(child, 0)

    return scopes


def _extract_scopes_by_pattern(
    root: Any,
    patterns: dict[str, str],
) -> list[SyntacticScope]:
    """Fallback scope extractor: matches ``node.type`` substrings.

    Used for languages without explicit packs. Mirrors the old
    ``_extract_generic_scopes`` behaviour.
    """
    scopes: list[SyntacticScope] = []
    scope_counter = 0

    scopes.append(
        SyntacticScope(
            scope_id=scope_counter,
            parent_scope_id=None,
            kind="file",
            start_line=root.start_point[0] + 1,
            start_col=root.start_point[1],
            end_line=root.end_point[0] + 1,
            end_col=root.end_point[1],
        )
    )

    def walk(node: Any, parent_scope_id: int) -> None:
        nonlocal scope_counter

        kind: str | None = None
        for pattern, scope_kind in patterns.items():
            if pattern in node.type:
                kind = scope_kind
                break

        if kind is not None:
            scope_counter += 1
            scopes.append(
                SyntacticScope(
                    scope_id=scope_counter,
                    parent_scope_id=parent_scope_id,
                    kind=kind,
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
            )
            for child in node.children:
                walk(child, scope_counter)
        else:
            for child in node.children:
                walk(child, parent_scope_id)

    for child in root.children:
        walk(child, 0)

    return scopes


# Module-level singleton
# ======================================================================

tree_sitter_service = TreeSitterService.get()
