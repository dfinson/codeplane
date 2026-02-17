"""Language-specific tree-sitter query configurations for symbol extraction.

Each language defines:
- query_text: Tree-sitter S-expression query with @name, @node, and @params captures
- patterns: Ordered list mapping pattern indices to (kind, nested_kind)
- container_types: Node types that establish parent context (class-like containers)
- container_name_field: Field name to extract container name (default: "name")

Capture conventions:
- @node  -- the whole definition node (used for line/column/end position)
- @name  -- the name node (decoded for symbol name)
- @params -- parameter list node (decoded for signature)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolPattern:
    """Maps a query pattern index to symbol extraction metadata."""

    kind: str
    nested_kind: str | None = None  # Kind when inside a container


@dataclass(frozen=True)
class LanguageQueryConfig:
    """Complete query configuration for symbol extraction in a language."""

    query_text: str
    patterns: tuple[SymbolPattern, ...]
    container_types: frozenset[str] = frozenset()
    container_name_field: str = "name"
    # If True, signature is extracted by collecting 'parameter' children
    # between '(' and ')' on the @node (used for Swift, OCaml)
    params_from_children: bool = False


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------
PYTHON_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_definition
            name: (identifier) @name
            parameters: (parameters) @params) @node
        (class_definition
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="class"),
    ),
    container_types=frozenset({"class_definition"}),
)


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------
JAVASCRIPT_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (generator_function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (class_declaration
            name: (identifier) @name) @node
        (method_definition
            name: (property_identifier) @name
            parameters: (formal_parameters) @params) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="method"),
    ),
    container_types=frozenset({"class_declaration"}),
)


# ---------------------------------------------------------------------------
# TypeScript (also used for TSX)
# ---------------------------------------------------------------------------
TYPESCRIPT_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (generator_function_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (class_declaration
            name: (type_identifier) @name) @node
        (method_definition
            name: (property_identifier) @name
            parameters: (formal_parameters) @params) @node
        (interface_declaration
            name: (type_identifier) @name) @node
        (type_alias_declaration
            name: (type_identifier) @name) @node
        (enum_declaration
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="type_alias"),
        SymbolPattern(kind="enum"),
    ),
    container_types=frozenset({"class_declaration"}),
)


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------
GO_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (method_declaration
            name: (field_identifier) @name
            parameters: (parameter_list) @params) @node
        (type_declaration
            (type_spec
                name: (type_identifier) @name) @node)
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="type"),
    ),
)


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------
RUST_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_item
            name: (identifier) @name
            parameters: (parameters) @params) @node
        (struct_item
            name: (type_identifier) @name) @node
        (enum_item
            name: (type_identifier) @name) @node
        (trait_item
            name: (type_identifier) @name) @node
        (impl_item
            type: (type_identifier) @name) @node
        (type_item
            name: (type_identifier) @name) @node
        (const_item
            name: (identifier) @name) @node
        (static_item
            name: (identifier) @name) @node
        (mod_item
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="trait"),
        SymbolPattern(kind="impl"),
        SymbolPattern(kind="type_alias"),
        SymbolPattern(kind="constant"),
        SymbolPattern(kind="variable"),
        SymbolPattern(kind="module"),
    ),
    container_types=frozenset({"impl_item", "trait_item"}),
)


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------
JAVA_QUERIES = LanguageQueryConfig(
    query_text="""
        (class_declaration
            name: (identifier) @name) @node
        (interface_declaration
            name: (identifier) @name) @node
        (enum_declaration
            name: (identifier) @name) @node
        (record_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (method_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (constructor_declaration
            name: (identifier) @name
            parameters: (formal_parameters) @params) @node
        (annotation_type_declaration
            name: (identifier) @name) @node
        (enum_constant
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="class"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="record"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="constructor"),
        SymbolPattern(kind="annotation"),
        SymbolPattern(kind="enum_constant"),
    ),
    container_types=frozenset({"class_declaration", "interface_declaration", "enum_declaration"}),
)


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------
CSHARP_QUERIES = LanguageQueryConfig(
    query_text="""
        (class_declaration
            name: (identifier) @name) @node
        (interface_declaration
            name: (identifier) @name) @node
        (struct_declaration
            name: (identifier) @name) @node
        (enum_declaration
            name: (identifier) @name) @node
        (record_declaration
            name: (identifier) @name) @node
        (method_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (constructor_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (property_declaration
            name: (identifier) @name) @node
        (field_declaration
            (variable_declaration
                (variable_declarator
                    (identifier) @name))) @node
        (namespace_declaration
            name: (_) @name) @node
        (delegate_declaration
            name: (identifier) @name
            parameters: (parameter_list) @params) @node
        (event_declaration
            name: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="class"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="record"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="constructor"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="field"),
        SymbolPattern(kind="namespace"),
        SymbolPattern(kind="delegate"),
        SymbolPattern(kind="event"),
    ),
    container_types=frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "struct_declaration",
            "record_declaration",
        }
    ),
)


# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------
KOTLIN_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_declaration
            (identifier) @name
            (function_value_parameters) @params) @node
        (class_declaration
            (identifier) @name) @node
        (object_declaration
            (identifier) @name) @node
        (property_declaration
            (variable_declaration
                (identifier) @name)) @node
        (companion_object) @node
        (enum_entry
            (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="object"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="companion_object"),
        SymbolPattern(kind="enum_constant"),
    ),
    container_types=frozenset({"class_declaration", "object_declaration"}),
)


# ---------------------------------------------------------------------------
# Scala
# ---------------------------------------------------------------------------
SCALA_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_definition
            name: (identifier) @name
            parameters: (parameters) @params) @node
        (class_definition
            name: (identifier) @name) @node
        (object_definition
            name: (identifier) @name) @node
        (trait_definition
            name: (identifier) @name) @node
        (val_definition
            pattern: (identifier) @name) @node
        (var_definition
            pattern: (identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="object"),
        SymbolPattern(kind="trait"),
        SymbolPattern(kind="val"),
        SymbolPattern(kind="var"),
    ),
    container_types=frozenset({"class_definition", "object_definition", "trait_definition"}),
)


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------
PHP_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_definition
            name: (name) @name
            parameters: (formal_parameters) @params) @node
        (class_declaration
            name: (name) @name) @node
        (interface_declaration
            name: (name) @name) @node
        (trait_declaration
            name: (name) @name) @node
        (method_declaration
            name: (name) @name
            parameters: (formal_parameters) @params) @node
        (property_declaration
            (property_element
                (variable_name
                    (name) @name))) @node
        (enum_declaration
            name: (name) @name) @node
        (enum_case
            name: (name) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="interface"),
        SymbolPattern(kind="trait"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="enum_case"),
    ),
    container_types=frozenset({"class_declaration", "interface_declaration", "trait_declaration"}),
)


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------
RUBY_QUERIES = LanguageQueryConfig(
    query_text="""
        (method
            name: (identifier) @name
            parameters: (method_parameters) @params) @node
        (singleton_method
            name: (identifier) @name
            parameters: (method_parameters) @params) @node
        (singleton_method
            name: (identifier) @name) @node
        (class
            name: (constant) @name) @node
        (module
            name: (constant) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="module"),
    ),
    container_types=frozenset({"class", "module"}),
)


# ---------------------------------------------------------------------------
# C / C++
# ---------------------------------------------------------------------------
CPP_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (function_definition
            declarator: (function_declarator
                declarator: (qualified_identifier
                    name: (_) @name)
                parameters: (parameter_list) @params)) @node
        (function_definition
            declarator: (function_declarator
                declarator: (field_identifier) @name
                parameters: (parameter_list) @params)) @node
        (class_specifier
            name: (type_identifier) @name) @node
        (struct_specifier
            name: (type_identifier) @name) @node
        (namespace_definition
            name: (namespace_identifier) @name) @node
        (enum_specifier
            name: (type_identifier) @name) @node
        (declaration
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (declaration
            declarator: (function_declarator
                declarator: (qualified_identifier
                    name: (_) @name)
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (identifier) @name
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (qualified_identifier
                    name: (_) @name)
                parameters: (parameter_list) @params)) @node
        (field_declaration
            declarator: (function_declarator
                declarator: (field_identifier) @name
                parameters: (parameter_list) @params)) @node
    """,
    patterns=(
        # p0: function_definition with simple name
        SymbolPattern(kind="function", nested_kind="method"),
        # p1: function_definition with qualified name
        SymbolPattern(kind="function", nested_kind="method"),
        # p2: function_definition with field_identifier (class member)
        SymbolPattern(kind="method"),
        # p3: class
        SymbolPattern(kind="class"),
        # p4: struct
        SymbolPattern(kind="struct"),
        # p5: namespace
        SymbolPattern(kind="namespace"),
        # p6: enum
        SymbolPattern(kind="enum"),
        # p7: declaration with simple function name
        SymbolPattern(kind="function", nested_kind="method"),
        # p8: declaration with qualified function name
        SymbolPattern(kind="function", nested_kind="method"),
        # p9: field_declaration with simple function name
        SymbolPattern(kind="method"),
        # p10: field_declaration with qualified function name
        SymbolPattern(kind="method"),
        # p11: field_declaration with field_identifier
        SymbolPattern(kind="method"),
    ),
    container_types=frozenset({"class_specifier", "struct_specifier", "namespace_definition"}),
)


# ---------------------------------------------------------------------------
# Swift
# ---------------------------------------------------------------------------
SWIFT_QUERIES = LanguageQueryConfig(
    query_text="""
        (class_declaration
            "class"
            name: (type_identifier) @name) @node
        (class_declaration
            "struct"
            name: (type_identifier) @name) @node
        (class_declaration
            "enum"
            name: (type_identifier) @name) @node
        (protocol_declaration
            name: (type_identifier) @name) @node
        (function_declaration
            name: (simple_identifier) @name) @node
        (protocol_function_declaration
            name: (simple_identifier) @name) @node
        (property_declaration
            name: (pattern
                (simple_identifier) @name)) @node
        (enum_entry
            name: (simple_identifier) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="class"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="protocol"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="property"),
        SymbolPattern(kind="enum_case"),
    ),
    container_types=frozenset({"class_declaration", "protocol_declaration"}),
    # Swift params need custom extraction (no wrapper node)
    params_from_children=True,
)


# ---------------------------------------------------------------------------
# Elixir
# ---------------------------------------------------------------------------
ELIXIR_QUERIES = LanguageQueryConfig(
    query_text="""
        (call
            target: (identifier) @_target
            (arguments (alias) @name)
            (#eq? @_target "defmodule")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "def")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "defp")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "defmacro")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name
                    (arguments) @params))
            (#eq? @_target "defmacrop")) @node
        (call
            target: (identifier) @_target
            (arguments (alias) @name)
            (#eq? @_target "defprotocol")) @node
        (call
            target: (identifier) @_target
            (arguments (alias) @name)
            (#eq? @_target "defimpl")) @node
        (call
            target: (identifier) @_target
            (arguments
                (call
                    target: (identifier) @name))
            (#eq? @_target "defstruct")) @node
    """,
    patterns=(
        SymbolPattern(kind="module"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="private_function"),
        SymbolPattern(kind="macro"),
        SymbolPattern(kind="private_macro"),
        SymbolPattern(kind="protocol"),
        SymbolPattern(kind="implementation"),
        SymbolPattern(kind="struct"),
    ),
    container_types=frozenset(),  # Elixir uses defmodule nesting, handled by call nodes
)


# ---------------------------------------------------------------------------
# Haskell
# ---------------------------------------------------------------------------
HASKELL_QUERIES = LanguageQueryConfig(
    query_text="""
        (function
            name: (variable) @name
            patterns: (patterns) @params) @node
        (function
            name: (variable) @name) @node
        (signature
            name: (variable) @name
            type: (_) @params) @node
        (type_synomym
            name: (_) @name) @node
        (data_type
            name: (_) @name) @node
        (newtype
            name: (_) @name) @node
        (class
            name: (_) @name) @node
        (instance
            name: (_) @name) @node
        (data_constructor
            (prefix
                (constructor) @name)) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="signature"),
        SymbolPattern(kind="type_alias"),
        SymbolPattern(kind="data"),
        SymbolPattern(kind="newtype"),
        SymbolPattern(kind="type_class"),
        SymbolPattern(kind="instance"),
        SymbolPattern(kind="constructor"),
    ),
    container_types=frozenset({"class", "instance"}),
)


# ---------------------------------------------------------------------------
# OCaml
# ---------------------------------------------------------------------------
OCAML_QUERIES = LanguageQueryConfig(
    query_text="""
        (value_definition
            (let_binding
                (value_name) @name
                (parameter) @params)) @node
        (value_definition
            (let_binding
                (value_name) @name)) @node
        (type_definition
            (type_binding
                (type_constructor) @name)) @node
        (module_definition
            (module_binding
                (module_name) @name)) @node
        (module_type_definition
            (module_type_name) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="variable"),
        SymbolPattern(kind="type"),
        SymbolPattern(kind="module"),
        SymbolPattern(kind="module_type"),
    ),
)


# ---------------------------------------------------------------------------
# Julia
# ---------------------------------------------------------------------------
JULIA_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_definition
            (signature
                (typed_expression
                    (call_expression
                        (identifier) @name
                        (argument_list) @params)))) @node
        (function_definition
            (signature
                (call_expression
                    (identifier) @name
                    (argument_list) @params))) @node
        (assignment
            (call_expression
                (identifier) @name
                (argument_list) @params)) @node
        (macro_definition
            (signature
                (call_expression
                    (identifier) @name
                    (argument_list) @params))) @node
        (struct_definition
            (type_head) @name) @node
        (module_definition
            name: (identifier) @name) @node
        (abstract_definition
            (type_head) @name) @node
        (const_statement
            (assignment
                (identifier) @name)) @node
    """,
    patterns=(
        # p0: function_definition with type annotation
        SymbolPattern(kind="function"),
        # p1: function_definition without type annotation
        SymbolPattern(kind="function"),
        # p2: short function definition (assignment with call lhs)
        SymbolPattern(kind="function"),
        # p3: macro_definition
        SymbolPattern(kind="macro"),
        # p4: struct (both mutable and immutable)
        SymbolPattern(kind="struct"),
        # p5: module
        SymbolPattern(kind="module"),
        # p6: abstract type
        SymbolPattern(kind="abstract_type"),
        # p7: constant
        SymbolPattern(kind="constant"),
    ),
)


# ---------------------------------------------------------------------------
# Lua
# ---------------------------------------------------------------------------
LUA_QUERIES = LanguageQueryConfig(
    query_text="""
        (function_declaration
            name: (_) @name
            parameters: (parameters) @params) @node
        (variable_declaration
            (assignment_statement
                (variable_list
                    (identifier) @name))) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="variable"),
    ),
)


# ---------------------------------------------------------------------------
# Language -> Config mapping
# ---------------------------------------------------------------------------
# Keys match the language names used in extract_symbols() dispatch
# (result.language values from TreeSitterParser._detect_language).
LANGUAGE_QUERY_CONFIGS: dict[str, LanguageQueryConfig] = {
    "python": PYTHON_QUERIES,
    "javascript": JAVASCRIPT_QUERIES,
    "typescript": TYPESCRIPT_QUERIES,
    "go": GO_QUERIES,
    "rust": RUST_QUERIES,
    "java": JAVA_QUERIES,
    "c_sharp": CSHARP_QUERIES,
    "csharp": CSHARP_QUERIES,
    "kotlin": KOTLIN_QUERIES,
    "scala": SCALA_QUERIES,
    "php": PHP_QUERIES,
    "ruby": RUBY_QUERIES,
    "c": CPP_QUERIES,
    "cpp": CPP_QUERIES,
    "swift": SWIFT_QUERIES,
    "elixir": ELIXIR_QUERIES,
    "haskell": HASKELL_QUERIES,
    "ocaml": OCAML_QUERIES,
    "julia": JULIA_QUERIES,
    "lua": LUA_QUERIES,
}
