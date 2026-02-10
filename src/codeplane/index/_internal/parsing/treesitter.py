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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tree_sitter

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


# Language to Tree-sitter language name mapping
# Maps our internal names to tree-sitter grammar module names
LANGUAGE_MAP: dict[str, str] = {
    # Core/mainstream
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
    "c": "c",
    "cpp": "cpp",
    "ruby": "ruby",
    "php": "php",
    "swift": "swift",
    # Functional
    "elixir": "elixir",
    "haskell": "haskell",
    "ocaml": "ocaml",
    # Scripting
    "bash": "bash",
    "shell": "bash",
    "lua": "lua",
    "julia": "julia",
    # Systems
    "zig": "zig",
    "ada": "ada",
    "fortran": "fortran",
    "odin": "odin",
    # Web
    "html": "html",
    "css": "css",
    "xml": "xml",
    # Hardware
    "verilog": "verilog",
    # Data/Config
    "json": "json",
    "yaml": "yaml",
    "toml": "toml",
    "dockerfile": "dockerfile",
    "hcl": "hcl",
    "terraform": "hcl",
    "sql": "sql",
    "graphql": "graphql",
    "makefile": "make",
    "make": "make",
    "markdown": "markdown",
    "regex": "regex",
    "requirements": "requirements",
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
    "scala": """
        (function_definition name: (identifier) @name) @function
        (class_definition name: (identifier) @name) @class
        (trait_definition name: (identifier) @name) @trait
        (object_definition name: (identifier) @name) @object
    """,
    "c_sharp": """
        (method_declaration name: (identifier) @name) @method
        (class_declaration name: (identifier) @name) @class
        (interface_declaration name: (identifier) @name) @interface
        (struct_declaration name: (identifier) @name) @struct
        (enum_declaration name: (identifier) @name) @enum
        (record_declaration name: (identifier) @name) @record
        (record_struct_declaration name: (identifier) @name) @record_struct
    """,
    "c": """
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @function
        (struct_specifier name: (type_identifier) @name) @struct
        (enum_specifier name: (type_identifier) @name) @enum
    """,
    "cpp": """
        (function_definition declarator: (function_declarator declarator: (identifier) @name)) @function
        (class_specifier name: (type_identifier) @name) @class
        (struct_specifier name: (type_identifier) @name) @struct
    """,
    "ruby": """
        (method name: (identifier) @name) @method
        (class name: (constant) @name) @class
        (module name: (constant) @name) @module
    """,
    "php": """
        (function_definition name: (name) @name) @function
        (class_declaration name: (name) @name) @class
        (method_declaration name: (name) @name) @method
    """,
    "swift": """
        (function_declaration name: (simple_identifier) @name) @function
        (class_declaration name: (type_identifier) @name) @class
        (protocol_declaration name: (type_identifier) @name) @protocol
    """,
    "haskell": """
        (function name: (variable) @name) @function
        (type_alias name: (type) @name) @type
    """,
    "lua": """
        (function_declaration name: (identifier) @name) @function
    """,
    "bash": """
        (function_definition name: (word) @name) @function
    """,
    "sql": """
        (create_function_statement name: (identifier) @name) @function
        (create_table_statement name: (identifier) @name) @table
    """,
    # TSX - same as TypeScript with JSX support
    "tsx": """
        (function_declaration name: (identifier) @name) @function
        (class_declaration name: (type_identifier) @name) @class
        (method_definition name: (property_identifier) @name) @method
        (interface_declaration name: (type_identifier) @name) @interface
        (type_alias_declaration name: (type_identifier) @name) @type_alias
    """,
    # Julia - functions and types
    "julia": """
        (function_definition name: (identifier) @name) @function
        (short_function_definition name: (identifier) @name) @function
        (struct_definition name: (identifier) @name) @struct
        (abstract_definition name: (identifier) @name) @abstract
        (macro_definition name: (identifier) @name) @macro
    """,
    # JSON - top-level keys as "symbols"
    "json": """
        (pair key: (string) @name) @property
    """,
    # HTML - elements with id/class attributes
    "html": """
        (element (start_tag (tag_name) @name)) @element
    """,
    # CSS - selectors and rules
    "css": """
        (rule_set (selectors (class_selector (class_name) @name))) @class
        (rule_set (selectors (id_selector (id_name) @name))) @id
    """,
    # Dockerfile - instructions
    "dockerfile": """
        (from_instruction) @from
        (run_instruction) @run
        (cmd_instruction) @cmd
        (label_instruction) @label
        (expose_instruction) @expose
        (env_instruction) @env
        (copy_instruction) @copy
        (entrypoint_instruction) @entrypoint
    """,
    # HCL/Terraform - blocks and resources
    "hcl": """
        (block (identifier) @type (string_lit)? @name) @block
    """,
    # Makefile - targets
    "make": """
        (rule (targets (word) @name)) @target
    """,
    # Markdown - headings
    "markdown": """
        (atx_heading (atx_h1_marker) (inline) @name) @h1
        (atx_heading (atx_h2_marker) (inline) @name) @h2
        (atx_heading (atx_h3_marker) (inline) @name) @h3
    """,
    # Requirements.txt - package names
    "requirements": """
        (requirement (package) @name) @package
    """,
    # TOML - tables and keys
    "toml": """
        (table (bare_key) @name) @table
        (pair (bare_key) @name) @key
    """,
    # XML - elements
    "xml": """
        (element (STag (Name) @name)) @element
        (element (EmptyElemTag (Name) @name)) @element
    """,
    # YAML - keys
    "yaml": """
        (block_mapping_pair key: (flow_node) @name) @mapping
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
        self._parser = tree_sitter.Parser()
        self._languages = {}

    def _get_language(self, lang_name: str) -> Any:
        """Get or load a Tree-sitter language."""
        if lang_name in self._languages:
            return self._languages[lang_name]

        # Special handling for languages with non-standard function names
        if lang_name in ("typescript", "tsx"):
            try:
                ts_module = importlib.import_module("tree_sitter_typescript")
                if lang_name == "typescript":
                    lang = tree_sitter.Language(ts_module.language_typescript())
                else:
                    lang = tree_sitter.Language(ts_module.language_tsx())
                self._languages[lang_name] = lang
                return lang
            except ImportError as err:
                raise ValueError(f"Language not available: {lang_name}") from err

        if lang_name == "xml":
            try:
                xml_module = importlib.import_module("tree_sitter_xml")
                lang = tree_sitter.Language(xml_module.language_xml())
                self._languages[lang_name] = lang
                return lang
            except ImportError as err:
                raise ValueError(f"Language not available: {lang_name}") from err

        if lang_name == "php":
            try:
                php_module = importlib.import_module("tree_sitter_php")
                lang = tree_sitter.Language(php_module.language_php())
                self._languages[lang_name] = lang
                return lang
            except ImportError as err:
                raise ValueError(f"Language not available: {lang_name}") from err

        # Standard loading for other languages
        lang_module = self._load_language_module(lang_name)
        if lang_module is None:
            raise ValueError(f"Language not available: {lang_name}")

        lang = tree_sitter.Language(lang_module.language())
        self._languages[lang_name] = lang
        return lang

    def _load_language_module(self, lang_name: str) -> Any:
        """Load tree-sitter language module by name."""
        # Map grammar names to their import paths
        # Must match all languages in GRAMMAR_PACKAGES in grammars.py
        import_map: dict[str, str] = {
            # Core/mainstream
            "python": "tree_sitter_python",
            "javascript": "tree_sitter_javascript",
            "go": "tree_sitter_go",
            "rust": "tree_sitter_rust",
            "java": "tree_sitter_java",
            "kotlin": "tree_sitter_kotlin",
            "scala": "tree_sitter_scala",
            "c_sharp": "tree_sitter_c_sharp",
            "c": "tree_sitter_c",
            "cpp": "tree_sitter_cpp",
            "ruby": "tree_sitter_ruby",
            "php": "tree_sitter_php",
            "swift": "tree_sitter_swift",
            # Functional
            "elixir": "tree_sitter_elixir",
            "haskell": "tree_sitter_haskell",
            "ocaml": "tree_sitter_ocaml",
            # Scripting
            "bash": "tree_sitter_bash",
            "lua": "tree_sitter_lua",
            "julia": "tree_sitter_julia",
            # Systems
            "zig": "tree_sitter_zig",
            "ada": "tree_sitter_ada",
            "fortran": "tree_sitter_fortran",
            "odin": "tree_sitter_odin",
            # Web
            "html": "tree_sitter_html",
            "css": "tree_sitter_css",
            "xml": "tree_sitter_xml",
            # Hardware
            "verilog": "tree_sitter_verilog",
            # Data/Config
            "json": "tree_sitter_json",
            "yaml": "tree_sitter_yaml",
            "toml": "tree_sitter_toml",
            "dockerfile": "tree_sitter_dockerfile",
            "hcl": "tree_sitter_hcl",
            "sql": "tree_sitter_sql",
            "graphql": "tree_sitter_graphql",
            "make": "tree_sitter_make",
            "markdown": "tree_sitter_markdown",
            "regex": "tree_sitter_regex",
            "requirements": "tree_sitter_requirements",
        }

        module_name = import_map.get(lang_name)
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

    def extract_scopes(self, result: ParseResult) -> list[SyntacticScope]:
        """Extract lexical scopes from a parse result.

        Args:
            result: ParseResult from parse()

        Returns:
            List of SyntacticScope objects representing lexical scopes.
        """
        if result.language == "python":
            return self._extract_python_scopes(result.root_node)
        elif result.language in ("javascript", "typescript", "tsx"):
            return self._extract_js_scopes(result.root_node)
        else:
            return self._extract_generic_scopes(result.root_node)

    def extract_imports(self, result: ParseResult, file_path: str) -> list[SyntacticImport]:
        """Extract import statements from a parse result.

        Args:
            result: ParseResult from parse()
            file_path: File path for UID generation

        Returns:
            List of SyntacticImport objects.
        """
        if result.language == "python":
            return self._extract_python_imports(result.root_node, file_path)
        elif result.language in ("javascript", "typescript", "tsx"):
            return self._extract_js_imports(result.root_node, file_path)
        elif result.language == "csharp":
            return self._extract_csharp_imports(result.root_node, file_path)
        else:
            return []

    def extract_dynamic_accesses(self, result: ParseResult) -> list[DynamicAccess]:
        """Extract dynamic access patterns for telemetry.

        Args:
            result: ParseResult from parse()

        Returns:
            List of DynamicAccess objects.
        """
        if result.language == "python":
            return self._extract_python_dynamic(result.root_node)
        elif result.language in ("javascript", "typescript", "tsx"):
            return self._extract_js_dynamic(result.root_node)
        else:
            return []

    def _extract_python_scopes(self, root: Any) -> list[SyntacticScope]:
        """Extract scopes from Python AST."""
        scopes: list[SyntacticScope] = []
        scope_counter = 0

        # File scope is implicit (scope_id=0)
        file_scope = SyntacticScope(
            scope_id=scope_counter,
            parent_scope_id=None,
            kind="file",
            start_line=root.start_point[0] + 1,
            start_col=root.start_point[1],
            end_line=root.end_point[0] + 1,
            end_col=root.end_point[1],
        )
        scopes.append(file_scope)

        def walk(node: Any, parent_scope_id: int) -> None:
            nonlocal scope_counter

            scope_types = {
                "class_definition": "class",
                "function_definition": "function",
                "lambda": "lambda",
                "list_comprehension": "comprehension",
                "set_comprehension": "comprehension",
                "dictionary_comprehension": "comprehension",
                "generator_expression": "comprehension",
            }

            if node.type in scope_types:
                scope_counter += 1
                scope = SyntacticScope(
                    scope_id=scope_counter,
                    parent_scope_id=parent_scope_id,
                    kind=scope_types[node.type],
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
                scopes.append(scope)
                for child in node.children:
                    walk(child, scope_counter)
            else:
                for child in node.children:
                    walk(child, parent_scope_id)

        for child in root.children:
            walk(child, 0)

        return scopes

    def _extract_js_scopes(self, root: Any) -> list[SyntacticScope]:
        """Extract scopes from JavaScript/TypeScript AST."""
        scopes: list[SyntacticScope] = []
        scope_counter = 0

        # File scope
        file_scope = SyntacticScope(
            scope_id=scope_counter,
            parent_scope_id=None,
            kind="file",
            start_line=root.start_point[0] + 1,
            start_col=root.start_point[1],
            end_line=root.end_point[0] + 1,
            end_col=root.end_point[1],
        )
        scopes.append(file_scope)

        def walk(node: Any, parent_scope_id: int) -> None:
            nonlocal scope_counter

            scope_types = {
                "class_declaration": "class",
                "class_expression": "class",
                "function_declaration": "function",
                "function_expression": "function",
                "arrow_function": "function",
                "method_definition": "function",
                "for_statement": "block",
                "for_in_statement": "block",
                "while_statement": "block",
                "if_statement": "block",
                "statement_block": "block",
            }

            if node.type in scope_types:
                scope_counter += 1
                scope = SyntacticScope(
                    scope_id=scope_counter,
                    parent_scope_id=parent_scope_id,
                    kind=scope_types[node.type],
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
                scopes.append(scope)
                for child in node.children:
                    walk(child, scope_counter)
            else:
                for child in node.children:
                    walk(child, parent_scope_id)

        for child in root.children:
            walk(child, 0)

        return scopes

    def _extract_generic_scopes(self, root: Any) -> list[SyntacticScope]:
        """Extract scopes generically by looking for common scope patterns."""
        scopes: list[SyntacticScope] = []
        scope_counter = 0

        # File scope
        file_scope = SyntacticScope(
            scope_id=scope_counter,
            parent_scope_id=None,
            kind="file",
            start_line=root.start_point[0] + 1,
            start_col=root.start_point[1],
            end_line=root.end_point[0] + 1,
            end_col=root.end_point[1],
        )
        scopes.append(file_scope)

        scope_type_patterns = {
            "class": "class",
            "function": "function",
            "method": "function",
            "block": "block",
            "lambda": "lambda",
        }

        def walk(node: Any, parent_scope_id: int) -> None:
            nonlocal scope_counter

            # Check if node type contains any scope-indicating patterns
            kind: str | None = None
            for pattern, scope_kind in scope_type_patterns.items():
                if pattern in node.type:
                    kind = scope_kind
                    break

            if kind is not None:
                scope_counter += 1
                scope = SyntacticScope(
                    scope_id=scope_counter,
                    parent_scope_id=parent_scope_id,
                    kind=kind,
                    start_line=node.start_point[0] + 1,
                    start_col=node.start_point[1],
                    end_line=node.end_point[0] + 1,
                    end_col=node.end_point[1],
                )
                scopes.append(scope)
                for child in node.children:
                    walk(child, scope_counter)
            else:
                for child in node.children:
                    walk(child, parent_scope_id)

        for child in root.children:
            walk(child, 0)

        return scopes

    def _extract_python_imports(self, root: Any, file_path: str) -> list[SyntacticImport]:
        """Extract imports from Python AST."""
        imports: list[SyntacticImport] = []

        def make_uid(name: str, line: int) -> str:
            raw = f"{file_path}:{line}:{name}"
            return hashlib.sha256(raw.encode()).hexdigest()[:16]

        def walk(node: Any) -> None:
            # import foo, import foo as bar
            if node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        name = child.text.decode("utf-8") if child.text else ""
                        imports.append(
                            SyntacticImport(
                                import_uid=make_uid(name, node.start_point[0] + 1),
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
                                    import_uid=make_uid(name, node.start_point[0] + 1),
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

            # from foo import bar, from foo import bar as baz
            elif node.type == "import_from_statement":
                module_node = node.child_by_field_name("module_name")
                source = (
                    module_node.text.decode("utf-8") if module_node and module_node.text else None
                )

                for child in node.children:
                    if child.type == "dotted_name" and child != module_node:
                        name = child.text.decode("utf-8") if child.text else ""
                        imports.append(
                            SyntacticImport(
                                import_uid=make_uid(name, node.start_point[0] + 1),
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
                                    import_uid=make_uid(name, node.start_point[0] + 1),
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
                        # from X import * — namespace-level wildcard import
                        imports.append(
                            SyntacticImport(
                                import_uid=make_uid("*", node.start_point[0] + 1),
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

            for child in node.children:
                walk(child)

        walk(root)
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

    def _extract_csharp_imports(self, root: Any, file_path: str) -> list[SyntacticImport]:
        """Extract using directives from C# AST.

        Handles three forms:
        - ``using Namespace;``  -> import_kind = csharp_using
        - ``using static Type;`` -> import_kind = csharp_using_static
        - ``using Alias = Namespace.Type;`` -> import_kind = csharp_using, alias set
        """
        imports: list[SyntacticImport] = []

        def make_uid(name: str, line: int) -> str:
            raw = f"{file_path}:{line}:{name}"
            return hashlib.sha256(raw.encode()).hexdigest()[:16]

        def _walk_for_usings(parent: Any) -> None:
            """Walk tree nodes, descending into namespaces and preprocessor wrappers.

            C# allows `using` directives inside namespace declarations, so we must
            recurse into namespace_declaration and declaration_list nodes.
            """
            _DESCEND_INTO = _CSHARP_PREPROC_WRAPPERS | {
                "namespace_declaration",
                "declaration_list",
            }
            for node in parent.children:
                if node.type in _DESCEND_INTO:
                    _walk_for_usings(node)
                if node.type == "using_directive":
                    _process_using_directive(node)

        def _process_using_directive(node: Any) -> None:
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
                            import_uid=make_uid(target_text, node.start_point[0] + 1),
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
                            import_uid=make_uid(target_text, node.start_point[0] + 1),
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
                            import_uid=make_uid(target_text, node.start_point[0] + 1),
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

        _walk_for_usings(root)
        return imports

    def extract_csharp_namespace_types(self, root: Any) -> dict[str, list[str]]:
        """Extract namespace -> type names mapping from a C# AST.

        Handles both block-scoped and file-scoped namespace declarations.
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

        def _type_names_from(declaration_list: Any) -> list[str]:
            """Collect type names from a declaration_list node."""
            names: list[str] = []
            for child in declaration_list.children:
                if child.type in _TYPE_DECLS:
                    for sub in child.children:
                        if sub.type == "identifier":
                            names.append(sub.text.decode("utf-8"))
                            break
            return names

        ns_map: dict[str, list[str]] = {}

        def _walk_for_namespaces(parent: Any) -> None:
            """Walk tree nodes, descending into preprocessor wrappers."""
            for node in parent.children:
                if node.type == "namespace_declaration":
                    # Block-scoped: namespace X.Y { class A {} }
                    ns_name = None
                    for child in node.children:
                        if child.type in ("qualified_name", "identifier"):
                            ns_name = self._qualified_name_text(child)
                        elif child.type == "declaration_list" and ns_name:
                            types = _type_names_from(child)
                            if types:
                                ns_map.setdefault(ns_name, []).extend(types)

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
                    _walk_for_namespaces(node)

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

    def _extract_js_imports(self, root: Any, file_path: str) -> list[SyntacticImport]:
        """Extract imports from JavaScript/TypeScript AST."""
        imports: list[SyntacticImport] = []

        def make_uid(name: str, line: int) -> str:
            raw = f"{file_path}:{line}:{name}"
            return hashlib.sha256(raw.encode()).hexdigest()[:16]

        def walk(node: Any) -> None:
            # import { foo } from 'bar', import foo from 'bar'
            if node.type == "import_statement":
                source_node = node.child_by_field_name("source")
                source = None
                if source_node and source_node.text:
                    # Strip quotes
                    source = source_node.text.decode("utf-8").strip("'\"")

                # Find imported names
                for child in node.children:
                    if child.type == "import_clause":
                        for clause_child in child.children:
                            if clause_child.type == "identifier":
                                # default import
                                name = (
                                    clause_child.text.decode("utf-8") if clause_child.text else ""
                                )
                                imports.append(
                                    SyntacticImport(
                                        import_uid=make_uid(name, node.start_point[0] + 1),
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
                                                    import_uid=make_uid(
                                                        name, node.start_point[0] + 1
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
                                # import * as foo
                                for ns_child in clause_child.children:
                                    if ns_child.type == "identifier":
                                        alias = (
                                            ns_child.text.decode("utf-8") if ns_child.text else ""
                                        )
                                        imports.append(
                                            SyntacticImport(
                                                import_uid=make_uid("*", node.start_point[0] + 1),
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

            # require() calls - const foo = require('bar')
            elif node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node and func_node.text and func_node.text.decode("utf-8") == "require":
                    args_node = node.child_by_field_name("arguments")
                    if args_node and args_node.children:
                        for arg in args_node.children:
                            if arg.type == "string":
                                source = arg.text.decode("utf-8").strip("'\"") if arg.text else None
                                # Try to find variable name from parent
                                imports.append(
                                    SyntacticImport(
                                        import_uid=make_uid(
                                            source or "require", node.start_point[0] + 1
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

            for child in node.children:
                walk(child)

        walk(root)
        return imports

    def _extract_python_dynamic(self, root: Any) -> list[DynamicAccess]:
        """Extract dynamic access patterns from Python AST."""
        dynamics: list[DynamicAccess] = []

        def walk(node: Any) -> None:
            # getattr(obj, name)
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
                                if i == 1:  # Second argument is the attribute name
                                    if arg.type == "string":
                                        literal = (
                                            arg.text.decode("utf-8").strip("'\"")
                                            if arg.text
                                            else ""
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

            # obj[key] subscript
            elif node.type == "subscript":
                subscript_node = node.child_by_field_name("subscript")
                sub_literals: list[str] = []
                sub_has_dynamic = True
                if subscript_node and subscript_node.type == "string":
                    literal = (
                        subscript_node.text.decode("utf-8").strip("'\"")
                        if subscript_node.text
                        else ""
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

            for child in node.children:
                walk(child)

        walk(root)
        return dynamics

    def _extract_js_dynamic(self, root: Any) -> list[DynamicAccess]:
        """Extract dynamic access patterns from JavaScript/TypeScript AST."""
        dynamics: list[DynamicAccess] = []

        def walk(node: Any) -> None:
            # obj[key] subscript
            if node.type == "subscript_expression":
                index_node = node.child_by_field_name("index")
                literals: list[str] = []
                has_dynamic = True
                if index_node and index_node.type == "string":
                    literal = (
                        index_node.text.decode("utf-8").strip("'\"") if index_node.text else ""
                    )
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

            # eval() calls
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

            for child in node.children:
                walk(child)

        walk(root)
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
        """Detect language from file extension."""
        ext_map = {
            # Python
            "py": "python",
            "pyi": "python",
            "pyw": "python",
            "pyx": "python",
            "pxd": "python",
            # JavaScript/TypeScript
            "js": "javascript",
            "jsx": "javascript",
            "mjs": "javascript",
            "cjs": "javascript",
            "ts": "typescript",
            "tsx": "tsx",
            "mts": "typescript",
            "cts": "typescript",
            # Go
            "go": "go",
            # Rust
            "rs": "rust",
            # JVM
            "java": "java",
            "kt": "kotlin",
            "kts": "kotlin",
            "scala": "scala",
            "sc": "scala",
            # .NET
            "cs": "csharp",
            # C/C++
            "c": "c",
            "h": "c",
            "cpp": "cpp",
            "cc": "cpp",
            "cxx": "cpp",
            "hpp": "cpp",
            "hxx": "cpp",
            "hh": "cpp",
            # Ruby
            "rb": "ruby",
            "rake": "ruby",
            # PHP
            "php": "php",
            # Swift
            "swift": "swift",
            # Functional
            "ex": "elixir",
            "exs": "elixir",
            "hs": "haskell",
            "lhs": "haskell",
            "ml": "ocaml",
            "mli": "ocaml",
            # Scripting
            "jl": "julia",
            "lua": "lua",
            "sh": "bash",
            "bash": "bash",
            "zsh": "bash",
            # Systems
            "zig": "zig",
            "adb": "ada",
            "ads": "ada",
            "f90": "fortran",
            "f95": "fortran",
            "f03": "fortran",
            "f08": "fortran",
            "odin": "odin",
            # Web
            "html": "html",
            "htm": "html",
            "css": "css",
            "xml": "xml",
            "xsl": "xml",
            "svg": "xml",
            # Hardware
            "v": "verilog",
            "sv": "verilog",
            "vhd": "verilog",
            "vhdl": "verilog",
            # Config/Data
            "json": "json",
            "yaml": "yaml",
            "yml": "yaml",
            "toml": "toml",
            "tf": "terraform",
            "tfvars": "terraform",
            "hcl": "hcl",
            "sql": "sql",
            "graphql": "graphql",
            "gql": "graphql",
            "dockerfile": "dockerfile",
            "makefile": "makefile",
            "mk": "makefile",
            "md": "markdown",
            "mdx": "markdown",
            "markdown": "markdown",
            "txt": "requirements",
            "regex": "regex",
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
        """Extract symbols from Python AST.

        Extracts:
        - Classes
        - Functions (module-level)
        - Methods (inside classes)
        - Module-level constants (UPPERCASE names or type-annotated assignments)
        """
        symbols: list[SyntacticSymbol] = []
        current_class: str | None = None

        def is_constant_name(name: str) -> bool:
            """Check if name follows constant naming convention (UPPER_CASE)."""
            # Must have at least one letter and be all uppercase letters/digits/underscores
            return (
                name.isupper()
                or (
                    name[0].isupper()
                    and "_" in name
                    and all(c.isupper() or c == "_" or c.isdigit() for c in name)
                )
            ) and not name.startswith("_")

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

            # Module-level constant/variable assignments (not inside classes)
            elif node.type == "expression_statement" and class_name is None:
                for child in node.children:
                    # Handle simple assignment: NAME = value
                    if child.type == "assignment":
                        left = child.child_by_field_name("left")
                        if left and left.type == "identifier":
                            name = left.text.decode("utf-8")
                            # Only index UPPERCASE constants
                            if is_constant_name(name):
                                symbols.append(
                                    SyntacticSymbol(
                                        name=name,
                                        kind="variable",
                                        line=node.start_point[0] + 1,
                                        column=node.start_point[1],
                                        end_line=node.end_point[0] + 1,
                                        end_column=node.end_point[1],
                                    )
                                )
                    # Handle annotated assignment: NAME: type = value
                    elif child.type == "type" or node.type == "typed_assignment":
                        pass  # These are handled in the next branch

            # Handle typed module-level assignments: NAME: Type = value
            elif (node.type in ("assignment", "typed_assignment")) and class_name is None:
                # Check if this is at module level (parent is module or expression_statement)
                left = node.child_by_field_name("left") or node.child_by_field_name("name")
                if left and left.type == "identifier":
                    name = left.text.decode("utf-8")
                    if is_constant_name(name):
                        symbols.append(
                            SyntacticSymbol(
                                name=name,
                                kind="variable",
                                line=node.start_point[0] + 1,
                                column=node.start_point[1],
                                end_line=node.end_point[0] + 1,
                                end_column=node.end_point[1],
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
