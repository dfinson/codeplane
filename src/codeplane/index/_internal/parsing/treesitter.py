"""Tree-sitter parsing for syntactic analysis.

This module provides Tree-sitter parsing for:
- Local symbol extraction (functions, classes, methods, variables)
- Identifier occurrence tracking (where identifiers appear)
- Scope extraction (lexical scopes for binding resolution)
- Import extraction (import statements for cross-file refs)
- Interface hash computation (for dependency change detection)
- Probe validation (does this file parse correctly?)

Note: "identifier_occurrences" != "references". At the syntactic layer,
we only know "an identifier named X appears at line Y". Semantic resolution
(which definition does this refer to?) requires additional analysis.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tree_sitter
from tree_sitter import Query as _TSQuery
from tree_sitter import QueryCursor as _TSQueryCursor

from codeplane.index._internal.parsing.packs import (
    PACKS,
    LanguagePack,
    SymbolQueryConfig,
    get_pack,
    get_pack_for_ext,
    get_pack_for_filename,
)

# Derive LANGUAGE_MAP from packs — includes aliases like shell→bash
LANGUAGE_MAP: dict[str, str] = {key: pack.grammar_name for key, pack in PACKS.items()}

if TYPE_CHECKING:
    pass


@dataclass
class SyntacticScope:
    """A lexical scope extracted via Tree-sitter parsing."""

    scope_id: int  # Local ID within file (assigned by extractor)
    parent_scope_id: int | None  # Parent scope ID (None for file scope)
    kind: str  # file, class, function, block, comprehension, lambda
    start_line: int
    start_col: int
    end_line: int
    end_col: int


@dataclass
class SyntacticImport:
    """An import statement extracted via Tree-sitter parsing."""

    import_uid: str  # Unique ID (computed from file + line + name)
    imported_name: str  # Name being imported
    alias: str | None  # Local alias (None if no alias)
    source_literal: str | None  # Module path string (if extractable)
    import_kind: str  # python_import, python_from, js_import, etc.
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    scope_id: int | None = None  # Scope where import is visible


@dataclass
class SyntacticBind:
    """A local binding extracted via Tree-sitter parsing."""

    name: str  # Bound identifier name
    scope_id: int  # Scope where binding occurs
    target_kind: str  # DEF, IMPORT, UNKNOWN
    target_uid: str | None  # def_uid or import_uid
    reason_code: str  # PARAM, LOCAL_ASSIGN, DEF_IN_SCOPE, IMPORT_ALIAS, etc.
    start_line: int
    start_col: int


@dataclass
class DynamicAccess:
    """A dynamic access pattern detected via Tree-sitter parsing."""

    pattern_type: str  # bracket_access, getattr, reflect, eval, import_module
    start_line: int
    start_col: int
    extracted_literals: list[str] = field(default_factory=list)
    has_non_literal_key: bool = False


@dataclass
class SyntacticSymbol:
    """A symbol extracted via Tree-sitter parsing."""

    name: str
    kind: str  # function, class, method, variable, etc.
    line: int
    column: int
    end_line: int
    end_column: int
    signature: str | None = None
    parent_name: str | None = None  # For methods: the class name
    signature_text: str | None = None  # Raw signature text (params only)
    decorators: list[str] | None = None  # Decorator/annotation strings
    docstring: str | None = None  # First paragraph of docstring
    return_type: str | None = None  # Return type annotation text


@dataclass
class IdentifierOccurrence:
    """An identifier occurrence (not a semantic reference)."""

    name: str
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass
class ProbeValidation:
    """Result of validating a file for context probing."""

    is_valid: bool
    error_count: int
    total_nodes: int
    has_meaningful_content: bool
    error_ratio: float = 0.0


@dataclass
class ParseResult:
    """Result of parsing a file."""

    tree: Any  # Tree-sitter Tree (not serializable)
    language: str
    error_count: int
    total_nodes: int
    root_node: Any  # Tree-sitter Node
    ts_language: Any = None  # tree-sitter Language object for grammar introspection


# C# preprocessor wrapper node types that may contain declarations.
# Tree-sitter wraps code inside #if/#region blocks under these types.
_CSHARP_PREPROC_WRAPPERS = frozenset(
    {
        "preproc_if",
        "preproc_ifdef",
        "preproc_elif",
        "preproc_else",
        "preproc_region",
    }
)


def _import_uid(file_path: str, name: str, line: int) -> str:
    """Compute stable import UID."""
    return hashlib.sha256(f"{file_path}:{line}:{name}".encode()).hexdigest()[:16]


@dataclass
class TreeSitterParser:
    """
    Tree-sitter parser for syntactic analysis.

    Provides parsing and symbol extraction for multiple languages.
    Uses tree-sitter-languages for grammar bundles.

    Usage::

        parser = TreeSitterParser()

        # Parse a file
        result = parser.parse(Path("src/foo.py"), content)

        # Extract symbols
        symbols = parser.extract_symbols(result)

        # Extract identifier occurrences
        occurrences = parser.extract_identifier_occurrences(result)

        # Compute interface hash
        hash = parser.compute_interface_hash(symbols)

        # Validate for probing
        validation = parser.validate_code_file(result)
    """

    _parser: Any = field(default=None, repr=False)
    _languages: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Initialize the parser."""
        self._parser = tree_sitter.Parser()
        self._languages = {}

    def _get_language(self, lang_name: str) -> Any:
        """Get or load a Tree-sitter language.

        Uses LanguagePack metadata for module/function resolution instead
        of hard-coded special-case blocks.
        """
        if lang_name in self._languages:
            return self._languages[lang_name]

        # Find the pack for this grammar name (lang_name is grammar_name here)
        pack = self._find_pack_by_grammar(lang_name)

        if pack is not None and pack.language_func:
            # Non-standard language function (typescript, tsx, php, xml, ocaml)
            try:
                mod = importlib.import_module(pack.grammar_module)
                lang_fn = getattr(mod, pack.language_func)
                lang = tree_sitter.Language(lang_fn())
                self._languages[lang_name] = lang
                return lang
            except (ImportError, AttributeError) as err:
                raise ValueError(f"Language not available: {lang_name}") from err

        # Standard loading: module.language()
        lang_module = self._load_language_module(lang_name)
        if lang_module is None:
            raise ValueError(f"Language not available: {lang_name}")

        lang = tree_sitter.Language(lang_module.language())
        self._languages[lang_name] = lang
        return lang

    @staticmethod
    def _find_pack_by_grammar(grammar_name: str) -> LanguagePack | None:
        """Find the pack whose grammar_name matches."""
        # Fast path: name == grammar_name for most languages
        pack = get_pack(grammar_name)
        if pack is not None and pack.grammar_name == grammar_name:
            return pack
        # Slow path: search all packs (e.g. csharp -> c_sharp)
        for p in PACKS.values():
            if p.grammar_name == grammar_name:
                return p
        return None

    def _load_language_module(self, lang_name: str) -> Any:
        """Load tree-sitter language module by name.

        Uses LanguagePack metadata for module resolution.
        """
        pack = self._find_pack_by_grammar(lang_name)
        module_name = pack.grammar_module if pack is not None else None
        if module_name is None:
            return None

        try:
            return importlib.import_module(module_name)
        except ImportError:
            return None

    def parse(self, path: Path, content: bytes | None = None) -> ParseResult:
        """
        Parse a file with Tree-sitter.

        Args:
            path: Path to file (used for language detection)
            content: File content as bytes. If None, reads from path.

        Returns:
            ParseResult with tree, language, and error info.
        """
        if content is None:
            content = path.read_bytes()

        # Detect language from extension
        ext = path.suffix.lower().lstrip(".")
        language = self._detect_language_from_ext(ext)

        # Fallback: detect from filename (Makefile, Dockerfile, etc.)
        if language is None:
            language = self._detect_language_from_filename(path.name)

        if language is None:
            raise ValueError(f"Unsupported file extension: {ext}")

        pack = get_pack(language)
        ts_lang_name = pack.grammar_name if pack is not None else language
        ts_lang = self._get_language(ts_lang_name)

        self._parser.language = ts_lang
        tree = self._parser.parse(content)

        # Count errors and total nodes
        error_count = 0
        total_nodes = 0

        def count_nodes(node: Any) -> None:
            nonlocal error_count, total_nodes
            total_nodes += 1
            if node.type == "ERROR" or node.is_missing:
                error_count += 1
            for child in node.children:
                count_nodes(child)

        count_nodes(tree.root_node)

        return ParseResult(
            tree=tree,
            language=language,
            error_count=error_count,
            total_nodes=total_nodes,
            root_node=tree.root_node,
            ts_language=ts_lang,
        )

    def extract_symbols(self, result: ParseResult) -> list[SyntacticSymbol]:
        """
        Extract symbol definitions from a parse result.

        Uses tree-sitter queries for all supported languages.  Each language
        has a declarative ``SymbolQueryConfig`` (defined in
        ``packs.py``) that maps query patterns to symbol kinds.
        The unified executor processes query matches, resolves parent
        context, and extracts parameter signatures.

        Args:
            result: ParseResult from parse()

        Returns:
            List of SyntacticSymbol objects.
        """
        pack = get_pack(result.language)
        config = pack.symbol_config if pack is not None else None
        if config is not None:
            return self._extract_symbols_via_query(result.tree, result.root_node, config)
        # Generic extraction via walking for unsupported languages
        return self._extract_generic_symbols(result.root_node, result.language)

    def extract_identifier_occurrences(self, result: ParseResult) -> list[IdentifierOccurrence]:
        """
        Extract all identifier occurrences from a parse result.

        Note: These are NOT semantic references. We only know that an
        identifier with a given name appears at a given location.

        Args:
            result: ParseResult from parse()

        Returns:
            List of IdentifierOccurrence objects.
        """
        occurrences: list[IdentifierOccurrence] = []

        def walk(node: Any) -> None:
            if node.type == "identifier" or node.type.endswith("_identifier"):
                name = node.text.decode("utf-8") if node.text else ""
                if name:
                    occurrences.append(
                        IdentifierOccurrence(
                            name=name,
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )
            for child in node.children:
                walk(child)

        walk(result.root_node)
        return occurrences

    def extract_scopes(self, result: ParseResult) -> list[SyntacticScope]:
        """Extract lexical scopes from a parse result.

        Uses pack-driven scope_types for a single generic walker instead of
        per-language methods.

        Args:
            result: ParseResult from parse()

        Returns:
            List of SyntacticScope objects representing lexical scopes.
        """
        from codeplane.index._internal.parsing.packs import (
            _GENERIC_SCOPE_PATTERNS,
        )
        from codeplane.index._internal.parsing.service import (
            _extract_scopes_by_pattern,
            _extract_scopes_generic,
        )

        pack = get_pack(result.language)
        if pack is not None and pack.scope_types:
            return _extract_scopes_generic(result.root_node, pack.scope_types)
        return _extract_scopes_by_pattern(result.root_node, _GENERIC_SCOPE_PATTERNS)

    def extract_imports(self, result: ParseResult, file_path: str) -> list[SyntacticImport]:
        """Extract import statements from a parse result.

        Uses tree-sitter queries to find import container nodes, then
        per-language processors extract SyntacticImport objects.

        Args:
            result: ParseResult from parse()
            file_path: File path for UID generation

        Returns:
            List of SyntacticImport objects.
        """
        pack = get_pack(result.language)
        if pack is not None and pack.import_query is not None:
            return self._extract_imports_via_query(result.tree, result.root_node, pack, file_path)
        return []

    def _extract_imports_via_query(
        self,
        tree: Any,
        root: Any,
        pack: LanguagePack,
        file_path: str,
    ) -> list[SyntacticImport]:
        """Extract imports using a tree-sitter query to find import nodes.

        The query finds all import container nodes, then a per-language
        processor extracts SyntacticImport objects from each matched node.
        """
        assert pack.import_query is not None
        try:
            query = _TSQuery(tree.language, pack.import_query)
        except Exception:
            return []
        cursor = _TSQueryCursor(query)
        matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)

        processor = self._get_import_processor(pack.name)
        imports: list[SyntacticImport] = []
        for _pattern_idx, captures in matches:
            nodes = captures.get("import_node", [])
            for node in nodes:
                imports.extend(processor(node, file_path))
        return imports

    def _get_import_processor(self, language: str) -> Any:
        """Return the per-language import node processor."""
        _PROCESSORS: dict[str, Any] = {
            "python": self._process_python_import_node,
            "javascript": self._process_js_import_node,
            "typescript": self._process_js_import_node,
            "tsx": self._process_js_import_node,
            "go": self._process_go_import_node,
            "rust": self._process_rust_import_node,
            "java": self._process_java_import_node,
            "csharp": self._process_csharp_import_node,
            "kotlin": self._process_kotlin_import_node,
            "scala": self._process_scala_import_node,
            "ruby": self._process_ruby_import_node,
            "php": self._process_php_import_node,
            "swift": self._process_swift_import_node,
            "elixir": self._process_elixir_import_node,
            "haskell": self._process_haskell_import_node,
            "ocaml": self._process_ocaml_import_node,
            "lua": self._process_lua_import_node,
            "julia": self._process_julia_import_node,
            "c": self._process_c_import_node,
            "cpp": self._process_c_import_node,
        }
        return _PROCESSORS.get(language, lambda _n, _f: [])

    def extract_declared_module(self, result: ParseResult, file_path: str) -> str | None:
        """Extract the language-level module/package/namespace declaration.

        Uses tree-sitter queries when available, falls back to per-language
        handlers for complex cases.
        """
        pack = get_pack(result.language)

        # Try query-based extraction first
        if pack and pack.declared_module_query:
            return self._extract_declared_module_via_query(result.tree, result.root_node, pack)

        # Fall back to handler-based extraction
        lang = result.language
        root = result.root_node
        if lang == "csharp":
            return self._declared_module_csharp(root)
        elif lang == "ruby":
            return self._declared_module_ruby(root)
        elif lang == "ocaml":
            return self._declared_module_ocaml(file_path)
        return None

    def _extract_declared_module_via_query(
        self,
        tree: Any,
        root: Any,
        pack: LanguagePack,
    ) -> str | None:
        """Extract declared module using a tree-sitter query."""
        assert pack.declared_module_query is not None
        try:
            query = _TSQuery(tree.language, pack.declared_module_query)
        except Exception:
            return None
        cursor = _TSQueryCursor(query)
        matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)
        if not matches:
            return None

        # For Elixir, filter to only defmodule calls
        # (Python bindings don't auto-filter #eq? predicates)
        if pack.name == "elixir":
            for _, captures in matches:
                target_nodes = captures.get("_target", [])
                if (
                    target_nodes
                    and target_nodes[0].text
                    and target_nodes[0].text.decode("utf-8") == "defmodule"
                ):
                    module_nodes = captures.get("module_node", [])
                    if module_nodes and module_nodes[0].text:
                        return str(module_nodes[0].text.decode("utf-8"))
            return None

        module_node = matches[0][1].get("module_node", [None])[0]
        if module_node is None:
            return None

        # Per-language text extraction from the found node
        lang = pack.name
        if lang == "java":
            return self._declared_module_java_node(module_node)
        elif lang == "kotlin":
            return self._declared_module_kotlin_node(module_node)
        elif lang == "scala":
            return self._declared_module_scala_node(module_node)
        elif lang == "go":
            return self._declared_module_go_node(module_node)
        elif lang == "julia":
            # module_definition has identifier child
            for child in module_node.children:
                if child.type == "identifier" and child.text:
                    return str(child.text.decode("utf-8"))
            return None
        elif lang == "php":
            # namespace_definition has namespace_name child
            for child in module_node.children:
                if child.type == "namespace_name":
                    parts = [
                        c.text.decode("utf-8")
                        for c in child.children
                        if c.type == "name" and c.text
                    ]
                    return ".".join(parts) if parts else None
            return None

        elif lang == "haskell":
            # module node contains module_id children
            parts = [
                c.text.decode("utf-8")
                for c in module_node.children
                if c.type == "module_id" and c.text
            ]
            return ".".join(parts) if parts else None

        # Generic: try using node text directly
        return module_node.text.decode("utf-8") if module_node.text else None

    # ---- Package/module declaration extractors ----

    def _declared_module_java_node(self, node: Any) -> str | None:
        """Extract module from a package_declaration node."""
        for child in node.children:
            if child.type == "scoped_identifier":
                parts = self._extract_java_scoped_path(child)
                return ".".join(parts) if parts else None
            elif child.type == "identifier":
                return child.text.decode("utf-8") if child.text else None
        return None

    def _declared_module_kotlin_node(self, node: Any) -> str | None:
        """Extract module from a package_header node."""
        for child in node.children:
            if child.type == "qualified_identifier":
                parts = [
                    c.text.decode("utf-8")
                    for c in child.children
                    if c.type == "identifier" and c.text
                ]
                return ".".join(parts) if parts else None
        return None

    def _declared_module_scala_node(self, node: Any) -> str | None:
        """Extract module from a package_clause node."""
        for child in node.children:
            if child.type == "package_identifier":
                parts = [
                    c.text.decode("utf-8")
                    for c in child.children
                    if c.type == "identifier" and c.text
                ]
                return ".".join(parts) if parts else None
        return None

    def _declared_module_csharp(self, root: Any) -> str | None:
        """Extract namespace from C# file, handling nesting.

        Supports:
        - ``namespace Foo.Bar { ... }``  (block-scoped)
        - ``namespace Foo.Bar;``  (file-scoped, C# 10+)
        - ``namespace A { namespace B { ... } }``  (nested, concatenated)

        Uses ``node.text`` instead of filtering children because
        tree-sitter-c-sharp's ``qualified_name`` is recursively nested
        for 3+ segments (only the last segment is a direct ``identifier``
        child; earlier segments are wrapped in a sub-``qualified_name``).
        """
        parts: list[str] = []
        node: Any = root
        while True:
            found = False
            for child in node.children:
                if child.type in (
                    "namespace_declaration",
                    "file_scoped_namespace_declaration",
                ):
                    for sub in child.children:
                        if (
                            sub.type == "qualified_name"
                            and sub.text
                            or sub.type == "identifier"
                            and sub.text
                        ):
                            parts.append(sub.text.decode("utf-8"))
                            found = True
                            break
                    if found:
                        # Look for nested namespace inside declaration_list
                        for sub in child.children:
                            if sub.type == "declaration_list":
                                node = sub
                                break
                        else:
                            # file-scoped namespace has no declaration_list
                            break
                    break
            if not found:
                break
        return ".".join(parts) if parts else None

    def _declared_module_go_node(self, node: Any) -> str | None:
        """Extract module from a package_clause node."""
        for child in node.children:
            if child.type == "package_identifier":
                return child.text.decode("utf-8") if child.text else None
        return None

    def _declared_module_ruby(self, root: Any) -> str | None:
        """Extract nested `module A; module B; end; end` → 'A::B'.

        Walks the module nesting chain and builds the full constant path.
        """
        parts: list[str] = []

        def _walk_modules(node: Any) -> None:
            if node.type == "module":
                for sub in node.children:
                    if sub.type == "constant" and sub.text:
                        parts.append(sub.text.decode("utf-8"))
                    elif sub.type == "scope_resolution" and sub.text:
                        # e.g. `module A::B` in a single declaration
                        parts.append(sub.text.decode("utf-8"))
                    elif sub.type == "body_statement":
                        # Check for nested modules
                        for body_child in sub.children:
                            if body_child.type == "module":
                                _walk_modules(body_child)
                                return  # Only follow the first nesting chain

        _walk_modules(root.children[0] if root.children else root)
        return "::.".join(parts).replace("::", ".") if parts else None

    @staticmethod
    def _declared_module_ocaml(file_path: str) -> str | None:
        """Derive OCaml module name from filename.

        OCaml uses filename-based modules: each `.ml`/`.mli` file implicitly
        defines a module with the stem name, first character capitalized.

        Examples:
            ``src/array.ml`` → ``Array``
            ``src/array_intf.mli`` → ``Array_intf``
        """
        from pathlib import PurePosixPath

        stem = PurePosixPath(file_path).stem
        if not stem:
            return None
        # OCaml modules are the stem with first character capitalized
        return stem[0].upper() + stem[1:]

    def extract_dynamic_accesses(self, result: ParseResult) -> list[DynamicAccess]:
        """Extract dynamic access patterns for telemetry.

        Args:
            result: ParseResult from parse()

        Returns:
            List of DynamicAccess objects.
        """
        pack = get_pack(result.language)
        if pack is not None and pack.dynamic_query is not None:
            return self._extract_dynamic_via_query(result.tree, result.root_node, pack)
        return []

    def _extract_dynamic_via_query(
        self,
        tree: Any,
        root: Any,
        pack: LanguagePack,
    ) -> list[DynamicAccess]:
        """Extract dynamic accesses using a query to find candidate nodes."""
        assert pack.dynamic_query is not None
        try:
            query = _TSQuery(tree.language, pack.dynamic_query)
        except Exception:
            return []
        cursor = _TSQueryCursor(query)
        matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)

        processor = {
            "python": self._process_python_dynamic_node,
            "javascript": self._process_js_dynamic_node,
            "typescript": self._process_js_dynamic_node,
            "tsx": self._process_js_dynamic_node,
        }.get(pack.name, lambda _n: [])

        dynamics: list[DynamicAccess] = []
        for _idx, captures in matches:
            nodes = captures.get("dynamic_node", [])
            for node in nodes:
                dynamics.extend(processor(node))
        return dynamics

    def _process_python_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Python import node found by query."""
        imports: list[SyntacticImport] = []

        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    name = child.text.decode("utf-8") if child.text else ""
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                            imported_name=name,
                            alias=None,
                            source_literal=name,
                            import_kind="python_import",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    if name_node:
                        name = name_node.text.decode("utf-8") if name_node.text else ""
                        alias = (
                            alias_node.text.decode("utf-8")
                            if alias_node and alias_node.text
                            else None
                        )
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                                imported_name=name,
                                alias=alias,
                                source_literal=name,
                                import_kind="python_import",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )

        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            source = module_node.text.decode("utf-8") if module_node and module_node.text else None

            for child in node.children:
                if child.type == "dotted_name" and child != module_node:
                    name = child.text.decode("utf-8") if child.text else ""
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                            imported_name=name,
                            alias=None,
                            source_literal=source,
                            import_kind="python_from",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    if name_node:
                        name = name_node.text.decode("utf-8") if name_node.text else ""
                        alias = (
                            alias_node.text.decode("utf-8")
                            if alias_node and alias_node.text
                            else None
                        )
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(file_path, name, node.start_point[0] + 1),
                                imported_name=name,
                                alias=alias,
                                source_literal=source,
                                import_kind="python_from",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )
                elif child.type == "wildcard_import":
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(file_path, "*", node.start_point[0] + 1),
                            imported_name="*",
                            alias=None,
                            source_literal=source,
                            import_kind="python_from",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )

        return imports

    # ------------------------------------------------------------------
    # C# using directive and namespace extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _qualified_name_text(node: Any) -> str:
        """Extract full text of a qualified_name or identifier node."""
        if node.text:
            text: str = node.text.decode("utf-8")
            return text
        return ""

    def _process_csharp_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single C# using_directive node found by query.

        Handles three forms:
        - ``using Namespace;``  -> import_kind = csharp_using
        - ``using static Type;`` -> import_kind = csharp_using_static
        - ``using Alias = Namespace.Type;`` -> import_kind = csharp_using, alias set
        """
        imports: list[SyntacticImport] = []

        children = node.children
        has_static = any(c.type == "static" for c in children)
        has_equals = any(c.type == "=" or (c.text and c.text == b"=") for c in children)

        if has_equals:
            # Aliased using: using Alias = Namespace.Type;
            alias_node = None
            target_node = None
            found_equals = False
            for c in children:
                if c.type == "using":
                    continue
                if c.type == ";":
                    continue
                if c.type == "=" or (c.text and c.text == b"="):
                    found_equals = True
                    continue
                if not found_equals and c.type == "identifier":
                    alias_node = c
                elif found_equals and c.type in (
                    "qualified_name",
                    "identifier",
                    "generic_name",
                ):
                    target_node = c

            if alias_node and target_node:
                alias_text = self._qualified_name_text(alias_node)
                target_text = self._qualified_name_text(target_node)
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, target_text, node.start_point[0] + 1),
                        imported_name=target_text,
                        alias=alias_text,
                        source_literal=target_text,
                        import_kind="csharp_using",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )

        elif has_static:
            # Static using: using static Namespace.Type;
            target_node = None
            for c in children:
                if c.type in ("qualified_name", "identifier", "generic_name"):
                    target_node = c
            if target_node:
                target_text = self._qualified_name_text(target_node)
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, target_text, node.start_point[0] + 1),
                        imported_name=target_text,
                        alias=None,
                        source_literal=target_text,
                        import_kind="csharp_using_static",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )

        else:
            # Regular namespace using: using Namespace;
            target_node = None
            for c in children:
                if c.type in ("qualified_name", "identifier"):
                    target_node = c
                    break
            if target_node:
                target_text = self._qualified_name_text(target_node)
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, target_text, node.start_point[0] + 1),
                        imported_name=target_text,
                        alias=None,
                        source_literal=target_text,
                        import_kind="csharp_using",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )

        return imports

    def _process_go_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Go import_spec node found by query."""
        imports: list[SyntacticImport] = []
        # node is an import_spec
        path_node = None
        alias_node = None
        is_dot_import = False

        for child in node.children:
            if child.type == "interpreted_string_literal" or child.type == "raw_string_literal":
                path_node = child
            elif child.type == "package_identifier":
                alias_node = child
            elif child.type == "dot" or (child.text and child.text == b"."):
                is_dot_import = True
            elif child.type == "blank_identifier":
                alias_node = child

        if path_node:
            path_text = path_node.text.decode("utf-8").strip('"`') if path_node.text else ""
            alias_text = None
            if alias_node and alias_node.text:
                alias_text = alias_node.text.decode("utf-8")
            imported_name = "*" if is_dot_import else path_text.split("/")[-1]
            imports.append(
                SyntacticImport(
                    import_uid=_import_uid(file_path, path_text, node.start_point[0] + 1),
                    imported_name=imported_name,
                    alias=alias_text if not is_dot_import else None,
                    source_literal=path_text,
                    import_kind="go_import",
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
            )
        return imports

    def _process_rust_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Rust use_declaration node found by query."""
        imports: list[SyntacticImport] = []

        def _extract_rust_path(node: Any) -> str:
            """Extract full path from scoped_identifier or identifier."""
            if node.type == "identifier":
                text: str = node.text.decode("utf-8") if node.text else ""
                return text
            elif node.type in ("crate", "self", "super"):
                text = node.text.decode("utf-8") if node.text else node.type
                return str(text)
            elif node.type == "scoped_identifier":
                parts: list[str] = []
                for child in node.children:
                    if child.type in ("identifier", "crate", "self", "super", "scoped_identifier"):
                        parts.append(_extract_rust_path(child))
                return "::".join(p for p in parts if p)
            return ""

        def extract_use_tree(node: Any, prefix: str = "") -> list[tuple[str, str | None, bool]]:
            """Recursively extract (path, alias, is_glob) from use_tree nodes."""
            results: list[tuple[str, str | None, bool]] = []

            if node.type == "use_as_clause":
                path_node = None
                alias_node = None
                saw_as = False
                for child in node.children:
                    if child.type == "as":
                        saw_as = True
                    elif child.type in (
                        "scoped_identifier",
                        "crate",
                        "self",
                        "super",
                    ):
                        path_node = child
                    elif child.type == "identifier":
                        if saw_as:
                            alias_node = child
                        elif path_node is None:
                            path_node = child
                if path_node:
                    path = _extract_rust_path(path_node)
                    full_path = f"{prefix}::{path}" if prefix else path
                    alias = (
                        alias_node.text.decode("utf-8") if alias_node and alias_node.text else None
                    )
                    results.append((full_path, alias, False))

            elif node.type == "use_wildcard":
                for child in node.children:
                    if child.type == "scoped_identifier":
                        path = _extract_rust_path(child)
                        full_path = f"{prefix}::{path}" if prefix else path
                        results.append((full_path, None, True))
                if not results:
                    results.append((prefix, None, True))

            elif node.type == "use_list":
                for child in node.children:
                    if child.type in (
                        "use_as_clause",
                        "use_wildcard",
                        "use_list",
                        "identifier",
                        "self",
                    ):
                        sub_results = extract_use_tree(child, prefix)
                        results.extend(sub_results)

            elif node.type == "scoped_use_list":
                scope_prefix = ""
                use_list = None
                for child in node.children:
                    if child.type in ("scoped_identifier", "identifier", "crate", "self", "super"):
                        scope_prefix = _extract_rust_path(child)
                    elif child.type == "use_list":
                        use_list = child
                if use_list:
                    full_prefix = f"{prefix}::{scope_prefix}" if prefix else scope_prefix
                    results.extend(extract_use_tree(use_list, full_prefix))

            elif node.type in ("scoped_identifier", "identifier", "crate", "self", "super"):
                path = _extract_rust_path(node)
                full_path = f"{prefix}::{path}" if prefix else path
                results.append((full_path, None, False))

            return results

        # Process the use_declaration node
        for child in node.children:
            if child.type in (
                "use_as_clause",
                "use_wildcard",
                "scoped_use_list",
                "scoped_identifier",
                "identifier",
                "use_list",
            ):
                results = extract_use_tree(child)
                for path, alias, is_glob in results:
                    imported_name = "*" if is_glob else path.split("::")[-1]
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(file_path, path, node.start_point[0] + 1),
                            imported_name=imported_name,
                            alias=alias,
                            source_literal=path,
                            import_kind="rust_use",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )

        return imports

    def _process_java_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Java import_declaration node found by query."""
        imports: list[SyntacticImport] = []

        is_static = False
        is_wildcard = False
        path_parts: list[str] = []

        for child in node.children:
            if child.type == "static":
                is_static = True
            elif child.type == "scoped_identifier":
                path_parts = self._extract_java_scoped_path(child)
            elif child.type == "identifier":
                path_parts = [child.text.decode("utf-8") if child.text else ""]
            elif child.type == "asterisk":
                is_wildcard = True

        if path_parts:
            full_path = ".".join(path_parts)
            imported_name = "*" if is_wildcard else path_parts[-1]
            import_kind = "java_import_static" if is_static else "java_import"

            imports.append(
                SyntacticImport(
                    import_uid=_import_uid(file_path, full_path, node.start_point[0] + 1),
                    imported_name=imported_name,
                    alias=None,
                    source_literal=full_path,
                    import_kind=import_kind,
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
            )

        return imports

    def _extract_java_scoped_path(self, node: Any) -> list[str]:
        """Extract path parts from a Java scoped_identifier."""
        if node.type == "identifier":
            return [node.text.decode("utf-8") if node.text else ""]
        parts: list[str] = []
        for child in node.children:
            if child.type in ("scoped_identifier", "identifier"):
                parts.extend(self._extract_java_scoped_path(child))
        return parts

    def _process_kotlin_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a Kotlin import_header node found by query.

        The import_header node directly contains the qualified path, optional
        alias, and optional wildcard — there is no intermediate 'import' child.
        """
        imports: list[SyntacticImport] = []
        path_text = ""
        alias_text: str | None = None
        is_wildcard = False

        for child in node.children:
            if child.type == "qualified_identifier":
                parts: list[str] = []
                for qchild in child.children:
                    if qchild.type == "identifier" and qchild.text:
                        parts.append(qchild.text.decode("utf-8"))
                path_text = ".".join(parts)
            elif child.type == "identifier":
                # Single-segment import or the dotted-identifier node
                if not path_text:
                    raw = child.text.decode("utf-8") if child.text else ""
                    # identifier may be a dotted path (simple_identifier children)
                    sub_parts: list[str] = []
                    for sc in child.children:
                        if sc.type == "simple_identifier" and sc.text:
                            sub_parts.append(sc.text.decode("utf-8"))
                    path_text = ".".join(sub_parts) if sub_parts else raw
            elif child.type == "import_alias":
                for alias_child in child.children:
                    if alias_child.type == "simple_identifier":
                        alias_text = alias_child.text.decode("utf-8") if alias_child.text else None
            elif child.text == b"*":
                is_wildcard = True

        if path_text:
            imported_name = "*" if is_wildcard else path_text.split(".")[-1]
            imports.append(
                SyntacticImport(
                    import_uid=_import_uid(file_path, path_text, node.start_point[0] + 1),
                    imported_name=imported_name,
                    alias=alias_text,
                    source_literal=path_text,
                    import_kind="kotlin_import",
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
            )

        return imports

    def _process_ruby_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Ruby call node for require/require_relative."""
        imports: list[SyntacticImport] = []

        if node.type == "call":
            method_node = node.child_by_field_name("method")
            if method_node and method_node.text:
                method_name = method_node.text.decode("utf-8")
                if method_name in ("require", "require_relative"):
                    args_node = node.child_by_field_name("arguments")
                    if args_node:
                        for arg in args_node.children:
                            if arg.type == "string":
                                content = arg.text.decode("utf-8") if arg.text else ""
                                if (content.startswith("'") and content.endswith("'")) or (
                                    content.startswith('"') and content.endswith('"')
                                ):
                                    content = content[1:-1]

                                import_kind = (
                                    "ruby_require_relative"
                                    if method_name == "require_relative"
                                    else "ruby_require"
                                )
                                imports.append(
                                    SyntacticImport(
                                        import_uid=_import_uid(
                                            file_path, content, node.start_point[0] + 1
                                        ),
                                        imported_name=content.split("/")[-1],
                                        alias=None,
                                        source_literal=content,
                                        import_kind=import_kind,
                                        start_line=node.start_point[0] + 1,
                                        start_col=node.start_point[1],
                                        end_line=node.end_point[0] + 1,
                                        end_col=node.end_point[1],
                                    )
                                )
                                break

        return imports

    def _process_php_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single PHP namespace_use_declaration node found by query."""
        imports: list[SyntacticImport] = []

        if node.type == "namespace_use_declaration":
            for child in node.children:
                if child.type == "namespace_use_clause":
                    path_text = ""
                    alias_text: str | None = None

                    for sub in child.children:
                        if sub.type == "qualified_name":
                            path_text = self._extract_php_qualified_name(sub)
                        elif sub.type == "namespace_aliasing_clause":
                            for alias_child in sub.children:
                                if alias_child.type == "name":
                                    alias_text = (
                                        alias_child.text.decode("utf-8")
                                        if alias_child.text
                                        else None
                                    )

                    if path_text:
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(
                                    file_path, path_text, node.start_point[0] + 1
                                ),
                                imported_name=path_text.split("\\")[-1],
                                alias=alias_text,
                                source_literal=path_text,
                                import_kind="php_use",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )

        return imports

    def _extract_php_qualified_name(self, node: Any) -> str:
        """Extract qualified name from PHP qualified_name node."""
        if node.text:
            text: str = node.text.decode("utf-8")
            return text
        parts: list[str] = []
        for child in node.children:
            if child.type == "name":
                parts.append(child.text.decode("utf-8") if child.text else "")
        return "\\".join(parts)

    def _process_swift_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Swift import_declaration node found by query."""
        imports: list[SyntacticImport] = []

        if node.type == "import_declaration":
            module_parts: list[str] = []

            for child in node.children:
                if child.type == "identifier":
                    module_parts.append(child.text.decode("utf-8") if child.text else "")
                elif child.type == "import_path":
                    for path_child in child.children:
                        if path_child.type == "identifier":
                            module_parts.append(
                                path_child.text.decode("utf-8") if path_child.text else ""
                            )

            if module_parts:
                full_path = ".".join(module_parts)
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, full_path, node.start_point[0] + 1),
                        imported_name=module_parts[-1],
                        alias=None,
                        source_literal=full_path,
                        import_kind="swift_import",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )

        return imports

    def _process_scala_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Scala import_declaration node found by query."""
        imports: list[SyntacticImport] = []

        def _emit_import(
            full_path: str,
            is_wildcard: bool,
            decl_node: Any,
        ) -> None:
            """Create a SyntacticImport from an assembled dotted path."""
            if is_wildcard:
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(
                            file_path, f"{full_path}.*", decl_node.start_point[0] + 1
                        ),
                        imported_name="*",
                        alias=None,
                        source_literal=full_path,
                        import_kind="scala_import",
                        start_line=decl_node.start_point[0] + 1,
                        start_col=decl_node.start_point[1],
                        end_line=decl_node.end_point[0] + 1,
                        end_col=decl_node.end_point[1],
                    )
                )
            else:
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, full_path, decl_node.start_point[0] + 1),
                        imported_name=full_path.split(".")[-1],
                        alias=None,
                        source_literal=full_path,
                        import_kind="scala_import",
                        start_line=decl_node.start_point[0] + 1,
                        start_col=decl_node.start_point[1],
                        end_line=decl_node.end_point[0] + 1,
                        end_col=decl_node.end_point[1],
                    )
                )

        def _process_selectors(
            base_path: str,
            selectors_node: Any,
            decl_node: Any,
        ) -> None:
            """Process namespace_selectors: import com.foo.{Bar, Baz => B}."""
            for selector in selectors_node.children:
                if selector.type == "identifier":
                    name = selector.text.decode("utf-8") if selector.text else ""
                    if name:
                        full_path = f"{base_path}.{name}" if base_path else name
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(
                                    file_path, full_path, decl_node.start_point[0] + 1
                                ),
                                imported_name=name,
                                alias=None,
                                source_literal=full_path,
                                import_kind="scala_import",
                                start_line=decl_node.start_point[0] + 1,
                                start_col=decl_node.start_point[1],
                                end_line=decl_node.end_point[0] + 1,
                                end_col=decl_node.end_point[1],
                            )
                        )
                elif selector.type == "arrow_renamed_identifier":
                    idents = [c for c in selector.children if c.type == "identifier"]
                    if idents:
                        name = idents[0].text.decode("utf-8") if idents[0].text else ""
                        alias = (
                            idents[1].text.decode("utf-8")
                            if len(idents) > 1 and idents[1].text
                            else None
                        )
                        if name:
                            full_path = f"{base_path}.{name}" if base_path else name
                            imports.append(
                                SyntacticImport(
                                    import_uid=_import_uid(
                                        file_path, full_path, decl_node.start_point[0] + 1
                                    ),
                                    imported_name=name,
                                    alias=alias,
                                    source_literal=full_path,
                                    import_kind="scala_import",
                                    start_line=decl_node.start_point[0] + 1,
                                    start_col=decl_node.start_point[1],
                                    end_line=decl_node.end_point[0] + 1,
                                    end_col=decl_node.end_point[1],
                                )
                            )
                elif selector.type == "namespace_wildcard":
                    _emit_import(base_path, is_wildcard=True, decl_node=decl_node)

        def _process_import_declaration(decl_node: Any) -> None:
            """Process one import_declaration with potentially comma-separated paths."""
            groups: list[list[Any]] = []
            current: list[Any] = []
            for child in decl_node.children:
                if child.type == "import":
                    continue
                if child.type == ",":
                    if current:
                        groups.append(current)
                    current = []
                else:
                    current.append(child)
            if current:
                groups.append(current)

            for group in groups:
                if group and group[-1].type == "namespace_selectors":
                    parts = [
                        c.text.decode("utf-8")
                        for c in group[:-1]
                        if c.type == "identifier" and c.text
                    ]
                    base_path = ".".join(parts)
                    _process_selectors(base_path, group[-1], decl_node)
                elif group and group[-1].type == "namespace_wildcard":
                    parts = [
                        c.text.decode("utf-8") for c in group if c.type == "identifier" and c.text
                    ]
                    full_path = ".".join(parts)
                    if full_path:
                        _emit_import(full_path, is_wildcard=True, decl_node=decl_node)
                else:
                    parts = [
                        c.text.decode("utf-8") for c in group if c.type == "identifier" and c.text
                    ]
                    full_path = ".".join(parts)
                    if full_path:
                        _emit_import(full_path, is_wildcard=False, decl_node=decl_node)

        _process_import_declaration(node)
        return imports

    def _extract_scala_path(self, node: Any) -> str:
        """Extract qualified path from Scala stable_identifier or identifier."""
        if node.type == "identifier":
            return node.text.decode("utf-8") if node.text else ""
        parts: list[str] = []
        for child in node.children:
            if child.type in ("stable_identifier", "identifier"):
                parts.append(self._extract_scala_path(child))
        return ".".join(p for p in parts if p)

    def _extract_scala_base_path(self, node: Any) -> str:
        """Extract base path before import selectors."""
        for child in node.children:
            if child.type in ("stable_identifier", "identifier"):
                return self._extract_scala_path(child)
        return ""

    def _process_elixir_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Elixir call node for import/alias/use/require."""
        imports: list[SyntacticImport] = []

        if node.type == "call":
            target = None
            for child in node.children:
                if child.type == "identifier":
                    target = child.text.decode("utf-8") if child.text else ""
                    break
                elif child.type == "dot":
                    break

            if target in ("import", "alias", "use", "require"):
                args_node = None
                for child in node.children:
                    if child.type == "arguments":
                        args_node = child
                        break

                if args_node:
                    module_name = ""
                    alias_name: str | None = None

                    for arg in args_node.children:
                        if arg.type == "alias":
                            module_name = arg.text.decode("utf-8") if arg.text else ""
                        elif arg.type == "keywords":
                            for kw in arg.children:
                                if kw.type == "pair":
                                    key_node = kw.child_by_field_name("key")
                                    val_node = kw.child_by_field_name("value")
                                    if key_node and key_node.text == b"as" and val_node:
                                        alias_name = (
                                            val_node.text.decode("utf-8") if val_node.text else None
                                        )

                    if module_name:
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(
                                    file_path, module_name, node.start_point[0] + 1
                                ),
                                imported_name=module_name.split(".")[-1],
                                alias=alias_name,
                                source_literal=module_name,
                                import_kind="elixir_import",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )

        return imports

    def _process_haskell_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Haskell import node found by query."""
        imports: list[SyntacticImport] = []

        if node.type == "import":
            module_name = ""
            alias_name: str | None = None

            for child in node.children:
                if child.type == "qualified":
                    pass
                elif child.type == "module" or child.type == "module_id":
                    module_name = child.text.decode("utf-8") if child.text else ""
                elif child.type == "as":
                    pass
                elif child.type == "alias":
                    alias_name = child.text.decode("utf-8") if child.text else None

            if module_name:
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, module_name, node.start_point[0] + 1),
                        imported_name=module_name.split(".")[-1],
                        alias=alias_name,
                        source_literal=module_name,
                        import_kind="haskell_import",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )

        return imports

    def _process_ocaml_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single OCaml open_module or include_module node found by query."""
        imports: list[SyntacticImport] = []

        if node.type in ("open_module", "include_module"):
            module_name = ""

            for child in node.children:
                if (
                    child.type in ("module_path", "extended_module_path", "constructor_path")
                    or child.type == "constructor_name"
                ):
                    module_name = child.text.decode("utf-8") if child.text else ""

            if module_name:
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, module_name, node.start_point[0] + 1),
                        imported_name=module_name.split(".")[-1],
                        alias=None,
                        source_literal=module_name,
                        import_kind="ocaml_open",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )

        return imports

    def _process_lua_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Lua function_call node for require()."""
        imports: list[SyntacticImport] = []

        if node.type == "function_call":
            func_name = ""
            module_name = ""

            for child in node.children:
                if child.type == "identifier":
                    func_name = child.text.decode("utf-8") if child.text else ""
                    break

            if func_name == "require":
                for child in node.children:
                    if child.type == "arguments":
                        for arg in child.children:
                            if arg.type == "string":
                                module_name = arg.text.decode("utf-8") if arg.text else ""
                                module_name = module_name.strip("'\"")
                                break
                    elif child.type == "string":
                        module_name = child.text.decode("utf-8") if child.text else ""
                        module_name = module_name.strip("'\"")

                if module_name:
                    imports.append(
                        SyntacticImport(
                            import_uid=_import_uid(file_path, module_name, node.start_point[0] + 1),
                            imported_name=module_name.split(".")[-1],
                            alias=None,
                            source_literal=module_name,
                            import_kind="lua_require",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_col=node.end_point[1],
                        )
                    )

        return imports

    def _process_julia_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single Julia import/using statement node found by query."""
        imports: list[SyntacticImport] = []

        if node.type in ("import_statement", "using_statement"):
            for child in node.children:
                if child.type in ("identifier", "selected_import", "scoped_identifier"):
                    module_name = child.text.decode("utf-8") if child.text else ""
                    if module_name:
                        imports.append(
                            SyntacticImport(
                                import_uid=_import_uid(
                                    file_path, module_name, node.start_point[0] + 1
                                ),
                                imported_name=module_name.split(".")[-1].split(":")[0],
                                alias=None,
                                source_literal=module_name.split(":")[0],
                                import_kind="julia_using",
                                start_line=node.start_point[0] + 1,
                                start_col=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_col=node.end_point[1],
                            )
                        )

        return imports

    def _process_c_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single C/C++ preproc_include node found by query."""
        imports: list[SyntacticImport] = []

        if node.type == "preproc_include":
            header_name = ""

            for child in node.children:
                if child.type == "string_literal":
                    header_name = child.text.decode("utf-8") if child.text else ""
                    header_name = header_name.strip('"')
                elif child.type == "system_lib_string":
                    header_name = child.text.decode("utf-8") if child.text else ""
                    header_name = header_name.strip("<>")

            if header_name:
                imports.append(
                    SyntacticImport(
                        import_uid=_import_uid(file_path, header_name, node.start_point[0] + 1),
                        imported_name=header_name.split("/")[-1],
                        alias=None,
                        source_literal=header_name,
                        import_kind="c_include",
                        start_line=node.start_point[0] + 1,
                        start_col=node.start_point[1],
                        end_line=node.end_point[0] + 1,
                        end_col=node.end_point[1],
                    )
                )

        return imports

    def extract_csharp_namespace_types(self, root: Any) -> dict[str, list[str]]:
        """Extract namespace -> type names mapping from a C# AST.

        Handles both block-scoped and file-scoped namespace declarations,
        including nested namespace declarations with composed prefixes
        (e.g., ``namespace Outer { namespace Inner { class Foo {} } }``
        extracts ``{"Outer.Inner": ["Foo"]}``).

        Returns a dict mapping fully-qualified namespace names to lists of
        top-level type names (classes, interfaces, structs, enums) declared
        within that namespace.
        """
        _TYPE_DECLS = {
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "enum_declaration",
            "record_declaration",
            "record_struct_declaration",
        }

        ns_map: dict[str, list[str]] = {}

        def _type_names_from(declaration_list: Any, ns_name: str) -> None:
            """Collect type names from a declaration_list node, recursing into nested namespaces."""
            for child in declaration_list.children:
                if child.type in _TYPE_DECLS:
                    for sub in child.children:
                        if sub.type == "identifier":
                            ns_map.setdefault(ns_name, []).append(sub.text.decode("utf-8"))
                            break
                elif child.type == "namespace_declaration":
                    # Nested namespace: namespace Inner { ... }
                    _process_namespace(child, ns_name)
                elif child.type in _CSHARP_PREPROC_WRAPPERS:
                    # Recurse into preprocessor blocks
                    _type_names_from(child, ns_name)

        def _process_namespace(node: Any, parent_ns: str | None) -> None:
            """Process a namespace_declaration node, composing the full namespace path."""
            ns_name = None
            for child in node.children:
                if child.type in ("qualified_name", "identifier"):
                    local_ns = self._qualified_name_text(child)
                    ns_name = f"{parent_ns}.{local_ns}" if parent_ns else local_ns
                elif child.type == "declaration_list" and ns_name:
                    _type_names_from(child, ns_name)

        def _walk_for_namespaces(parent: Any, parent_ns: str | None = None) -> None:
            """Walk tree nodes, descending into preprocessor wrappers."""
            for node in parent.children:
                if node.type == "namespace_declaration":
                    # Block-scoped: namespace X.Y { class A {} }
                    _process_namespace(node, parent_ns)

                elif node.type == "file_scoped_namespace_declaration":
                    # File-scoped: namespace X.Y;
                    ns_name = None
                    for child in node.children:
                        if child.type in ("qualified_name", "identifier"):
                            ns_name = self._qualified_name_text(child)
                            break
                    if ns_name:
                        # Types are siblings in compilation_unit, not children.
                        # Scan all root-level nodes (including inside preproc wrappers).
                        _collect_file_scoped_types(root, ns_name)

                elif node.type in _CSHARP_PREPROC_WRAPPERS:
                    # Recurse into preprocessor blocks to find wrapped namespaces
                    _walk_for_namespaces(node, parent_ns)

        def _collect_file_scoped_types(parent: Any, ns_name: str) -> None:
            """Collect type declarations for file-scoped namespaces, including inside preproc blocks."""
            for sibling in parent.children:
                if sibling.type in _TYPE_DECLS:
                    for sub in sibling.children:
                        if sub.type == "identifier":
                            ns_map.setdefault(ns_name, []).append(sub.text.decode("utf-8"))
                            break
                elif sibling.type in _CSHARP_PREPROC_WRAPPERS:
                    _collect_file_scoped_types(sibling, ns_name)

        _walk_for_namespaces(root)

        return ns_map

    def _process_js_import_node(self, node: Any, file_path: str) -> list[SyntacticImport]:
        """Process a single JS/TS import node found by query."""
        imports: list[SyntacticImport] = []

        if node.type == "import_statement":
            source_node = node.child_by_field_name("source")
            source = None
            if source_node and source_node.text:
                source = source_node.text.decode("utf-8").strip("'\"")

            for child in node.children:
                if child.type == "import_clause":
                    for clause_child in child.children:
                        if clause_child.type == "identifier":
                            name = clause_child.text.decode("utf-8") if clause_child.text else ""
                            imports.append(
                                SyntacticImport(
                                    import_uid=_import_uid(
                                        file_path, name, node.start_point[0] + 1
                                    ),
                                    imported_name=name,
                                    alias=None,
                                    source_literal=source,
                                    import_kind="js_import",
                                    start_line=node.start_point[0] + 1,
                                    start_col=node.start_point[1],
                                    end_line=node.end_point[0] + 1,
                                    end_col=node.end_point[1],
                                )
                            )
                        elif clause_child.type == "named_imports":
                            for spec in clause_child.children:
                                if spec.type == "import_specifier":
                                    name_node = spec.child_by_field_name("name")
                                    alias_node = spec.child_by_field_name("alias")
                                    if name_node and name_node.text:
                                        name = name_node.text.decode("utf-8")
                                        alias = (
                                            alias_node.text.decode("utf-8")
                                            if alias_node and alias_node.text
                                            else None
                                        )
                                        imports.append(
                                            SyntacticImport(
                                                import_uid=_import_uid(
                                                    file_path, name, node.start_point[0] + 1
                                                ),
                                                imported_name=name,
                                                alias=alias,
                                                source_literal=source,
                                                import_kind="js_import",
                                                start_line=node.start_point[0] + 1,
                                                start_col=node.start_point[1],
                                                end_line=node.end_point[0] + 1,
                                                end_col=node.end_point[1],
                                            )
                                        )
                        elif clause_child.type == "namespace_import":
                            for ns_child in clause_child.children:
                                if ns_child.type == "identifier":
                                    alias = ns_child.text.decode("utf-8") if ns_child.text else ""
                                    imports.append(
                                        SyntacticImport(
                                            import_uid=_import_uid(
                                                file_path, "*", node.start_point[0] + 1
                                            ),
                                            imported_name="*",
                                            alias=alias,
                                            source_literal=source,
                                            import_kind="js_import",
                                            start_line=node.start_point[0] + 1,
                                            start_col=node.start_point[1],
                                            end_line=node.end_point[0] + 1,
                                            end_col=node.end_point[1],
                                        )
                                    )

        elif node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node and func_node.text and func_node.text.decode("utf-8") == "require":
                args_node = node.child_by_field_name("arguments")
                if args_node and args_node.children:
                    for arg in args_node.children:
                        if arg.type == "string":
                            source = arg.text.decode("utf-8").strip("'\"") if arg.text else None
                            imports.append(
                                SyntacticImport(
                                    import_uid=_import_uid(
                                        file_path,
                                        source or "require",
                                        node.start_point[0] + 1,
                                    ),
                                    imported_name=source or "require",
                                    alias=None,
                                    source_literal=source,
                                    import_kind="js_require",
                                    start_line=node.start_point[0] + 1,
                                    start_col=node.start_point[1],
                                    end_line=node.end_point[0] + 1,
                                    end_col=node.end_point[1],
                                )
                            )
                            break

        return imports

    def _process_python_dynamic_node(self, node: Any) -> list[DynamicAccess]:
        """Process a single Python dynamic-access node found by query."""
        dynamics: list[DynamicAccess] = []

        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node and func_node.type == "identifier":
                func_name = func_node.text.decode("utf-8") if func_node.text else ""
                if func_name in ("getattr", "setattr", "hasattr", "delattr"):
                    args_node = node.child_by_field_name("arguments")
                    literals: list[str] = []
                    has_dynamic = False
                    if args_node:
                        for i, arg in enumerate(args_node.children):
                            if i == 1:
                                if arg.type == "string":
                                    literal = (
                                        arg.text.decode("utf-8").strip("'\"") if arg.text else ""
                                    )
                                    literals.append(literal)
                                else:
                                    has_dynamic = True
                    dynamics.append(
                        DynamicAccess(
                            pattern_type="getattr",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            extracted_literals=literals,
                            has_non_literal_key=has_dynamic,
                        )
                    )
                elif func_name in ("eval", "exec"):
                    dynamics.append(
                        DynamicAccess(
                            pattern_type="eval",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            has_non_literal_key=True,
                        )
                    )

        elif node.type == "subscript":
            subscript_node = node.child_by_field_name("subscript")
            sub_literals: list[str] = []
            sub_has_dynamic = True
            if subscript_node and subscript_node.type == "string":
                literal = (
                    subscript_node.text.decode("utf-8").strip("'\"") if subscript_node.text else ""
                )
                sub_literals.append(literal)
                sub_has_dynamic = False
            dynamics.append(
                DynamicAccess(
                    pattern_type="bracket_access",
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    extracted_literals=sub_literals,
                    has_non_literal_key=sub_has_dynamic,
                )
            )

        return dynamics

    def _process_js_dynamic_node(self, node: Any) -> list[DynamicAccess]:
        """Process a single JS/TS dynamic-access node found by query."""
        dynamics: list[DynamicAccess] = []

        if node.type == "subscript_expression":
            index_node = node.child_by_field_name("index")
            literals: list[str] = []
            has_dynamic = True
            if index_node and index_node.type == "string":
                literal = index_node.text.decode("utf-8").strip("'\"") if index_node.text else ""
                literals.append(literal)
                has_dynamic = False
            dynamics.append(
                DynamicAccess(
                    pattern_type="bracket_access",
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    extracted_literals=literals,
                    has_non_literal_key=has_dynamic,
                )
            )

        elif node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node and func_node.type == "identifier":
                func_name = func_node.text.decode("utf-8") if func_node.text else ""
                if func_name == "eval":
                    dynamics.append(
                        DynamicAccess(
                            pattern_type="eval",
                            start_line=node.start_point[0] + 1,
                            start_col=node.start_point[1],
                            has_non_literal_key=True,
                        )
                    )

        return dynamics

    def compute_interface_hash(self, symbols: list[SyntacticSymbol]) -> str:
        """
        Compute a hash of the public interface of symbols.

        Used for dependency change detection: if a file's interface hash
        changes, dependents may need to be reindexed.

        Args:
            symbols: List of symbols from extract_symbols()

        Returns:
            SHA-256 hash of the interface signature.
        """
        # Sort symbols by name for determinism
        sorted_symbols = sorted(symbols, key=lambda s: (s.kind, s.name, s.line))

        # Build interface string
        parts: list[str] = []
        for sym in sorted_symbols:
            sig = sym.signature or ""
            parts.append(f"{sym.kind}:{sym.name}:{sig}")

        interface_str = "\n".join(parts)
        return hashlib.sha256(interface_str.encode()).hexdigest()

    def validate_code_file(self, result: ParseResult) -> ProbeValidation:
        """
        Validate a code file for context probing.

        Code families require:
        - Error nodes < 10% of total nodes
        - Has meaningful named nodes (not just comments/whitespace)

        Args:
            result: ParseResult from parse()

        Returns:
            ProbeValidation indicating if file is valid.
        """
        if result.total_nodes == 0:
            return ProbeValidation(
                is_valid=False,
                error_count=0,
                total_nodes=0,
                has_meaningful_content=False,
                error_ratio=0.0,
            )

        error_ratio = result.error_count / result.total_nodes
        has_meaningful = self._has_meaningful_nodes(result.root_node)

        # Valid if: error ratio < 10% AND has meaningful content
        is_valid = error_ratio < 0.10 and has_meaningful

        return ProbeValidation(
            is_valid=is_valid,
            error_count=result.error_count,
            total_nodes=result.total_nodes,
            has_meaningful_content=has_meaningful,
            error_ratio=error_ratio,
        )

    def validate_data_file(self, result: ParseResult) -> ProbeValidation:
        """
        Validate a data file for context probing.

        Data families require:
        - Valid tree (root has children)
        - Zero ERROR nodes

        Args:
            result: ParseResult from parse()

        Returns:
            ProbeValidation indicating if file is valid.
        """
        has_content = result.root_node is not None and len(result.root_node.children) > 0
        is_valid = has_content and result.error_count == 0

        return ProbeValidation(
            is_valid=is_valid,
            error_count=result.error_count,
            total_nodes=result.total_nodes,
            has_meaningful_content=has_content,
            error_ratio=(result.error_count / result.total_nodes if result.total_nodes > 0 else 0),
        )

    def _detect_language_from_ext(self, ext: str) -> str | None:
        """Detect language from file extension -- delegates to packs."""
        pack = get_pack_for_ext(ext)
        return pack.name if pack is not None else None

    def _detect_language_from_filename(self, filename: str) -> str | None:
        """Detect language from filename -- delegates to packs."""
        pack = get_pack_for_filename(filename)
        return pack.name if pack is not None else None

    def _has_meaningful_nodes(self, node: Any) -> bool:
        """Check if tree has meaningful (non-comment, non-whitespace) nodes."""
        meaningless_types = {
            "comment",
            "line_comment",
            "block_comment",
            "ERROR",
            "MISSING",
        }

        def check(n: Any) -> bool:
            if n.is_named and n.type not in meaningless_types:
                # Has at least one meaningful named node
                return True
            return any(check(child) for child in n.children)

        return check(node)

    # ------------------------------------------------------------------
    # Unified query-based symbol extraction
    # ------------------------------------------------------------------

    def _extract_symbols_via_query(
        self,
        tree: Any,
        root: Any,
        config: SymbolQueryConfig,
    ) -> list[SyntacticSymbol]:
        """Extract symbols using a tree-sitter query.

        This is the unified extraction path for all query-capable languages.
        Each language defines a ``SymbolQueryConfig`` with:

        - ``query_text``  — S-expression patterns with @name, @node, @params
        - ``patterns``    — ordered mapping of pattern index → SymbolPattern
        - ``container_types`` — node types that establish parent context

        The executor:
        1. Compiles and runs the query against the parse tree.
        2. Resolves parent context (parent_name) by walking ancestors.
        3. Adjusts kind (e.g. function → method) when nested.
        4. Extracts signature from @params capture.
        """
        ts_lang = tree.language
        query = _TSQuery(ts_lang, config.query_text)
        cursor = _TSQueryCursor(query)
        matches: list[tuple[int, dict[str, list[Any]]]] = cursor.matches(root)

        symbols: list[SyntacticSymbol] = []
        for pattern_idx, captures in matches:
            if pattern_idx >= len(config.patterns):
                continue  # Defensive: extra patterns (e.g. #eq? helpers)

            pattern = config.patterns[pattern_idx]

            # --- name ---
            name_nodes = captures.get("name")
            name: str = pattern.kind if not name_nodes else str(name_nodes[0].text.decode("utf-8"))

            # --- node (for position) ---
            node_list = captures.get("node")
            node = node_list[0] if node_list else (name_nodes[0] if name_nodes else None)
            if node is None:
                continue

            # --- kind + parent context ---
            kind = pattern.kind
            parent_name: str | None = None
            if config.container_types:
                parent_name = self._find_container_name(
                    node, config.container_types, config.container_name_field
                )
                if parent_name and pattern.nested_kind:
                    kind = pattern.nested_kind

            # --- signature ---
            signature = self._extract_signature(captures, node, config.params_from_children)

            # --- decorators (language-agnostic) ---
            decorators = self._extract_decorators(node)

            # --- return type ---
            return_type = self._extract_return_type(node)

            # --- docstring ---
            docstring = self._extract_docstring(node, config.body_node_types)

            symbols.append(
                SyntacticSymbol(
                    name=name,
                    kind=kind,
                    line=node.start_point[0] + 1,
                    column=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_column=node.end_point[1],
                    signature=signature,
                    parent_name=parent_name,
                    signature_text=signature,
                    decorators=decorators,
                    docstring=docstring,
                    return_type=return_type,
                )
            )

        return symbols

    @staticmethod
    def _find_container_name(
        node: Any,
        container_types: frozenset[str],
        name_field: str,
    ) -> str | None:
        """Walk ancestors to find the nearest container and return its name."""
        current = node.parent
        while current is not None:
            if current.type in container_types:
                name_node = current.child_by_field_name(name_field)
                if name_node:
                    return str(name_node.text.decode("utf-8"))
                # Some containers (e.g. Ruby module) use constant children
                for child in current.children:
                    if child.type in ("constant", "identifier", "type_identifier"):
                        return str(child.text.decode("utf-8"))
                return None
            current = current.parent
        return None

    @staticmethod
    def _extract_signature(
        captures: dict[str, list[Any]],
        node: Any,
        params_from_children: bool,
    ) -> str | None:
        """Extract signature from query captures or node children.

        Three strategies in order of priority:
        1. Use @params capture from query (most languages).
        2. Collect 'parameter' children between '(' and ')' (Swift, OCaml).
        3. Return None if no signature can be determined.
        """
        # Strategy 1: @params capture
        params_list = captures.get("params")
        if params_list:
            return str(params_list[0].text.decode("utf-8"))

        # Strategy 2: collect parameter children (e.g. Swift)
        if params_from_children:
            params: list[str] = []
            in_params = False
            for child in node.children:
                if child.type == "(":
                    in_params = True
                elif child.type == ")":
                    break
                elif in_params and child.type == "parameter":
                    params.append(child.text.decode("utf-8"))
            if params or in_params:
                return "(" + ", ".join(params) + ")"

        return None

    @staticmethod
    def _extract_decorators(node: Any) -> list[str] | None:
        """Extract decorator/annotation strings from a definition node.

        Language-agnostic strategy:
        1. Python: parent is 'decorated_definition' → collect 'decorator' children.
        2. Java/C#/Kotlin/PHP: node itself has 'modifiers' child with annotations.
        3. Rust: preceding 'attribute_item' siblings.
        4. Otherwise: return None.
        """
        decorators: list[str] = []

        # Strategy 1: Python decorated_definition parent
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            for child in parent.children:
                if child.type == "decorator":
                    decorators.append(child.text.decode("utf-8").strip())
            if decorators:
                return decorators

        # Strategy 2: Modifiers/attribute children on node itself
        # Covers Java, C#, Kotlin, Scala, PHP
        _annotation_types = frozenset(
            {
                "annotation",
                "marker_annotation",
                "attribute_list",
                "attribute",
                "single_annotation",
                "multi_annotation",
                "user_type",  # Kotlin annotations
            }
        )
        for child in node.children:
            if child.type == "modifiers":
                for mod_child in child.children:
                    if mod_child.type in _annotation_types:
                        decorators.append(mod_child.text.decode("utf-8").strip())
            elif child.type in _annotation_types:
                decorators.append(child.text.decode("utf-8").strip())

        # Strategy 3: Rust attribute_item siblings preceding the node
        if not decorators and parent is not None:
            for sibling in parent.children:
                if sibling == node:
                    break
                if sibling.type == "attribute_item":
                    decorators.append(sibling.text.decode("utf-8").strip())

        return decorators if decorators else None

    @staticmethod
    def _extract_return_type(node: Any) -> str | None:
        """Extract return type annotation from a definition node.

        Language-agnostic: checks common field names used across grammars.
        """
        # Most languages use 'return_type' or 'type' as the field name
        for field_name in ("return_type", "type"):
            type_node = node.child_by_field_name(field_name)
            if type_node is not None:
                text: str = str(type_node.text.decode("utf-8")).strip()
                # Avoid returning the whole body if 'type' matched something too big
                if len(text) < 200:
                    return text

        # Check for return type indicated by '->' or ':' followed by type
        # (TypeScript/Rust arrow return types handled by field names above)
        return None

    @staticmethod
    def _extract_docstring(
        node: Any,
        body_node_types: frozenset[str],
    ) -> str | None:
        """Extract docstring from a definition node.

        Three strategies:
        1. Python-style: body's first statement is expression_statement(string).
        2. Block comment: preceding sibling block/comment node (JSDoc, Javadoc).
        3. Line comments: consecutive preceding /// or // doc-comment siblings.
        """
        _comment_types = frozenset({"comment", "line_comment", "block_comment"})

        # Strategy 1: Python docstrings (first expression_statement > string in body)
        for child in node.children:
            if child.type in body_node_types:
                body = child
                if body.child_count > 0:
                    first = body.children[0]
                    if first.type == "expression_statement" and first.child_count > 0:
                        string_node = first.children[0]
                        if string_node.type == "string":
                            raw = string_node.text.decode("utf-8").strip()
                            # Strip triple quotes
                            for q in ('"""', "'''"):
                                if raw.startswith(q) and raw.endswith(q):
                                    raw = raw[3:-3].strip()
                                    break
                            # Take first paragraph only
                            first_para = raw.split("\n\n")[0].strip()
                            if first_para:
                                # Normalize whitespace
                                return " ".join(first_para.split())
                break  # Only check first body child

        # Strategy 2+3: Preceding sibling comment(s)
        prev = node.prev_named_sibling
        if prev is None or prev.type not in _comment_types:
            return None

        text = prev.text.decode("utf-8").strip()

        # Strategy 2: Block doc-comment (/** ... */)
        if text.startswith("/**"):
            text = text[3:]
            if text.endswith("*/"):
                text = text[:-2]
            text = text.strip()
            lines = []
            for line in text.splitlines():
                clean = line.strip().lstrip("* ").strip()
                lines.append(clean)
            full = " ".join(lines)
            first_para = full.split("\n\n")[0].strip()
            if first_para:
                return " ".join(first_para.split())

        # Strategy 3: Consecutive /// line-comments (Rust, C#, etc.)
        if text.startswith("///"):
            # Walk backward collecting all consecutive /// lines
            doc_lines: list[str] = []
            sibling = prev
            while sibling is not None and sibling.type in _comment_types:
                sib_text = sibling.text.decode("utf-8").strip()
                if sib_text.startswith("///"):
                    doc_lines.append(sib_text[3:].strip())
                    sibling = sibling.prev_named_sibling
                else:
                    break
            # Lines were collected in reverse order
            doc_lines.reverse()
            # Strip XML tags (C# style) and take meaningful content
            cleaned: list[str] = []
            for line in doc_lines:
                # Remove XML tags like <summary>, </summary>, <param>, etc.
                stripped = re.sub(r"<[^>]+>", "", line).strip()
                if stripped:
                    cleaned.append(stripped)
            full = " ".join(cleaned)
            first_para = full.split("\n\n")[0].strip()
            if first_para:
                return " ".join(first_para.split())

        return None

    def _extract_generic_symbols(self, root: Any, _language: str) -> list[SyntacticSymbol]:
        """Generic symbol extraction by walking the tree."""
        symbols: list[SyntacticSymbol] = []

        # Look for common definition patterns
        def_types = {
            "function_definition",
            "function_declaration",
            "method_definition",
            "method_declaration",
            "class_definition",
            "class_declaration",
            "struct_definition",
            "struct_item",
            "enum_definition",
            "enum_item",
            "enum_declaration",
            "interface_declaration",
            "type_declaration",
            "trait_item",
            # C# record types (SYNC: resolver.py _TYPE_KINDS)
            "record_declaration",
            "record_struct_declaration",
        }

        def walk(node: Any) -> None:
            if node.type in def_types:
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    kind = node.type.replace("_definition", "").replace("_declaration", "")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind=kind,
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )

            for child in node.children:
                walk(child)

        walk(root)
        return symbols
