"""Tree-sitter parsing for syntactic analysis.

This module provides Tree-sitter parsing for:
- Local symbol extraction (functions, classes, methods, variables)
- Identifier occurrence tracking (where identifiers appear)
- Interface hash computation (for dependency change detection)
- Probe validation (does this file parse correctly?)

Note: "identifier_occurrences" != "references". At the syntactic layer,
we only know "an identifier named X appears at line Y". Semantic resolution
(which definition does this refer to?) comes from SCIP.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


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


# Language to Tree-sitter language name mapping
LANGUAGE_MAP: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "kotlin": "kotlin",
    "scala": "scala",
    "csharp": "c_sharp",
    "fsharp": "c_sharp",  # F# uses C# grammar as fallback
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    "elixir": "elixir",
    "haskell": "haskell",
    "sql": "sql",
    "json": "json",
    "yaml": "yaml",
    "toml": "toml",
    "markdown": "markdown",
    "html": "html",
    "css": "css",
    "bash": "bash",
    "dockerfile": "dockerfile",
    "terraform": "hcl",
    "hcl": "hcl",
    "protobuf": "proto",
    "graphql": "graphql",
}


# Symbol extraction queries per language (Tree-sitter query syntax)
# These queries extract function/class/method definitions
SYMBOL_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @name) @function
        (class_definition name: (identifier) @name) @class
        (assignment left: (identifier) @name) @variable
    """,
    "javascript": """
        (function_declaration name: (identifier) @name) @function
        (class_declaration name: (identifier) @name) @class
        (method_definition name: (property_identifier) @name) @method
        (variable_declarator name: (identifier) @name) @variable
        (arrow_function) @arrow
    """,
    "typescript": """
        (function_declaration name: (identifier) @name) @function
        (class_declaration name: (type_identifier) @name) @class
        (method_definition name: (property_identifier) @name) @method
        (interface_declaration name: (type_identifier) @name) @interface
        (type_alias_declaration name: (type_identifier) @name) @type_alias
    """,
    "go": """
        (function_declaration name: (identifier) @name) @function
        (method_declaration name: (field_identifier) @name) @method
        (type_declaration (type_spec name: (type_identifier) @name)) @type
    """,
    "rust": """
        (function_item name: (identifier) @name) @function
        (impl_item type: (type_identifier) @name) @impl
        (struct_item name: (type_identifier) @name) @struct
        (enum_item name: (type_identifier) @name) @enum
        (trait_item name: (type_identifier) @name) @trait
    """,
    "java": """
        (method_declaration name: (identifier) @name) @method
        (class_declaration name: (identifier) @name) @class
        (interface_declaration name: (identifier) @name) @interface
    """,
}


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
        try:
            import tree_sitter
        except ImportError as e:
            raise ImportError("tree-sitter is required. Install with: uv add tree-sitter") from e

        self._parser = tree_sitter.Parser()
        self._languages = {}

    def _get_language(self, lang_name: str) -> Any:
        """Get or load a Tree-sitter language."""
        if lang_name in self._languages:
            return self._languages[lang_name]

        try:
            import tree_sitter_go
            import tree_sitter_javascript
            import tree_sitter_python
            import tree_sitter_rust

            # Map language names to their modules
            lang_modules = {
                "python": tree_sitter_python,
                "javascript": tree_sitter_javascript,
                "go": tree_sitter_go,
                "rust": tree_sitter_rust,
            }

            if lang_name in lang_modules:
                import tree_sitter

                lang = tree_sitter.Language(lang_modules[lang_name].language())
                self._languages[lang_name] = lang
                return lang
        except ImportError:
            pass

        raise ValueError(f"Language not available: {lang_name}")

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

        if language is None:
            raise ValueError(f"Unsupported file extension: {ext}")

        ts_lang_name = LANGUAGE_MAP.get(language, language)
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
        )

    def extract_symbols(self, result: ParseResult) -> list[SyntacticSymbol]:
        """
        Extract symbol definitions from a parse result.

        Args:
            result: ParseResult from parse()

        Returns:
            List of SyntacticSymbol objects.
        """
        symbols: list[SyntacticSymbol] = []

        # Use language-specific extraction
        if result.language == "python":
            symbols = self._extract_python_symbols(result.root_node)
        elif result.language in ("javascript", "typescript"):
            symbols = self._extract_js_symbols(result.root_node)
        elif result.language == "go":
            symbols = self._extract_go_symbols(result.root_node)
        elif result.language == "rust":
            symbols = self._extract_rust_symbols(result.root_node)
        else:
            # Generic extraction via walking
            symbols = self._extract_generic_symbols(result.root_node, result.language)

        return symbols

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
        """Detect language from file extension."""
        ext_map = {
            "py": "python",
            "pyi": "python",
            "js": "javascript",
            "jsx": "javascript",
            "mjs": "javascript",
            "cjs": "javascript",
            "ts": "typescript",
            "tsx": "tsx",
            "mts": "typescript",
            "cts": "typescript",
            "go": "go",
            "rs": "rust",
            "java": "java",
            "kt": "kotlin",
            "kts": "kotlin",
            "scala": "scala",
            "sc": "scala",
            "cs": "csharp",
            "fs": "fsharp",
            "fsx": "fsharp",
            "rb": "ruby",
            "php": "php",
            "swift": "swift",
            "ex": "elixir",
            "exs": "elixir",
            "hs": "haskell",
            "tf": "terraform",
            "hcl": "hcl",
            "sql": "sql",
            "md": "markdown",
            "json": "json",
            "yaml": "yaml",
            "yml": "yaml",
            "toml": "toml",
            "proto": "protobuf",
            "graphql": "graphql",
            "gql": "graphql",
            "nix": "nix",
            "sh": "bash",
            "bash": "bash",
            "html": "html",
            "css": "css",
        }
        return ext_map.get(ext)

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

    def _extract_python_symbols(self, root: Any) -> list[SyntacticSymbol]:
        """Extract symbols from Python AST."""
        symbols: list[SyntacticSymbol] = []
        current_class: str | None = None

        def walk(node: Any, class_name: str | None = None) -> None:
            nonlocal current_class

            if node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="class",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )
                    # Walk children with class context
                    for child in node.children:
                        walk(child, name)
                    return

            elif node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    kind = "method" if class_name else "function"

                    # Extract signature
                    params_node = node.child_by_field_name("parameters")
                    sig = params_node.text.decode("utf-8") if params_node else "()"

                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind=kind,
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                            signature=sig,
                            parent_name=class_name,
                        )
                    )

            for child in node.children:
                walk(child, class_name)

        walk(root)
        return symbols

    def _extract_js_symbols(self, root: Any) -> list[SyntacticSymbol]:
        """Extract symbols from JavaScript/TypeScript AST."""
        symbols: list[SyntacticSymbol] = []

        def walk(node: Any, class_name: str | None = None) -> None:
            if node.type in ("function_declaration", "function"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="function",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )

            elif node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="class",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )
                    for child in node.children:
                        walk(child, name)
                    return

            elif node.type == "method_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="method",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                            parent_name=class_name,
                        )
                    )

            for child in node.children:
                walk(child, class_name)

        walk(root)
        return symbols

    def _extract_go_symbols(self, root: Any) -> list[SyntacticSymbol]:
        """Extract symbols from Go AST."""
        symbols: list[SyntacticSymbol] = []

        def walk(node: Any) -> None:
            if node.type == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="function",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )

            elif node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="method",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )

            elif node.type == "type_declaration":
                for child in node.children:
                    if child.type == "type_spec":
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            name = name_node.text.decode("utf-8")
                            symbols.append(
                                SyntacticSymbol(
                                    name=name,
                                    kind="type",
                                    line=child.start_point[0] + 1,
                                    column=child.start_point[1],
                                    end_line=child.end_point[0] + 1,
                                    end_column=child.end_point[1],
                                )
                            )

            for child in node.children:
                walk(child)

        walk(root)
        return symbols

    def _extract_rust_symbols(self, root: Any) -> list[SyntacticSymbol]:
        """Extract symbols from Rust AST."""
        symbols: list[SyntacticSymbol] = []

        def walk(node: Any) -> None:
            if node.type == "function_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="function",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )

            elif node.type == "struct_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="struct",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )

            elif node.type == "enum_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="enum",
                            line=node.start_point[0] + 1,
                            column=node.start_point[1],
                            end_line=node.end_point[0] + 1,
                            end_column=node.end_point[1],
                        )
                    )

            elif node.type == "trait_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8")
                    symbols.append(
                        SyntacticSymbol(
                            name=name,
                            kind="trait",
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
            "interface_declaration",
            "type_declaration",
            "trait_item",
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
