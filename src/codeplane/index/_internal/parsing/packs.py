"""Unified LanguagePack — single source of truth for all tree-sitter config.

Every language that CodePlane supports has exactly ONE LanguagePack that
consolidates ALL config:
- Grammar install metadata (package, module, version, loader function)
- File extension / filename detection
- Symbol extraction queries (S-expression patterns + SymbolPattern mappings)
- Scope types (data-driven scope walker config)
- SEM_FACTS queries (body-evidence for embeddings)
- Type extraction config (type annotations, type members, member accesses,
  interface implementations)
- Module declaration handler name
- Dynamic access handler name

The PACKS registry is the canonical lookup: ``PACKS["python"]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# =========================================================================
# Dataclasses
# =========================================================================


@dataclass(frozen=True)
class SymbolPattern:
    """Maps a query pattern index to symbol extraction metadata."""

    kind: str
    nested_kind: str | None = None  # Kind when inside a container


@dataclass(frozen=True)
class SymbolQueryConfig:
    """Query configuration for symbol extraction in a language.

    Defines S-expression patterns and how to interpret their matches.
    """

    query_text: str = ""
    patterns: tuple[SymbolPattern, ...] = ()
    container_types: frozenset[str] = frozenset()
    container_name_field: str = "name"
    params_from_children: bool = False
    body_node_types: frozenset[str] = frozenset({"block", "body", "class_body"})


@dataclass(frozen=True)
class TypeExtractionConfig:
    """Configuration for query-based type extraction.

    Used by QueryBasedExtractor to extract type annotations, type members,
    member accesses, and interface implementations.
    """

    language_family: str = ""

    # Query patterns (S-expression strings)
    type_annotation_query: str = ""
    type_member_query: str = ""
    member_access_query: str = ""
    interface_impl_query: str = ""

    # Node types that create scopes
    scope_node_types: tuple[str, ...] = ()

    # Member access configuration
    member_access_types: tuple[str, ...] = ("attribute",)
    member_identifier_types: tuple[str, ...] = (
        "identifier",
        "property_identifier",
        "field_identifier",
    )
    access_styles: tuple[str, ...] = ("dot",)

    # Feature flags
    supports_type_annotations: bool = True
    supports_interfaces: bool = False

    # Type normalization
    optional_patterns: tuple[str, ...] = ()
    array_patterns: tuple[str, ...] = ()
    generic_indicator: str = "<"
    reference_indicator: str = ""


@dataclass(frozen=True)
class LanguagePack:
    """Complete tree-sitter configuration for a single language."""

    # -- Identity --
    name: str  # Canonical language name ("python", "typescript", ...)
    grammar_name: str  # tree-sitter grammar module key ("c_sharp", "tsx", ...)

    # -- Grammar install --
    grammar_package: str  # PyPI package ("tree-sitter-python")
    grammar_module: str  # Python import ("tree_sitter_python")
    min_version: str  # Minimum version
    # Non-standard function name (e.g. "language_typescript", "language_tsx")
    language_func: str | None = None

    # -- File detection --
    extensions: frozenset[str] = field(default_factory=frozenset)
    filenames: frozenset[str] = field(default_factory=frozenset)

    # -- Symbol extraction --
    symbol_config: SymbolQueryConfig | None = None

    # -- Scope extraction (node_type -> scope_kind) --
    scope_types: dict[str, str] = field(default_factory=dict)

    # -- SEM_FACTS body evidence --
    sem_query: str | None = None

    # -- Type extraction --
    type_config: TypeExtractionConfig | None = None

    # -- Import extraction (tree-sitter query to find import nodes) --
    import_query: str | None = None

    # -- Declared module detection --
    declared_module_query: str | None = None
    declared_module_handler: str | None = None

    # -- Dynamic access detection (tree-sitter query to find dynamic nodes) --
    dynamic_query: str | None = None
    dynamic_handler: str | None = None


# =========================================================================
# Scope type mappings
# =========================================================================

_PYTHON_SCOPES: dict[str, str] = {
    "class_definition": "class",
    "function_definition": "function",
    "lambda": "lambda",
    "list_comprehension": "comprehension",
    "set_comprehension": "comprehension",
    "dictionary_comprehension": "comprehension",
    "generator_expression": "comprehension",
}

_JS_SCOPES: dict[str, str] = {
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

_GO_SCOPES: dict[str, str] = {
    "function_declaration": "function",
    "method_declaration": "function",
    "func_literal": "function",
    "type_declaration": "class",
    "for_statement": "block",
    "if_statement": "block",
    "block": "block",
}

_RUST_SCOPES: dict[str, str] = {
    "function_item": "function",
    "impl_item": "class",
    "struct_item": "class",
    "enum_item": "class",
    "trait_item": "class",
    "closure_expression": "lambda",
    "for_expression": "block",
    "if_expression": "block",
    "match_expression": "block",
    "block": "block",
}

_JAVA_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "class",
    "enum_declaration": "class",
    "method_declaration": "function",
    "constructor_declaration": "function",
    "lambda_expression": "lambda",
    "for_statement": "block",
    "enhanced_for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "try_statement": "block",
    "block": "block",
}

_CSHARP_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "struct_declaration": "class",
    "interface_declaration": "class",
    "enum_declaration": "class",
    "record_declaration": "class",
    "method_declaration": "function",
    "constructor_declaration": "function",
    "lambda_expression": "lambda",
    "for_statement": "block",
    "foreach_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "try_statement": "block",
    "block": "block",
}

_KOTLIN_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "object_declaration": "class",
    "function_declaration": "function",
    "lambda_literal": "lambda",
    "for_statement": "block",
    "while_statement": "block",
    "if_expression": "block",
}

_SCALA_SCOPES: dict[str, str] = {
    "class_definition": "class",
    "object_definition": "class",
    "trait_definition": "class",
    "function_definition": "function",
    "val_definition": "function",
    "for_expression": "block",
    "if_expression": "block",
    "match_expression": "block",
    "block": "block",
}

_RUBY_SCOPES: dict[str, str] = {
    "class": "class",
    "module": "class",
    "method": "function",
    "singleton_method": "function",
    "block": "block",
    "do_block": "block",
    "lambda": "lambda",
    "if": "block",
    "while": "block",
    "for": "block",
}

_PHP_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "class",
    "trait_declaration": "class",
    "method_declaration": "function",
    "function_definition": "function",
    "anonymous_function_creation_expression": "lambda",
    "arrow_function": "lambda",
    "for_statement": "block",
    "foreach_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
}

_SWIFT_SCOPES: dict[str, str] = {
    "class_declaration": "class",
    "struct_declaration": "class",
    "protocol_declaration": "class",
    "enum_declaration": "class",
    "function_declaration": "function",
    "closure_expression": "lambda",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
}

_ELIXIR_SCOPES: dict[str, str] = {
    "call": "function",
    "anonymous_function": "lambda",
    "do_block": "block",
}

_HASKELL_SCOPES: dict[str, str] = {
    "function": "function",
    "match": "block",
    "case": "block",
    "lambda": "lambda",
}

_C_CPP_SCOPES: dict[str, str] = {
    "function_definition": "function",
    "class_specifier": "class",
    "struct_specifier": "class",
    "namespace_definition": "block",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "compound_statement": "block",
}

_LUA_SCOPES: dict[str, str] = {
    "function_declaration": "function",
    "function_definition": "function",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "do_statement": "block",
}

_JULIA_SCOPES: dict[str, str] = {
    "function_definition": "function",
    "macro_definition": "function",
    "struct_definition": "class",
    "module_definition": "class",
    "for_statement": "block",
    "while_statement": "block",
    "if_statement": "block",
    "let_statement": "block",
}

_OCAML_SCOPES: dict[str, str] = {
    "let_binding": "function",
    "value_definition": "function",
    "type_definition": "class",
    "module_definition": "class",
    "match_expression": "block",
    "fun_expression": "lambda",
}

# Generic fallback: matches common node type substrings
_GENERIC_SCOPE_PATTERNS: dict[str, str] = {
    "class": "class",
    "function": "function",
    "method": "function",
    "block": "block",
    "lambda": "lambda",
}


# =========================================================================
# PYTHON
# =========================================================================

_PYTHON_SYMBOLS = SymbolQueryConfig(
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
    body_node_types=frozenset({"block"}),
)

_PYTHON_SEM = """
    (call function: (identifier) @sem_call)
    (call function: (attribute attribute: (identifier) @sem_call))
    (assignment left: (attribute attribute: (identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (raise_statement (call function: (identifier) @sem_raise))
    (raise_statement (identifier) @sem_raise)
    (pair key: (string) @sem_key)
"""

_PYTHON_TYPES = TypeExtractionConfig(
    language_family="python",
    scope_node_types=("function_definition", "class_definition"),
    member_access_types=("attribute",),
    optional_patterns=("Optional[", "| None", "None |"),
    array_patterns=("list[", "List[", "Sequence[", "tuple[", "Tuple[", "set[", "Set["),
    generic_indicator="[",
    supports_interfaces=False,
    type_annotation_query="""
(typed_parameter
  (identifier) @name
  type: (type) @type) @param
(typed_default_parameter
  name: (identifier) @name
  type: (type) @type) @param
(function_definition
  name: (identifier) @name
  return_type: (type) @type) @return
(assignment
  left: (identifier) @name
  type: (type) @type)
""",
    type_member_query="""
(class_definition
  name: (identifier) @parent
  body: (block
    (function_definition
      name: (identifier) @member) @method))
(class_definition
  name: (identifier) @parent
  body: (block
    (expression_statement
      (assignment
        left: (identifier) @member
        type: (type) @type))))
""",
    member_access_query="""
(attribute
  object: (identifier) @receiver
  attribute: (identifier) @member) @expr
(call
  function: (attribute
    object: (identifier) @receiver
    attribute: (identifier) @member) @expr) @call
""",
)

PYTHON_PACK = LanguagePack(
    name="python",
    grammar_name="python",
    grammar_package="tree-sitter-python",
    grammar_module="tree_sitter_python",
    min_version="0.23.0",
    extensions=frozenset({"py", "pyi", "pyw", "pyx", "pxd"}),
    symbol_config=_PYTHON_SYMBOLS,
    scope_types=_PYTHON_SCOPES,
    sem_query=_PYTHON_SEM,
    type_config=_PYTHON_TYPES,
    import_query="""
        (import_statement) @import_node
        (import_from_statement) @import_node
    """,
    dynamic_query="""
        (call) @dynamic_node
        (subscript) @dynamic_node
    """,
    dynamic_handler="_extract_python_dynamic",
)


# =========================================================================
# JAVASCRIPT
# =========================================================================

_JAVASCRIPT_SYMBOLS = SymbolQueryConfig(
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

_JS_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (member_expression
            property: (property_identifier) @sem_call))
    (assignment_expression
        left: (member_expression
            property: (property_identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement
        (new_expression constructor: (identifier) @sem_raise))
    (pair key: (property_identifier) @sem_key)
"""

_JS_TS_TYPES = TypeExtractionConfig(
    language_family="javascript",
    scope_node_types=(
        "function_declaration",
        "method_definition",
        "class_declaration",
        "arrow_function",
    ),
    member_access_types=("member_expression",),
    optional_patterns=("| null", "| undefined", "?"),
    array_patterns=("[]", "Array<", "ReadonlyArray<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(required_parameter
  pattern: (identifier) @name
  type: (type_annotation (_) @type)) @param
(optional_parameter
  pattern: (identifier) @name
  type: (type_annotation (_) @type)) @param
(function_declaration
  name: (identifier) @name
  return_type: (type_annotation (_) @type)) @return
(method_definition
  name: (property_identifier) @name
  return_type: (type_annotation (_) @type)) @return
(arrow_function
  return_type: (type_annotation (_) @type)) @return
(variable_declarator
  name: (identifier) @name
  type: (type_annotation (_) @type))
(public_field_definition
  name: (property_identifier) @name
  type: (type_annotation (_) @type)) @field
(property_signature
  name: (property_identifier) @name
  type: (type_annotation (_) @type)) @field
""",
    type_member_query="""
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (method_definition
      name: (property_identifier) @member) @method))
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (public_field_definition
      name: (property_identifier) @member
      type: (type_annotation (_) @type)?)))
(interface_declaration
  name: (type_identifier) @parent
  body: (interface_body
    (method_signature
      name: (property_identifier) @member) @method))
(interface_declaration
  name: (type_identifier) @parent
  body: (interface_body
    (property_signature
      name: (property_identifier) @member
      type: (type_annotation (_) @type)?)))
""",
    member_access_query="""
(member_expression
  object: (identifier) @receiver
  property: (property_identifier) @member) @expr
(call_expression
  function: (member_expression
    object: (identifier) @receiver
    property: (property_identifier) @member) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  name: (type_identifier) @implementor
  (class_heritage
    (implements_clause
      (type_identifier) @interface)))
""",
)

JAVASCRIPT_PACK = LanguagePack(
    name="javascript",
    grammar_name="javascript",
    grammar_package="tree-sitter-javascript",
    grammar_module="tree_sitter_javascript",
    min_version="0.23.0",
    extensions=frozenset({"js", "jsx", "mjs", "cjs"}),
    symbol_config=_JAVASCRIPT_SYMBOLS,
    scope_types=_JS_SCOPES,
    sem_query=_JS_SEM,
    type_config=_JS_TS_TYPES,
    import_query="""
        (import_statement) @import_node
        (call_expression) @import_node
    """,
    dynamic_query="""
        (subscript_expression) @dynamic_node
        (call_expression) @dynamic_node
    """,
    dynamic_handler="_extract_js_dynamic",
)


# =========================================================================
# TYPESCRIPT
# =========================================================================

_TYPESCRIPT_SYMBOLS = SymbolQueryConfig(
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

_TS_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (member_expression
            property: (property_identifier) @sem_call))
    (assignment_expression
        left: (member_expression
            property: (property_identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement
        (new_expression constructor: (identifier) @sem_raise))
    (pair key: (property_identifier) @sem_key)
"""

TYPESCRIPT_PACK = LanguagePack(
    name="typescript",
    grammar_name="typescript",
    grammar_package="tree-sitter-typescript",
    grammar_module="tree_sitter_typescript",
    min_version="0.23.0",
    language_func="language_typescript",
    extensions=frozenset({"ts", "mts", "cts"}),
    symbol_config=_TYPESCRIPT_SYMBOLS,
    scope_types=_JS_SCOPES,
    sem_query=_TS_SEM,
    type_config=_JS_TS_TYPES,
    import_query="""
        (import_statement) @import_node
        (call_expression) @import_node
    """,
    dynamic_query="""
        (subscript_expression) @dynamic_node
        (call_expression) @dynamic_node
    """,
    dynamic_handler="_extract_js_dynamic",
)

TSX_PACK = LanguagePack(
    name="tsx",
    grammar_name="tsx",
    grammar_package="tree-sitter-typescript",
    grammar_module="tree_sitter_typescript",
    min_version="0.23.0",
    language_func="language_tsx",
    extensions=frozenset({"tsx"}),
    symbol_config=_TYPESCRIPT_SYMBOLS,
    scope_types=_JS_SCOPES,
    sem_query=_TS_SEM,
    type_config=_JS_TS_TYPES,
    import_query="""
        (import_statement) @import_node
        (call_expression) @import_node
    """,
    dynamic_query="""
        (subscript_expression) @dynamic_node
        (call_expression) @dynamic_node
    """,
    dynamic_handler="_extract_js_dynamic",
)


# =========================================================================
# GO
# =========================================================================

_GO_SYMBOLS = SymbolQueryConfig(
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

_GO_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (selector_expression
            field: (field_identifier) @sem_call))
    (return_statement
        (expression_list (identifier) @sem_return))
"""

_GO_TYPES = TypeExtractionConfig(
    language_family="go",
    scope_node_types=("function_declaration", "method_declaration"),
    member_access_types=("selector_expression",),
    member_identifier_types=("field_identifier",),
    optional_patterns=(),
    array_patterns=("[]",),
    generic_indicator="[",
    reference_indicator="*",
    supports_interfaces=True,
    type_annotation_query="""
(parameter_declaration
  name: (identifier) @name
  type: (_) @type) @param
(function_declaration
  name: (identifier) @name
  result: (_) @type) @return
(method_declaration
  name: (field_identifier) @name
  result: (_) @type) @return
(var_spec
  name: (identifier) @name
  type: (_) @type)
(const_spec
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(type_declaration
  (type_spec
    name: (type_identifier) @parent
    type: (struct_type
      (field_declaration_list
        (field_declaration
          name: (field_identifier) @member)))))
(type_declaration
  (type_spec
    name: (type_identifier) @parent
    type: (interface_type
      (method_elem
        (field_identifier) @member) @method)))
""",
    member_access_query="""
(selector_expression
  operand: (identifier) @receiver
  field: (field_identifier) @member) @expr
(call_expression
  function: (selector_expression
    operand: (identifier) @receiver
    field: (field_identifier) @member) @expr) @call
""",
)

GO_PACK = LanguagePack(
    name="go",
    grammar_name="go",
    grammar_package="tree-sitter-go",
    grammar_module="tree_sitter_go",
    min_version="0.23.0",
    extensions=frozenset({"go"}),
    symbol_config=_GO_SYMBOLS,
    scope_types=_GO_SCOPES,
    sem_query=_GO_SEM,
    type_config=_GO_TYPES,
    import_query="""
        (import_spec) @import_node
    """,
    declared_module_query="(package_clause) @module_node",
    declared_module_handler="_declared_module_go",
)


# =========================================================================
# RUST
# =========================================================================

_RUST_SYMBOLS = SymbolQueryConfig(
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

_RUST_SEM = """
    (call_expression function: (identifier) @sem_call)
    (call_expression
        function: (field_expression
            field: (field_identifier) @sem_call))
    (call_expression
        function: (scoped_identifier
            name: (identifier) @sem_call))
    (assignment_expression
        left: (field_expression
            field: (field_identifier) @sem_field))
    (return_expression (identifier) @sem_return)
"""

_RUST_TYPES = TypeExtractionConfig(
    language_family="rust",
    scope_node_types=("function_item", "impl_item"),
    member_access_types=("field_expression",),
    member_identifier_types=("field_identifier",),
    access_styles=("dot", "scope"),
    optional_patterns=("Option<",),
    array_patterns=("Vec<", "["),
    generic_indicator="<",
    reference_indicator="&",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  pattern: (identifier) @name
  type: (_) @type) @param
(function_item
  name: (identifier) @name
  return_type: (_) @type) @return
(let_declaration
  pattern: (identifier) @name
  type: (_) @type)
(const_item
  name: (identifier) @name
  type: (_) @type)
(static_item
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(struct_item
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      name: (field_identifier) @member
      type: (_) @type)))
(enum_item
  name: (type_identifier) @parent
  body: (enum_variant_list
    (enum_variant
      name: (identifier) @member)))
(trait_item
  name: (type_identifier) @parent
  body: (declaration_list
    (function_signature_item
      name: (identifier) @member) @method))
(impl_item
  type: (type_identifier) @parent
  body: (declaration_list
    (function_item
      name: (identifier) @member) @method))
""",
    member_access_query="""
(field_expression
  value: (identifier) @receiver
  field: (field_identifier) @member) @expr
(call_expression
  function: (field_expression
    value: (identifier) @receiver
    field: (field_identifier) @member) @expr) @call
(scoped_identifier
  path: (identifier) @receiver
  name: (identifier) @member) @expr @scope
""",
    interface_impl_query="""
(impl_item
  trait: (type_identifier) @interface
  "for"
  type: (type_identifier) @implementor)
""",
)

RUST_PACK = LanguagePack(
    name="rust",
    grammar_name="rust",
    grammar_package="tree-sitter-rust",
    grammar_module="tree_sitter_rust",
    min_version="0.23.0",
    extensions=frozenset({"rs"}),
    symbol_config=_RUST_SYMBOLS,
    scope_types=_RUST_SCOPES,
    sem_query=_RUST_SEM,
    type_config=_RUST_TYPES,
    import_query="""
        (use_declaration) @import_node
    """,
)


# =========================================================================
# JAVA
# =========================================================================

_JAVA_SYMBOLS = SymbolQueryConfig(
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

_JAVA_SEM = """
    (method_invocation name: (identifier) @sem_call)
    (assignment_expression
        left: (field_access
            field: (identifier) @sem_field))
    (return_statement (identifier) @sem_return)
    (throw_statement
        (object_creation_expression
            type: (type_identifier) @sem_raise))
"""

_JAVA_TYPES = TypeExtractionConfig(
    language_family="jvm",
    scope_node_types=("method_declaration", "constructor_declaration", "class_declaration"),
    member_access_types=("field_access", "method_invocation"),
    optional_patterns=("Optional<",),
    array_patterns=("[]", "List<", "Set<", "Collection<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(formal_parameter
  type: (_) @type
  name: (identifier) @name) @param
(method_declaration
  type: (_) @type
  name: (identifier) @name) @return
(field_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @name)) @field
(local_variable_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @name))
""",
    type_member_query="""
(class_declaration
  name: (identifier) @parent
  body: (class_body
    (method_declaration
      name: (identifier) @member) @method))
(class_declaration
  name: (identifier) @parent
  body: (class_body
    (field_declaration
      declarator: (variable_declarator
        name: (identifier) @member))))
(interface_declaration
  name: (identifier) @parent
  body: (interface_body
    (method_declaration
      name: (identifier) @member) @method))
(enum_declaration
  name: (identifier) @parent
  body: (enum_body
    (enum_constant
      name: (identifier) @member)))
""",
    member_access_query="""
(field_access
  object: (identifier) @receiver
  field: (identifier) @member) @expr
(method_invocation
  object: (identifier) @receiver
  name: (identifier) @member
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (identifier) @implementor
  interfaces: (super_interfaces
    (type_list
      (_) @interface)))
""",
)

JAVA_PACK = LanguagePack(
    name="java",
    grammar_name="java",
    grammar_package="tree-sitter-java",
    grammar_module="tree_sitter_java",
    min_version="0.23.0",
    extensions=frozenset({"java"}),
    symbol_config=_JAVA_SYMBOLS,
    scope_types=_JAVA_SCOPES,
    sem_query=_JAVA_SEM,
    type_config=_JAVA_TYPES,
    import_query="""
        (import_declaration) @import_node
    """,
    declared_module_query="(package_declaration) @module_node",
    declared_module_handler="_declared_module_java",
)


# =========================================================================
# C#
# =========================================================================

_CSHARP_SYMBOLS = SymbolQueryConfig(
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

_CSHARP_TYPES = TypeExtractionConfig(
    language_family="dotnet",
    scope_node_types=("method_declaration", "constructor_declaration", "class_declaration"),
    member_access_types=("member_access_expression",),
    optional_patterns=("?",),
    array_patterns=("[]", "List<", "IEnumerable<", "ICollection<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  type: (_) @type
  name: (identifier) @name) @param
(method_declaration
  returns: (_) @type
  name: (identifier) @name) @return
(property_declaration
  type: (_) @type
  name: (identifier) @name) @field
(field_declaration
  (variable_declaration
    type: (_) @type
    (variable_declarator
      (identifier) @name))) @field
(local_declaration_statement
  (variable_declaration
    type: (_) @type
    (variable_declarator
      (identifier) @name)))
""",
    type_member_query="""
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (method_declaration
      name: (identifier) @member) @method))
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (property_declaration
      name: (identifier) @member)))
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (field_declaration
      (variable_declaration
        (variable_declarator
          (identifier) @member)))))
(interface_declaration
  name: (identifier) @parent
  body: (declaration_list
    (method_declaration
      name: (identifier) @member) @method))
""",
    member_access_query="""
(member_access_expression
  expression: (identifier) @receiver
  name: (identifier) @member) @expr
(invocation_expression
  function: (member_access_expression
    expression: (identifier) @receiver
    name: (identifier) @member) @expr
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (identifier) @implementor
  (base_list
    (_) @interface))
(struct_declaration
  name: (identifier) @implementor
  (base_list
    (_) @interface))
""",
)

CSHARP_PACK = LanguagePack(
    name="csharp",
    grammar_name="c_sharp",
    grammar_package="tree-sitter-c-sharp",
    grammar_module="tree_sitter_c_sharp",
    min_version="0.23.0",
    extensions=frozenset({"cs"}),
    symbol_config=_CSHARP_SYMBOLS,
    scope_types=_CSHARP_SCOPES,
    type_config=_CSHARP_TYPES,
    import_query="""
        (using_directive) @import_node
    """,
    declared_module_handler="_declared_module_csharp",
)


# =========================================================================
# KOTLIN
# =========================================================================

_KOTLIN_SYMBOLS = SymbolQueryConfig(
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

_KOTLIN_TYPES = TypeExtractionConfig(
    language_family="jvm",
    scope_node_types=("function_declaration", "class_declaration"),
    member_access_types=("navigation_expression",),
    optional_patterns=("?",),
    array_patterns=("List<", "Array<", "Set<", "Collection<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  (simple_identifier) @name
  (user_type) @type) @param
(function_declaration
  (simple_identifier) @name
  (user_type) @type) @return
(property_declaration
  (variable_declaration
    (simple_identifier) @name
    (user_type) @type?))
""",
    type_member_query="""
(class_declaration
  (type_identifier) @parent
  (class_body
    (function_declaration
      (simple_identifier) @member) @method))
(class_declaration
  (type_identifier) @parent
  (class_body
    (property_declaration
      (variable_declaration
        (simple_identifier) @member))))
(interface_declaration
  (type_identifier) @parent
  (interface_body
    (function_declaration
      (simple_identifier) @member) @method))
""",
    member_access_query="""
(navigation_expression
  (simple_identifier) @receiver
  (navigation_suffix
    (simple_identifier) @member)) @expr
(call_expression
  (navigation_expression
    (simple_identifier) @receiver
    (navigation_suffix
      (simple_identifier) @member)) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  (type_identifier) @implementor
  (delegation_specifiers
    (delegation_specifier
      (user_type
        (type_identifier) @interface))))
""",
)

KOTLIN_PACK = LanguagePack(
    name="kotlin",
    grammar_name="kotlin",
    grammar_package="tree-sitter-kotlin",
    grammar_module="tree_sitter_kotlin",
    min_version="1.0.0",
    extensions=frozenset({"kt", "kts"}),
    symbol_config=_KOTLIN_SYMBOLS,
    scope_types=_KOTLIN_SCOPES,
    type_config=_KOTLIN_TYPES,
    import_query="""
        (import) @import_node
    """,
    declared_module_query="(package_header) @module_node",
    declared_module_handler="_declared_module_kotlin",
)


# =========================================================================
# SCALA
# =========================================================================

_SCALA_SYMBOLS = SymbolQueryConfig(
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

_SCALA_TYPES = TypeExtractionConfig(
    language_family="jvm",
    scope_node_types=("function_definition", "class_definition"),
    member_access_types=("field_expression",),
    optional_patterns=("Option[",),
    array_patterns=("List[", "Seq[", "Array[", "Set[", "Vector["),
    generic_indicator="[",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  name: (identifier) @name
  (parameter_type
    (_) @type)) @param
(function_definition
  name: (identifier) @name
  return_type: (_) @type) @return
(val_definition
  pattern: (identifier) @name
  type: (_) @type)
(var_definition
  pattern: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(class_definition
  name: (identifier) @parent
  body: (template_body
    (function_definition
      name: (identifier) @member) @method))
(class_definition
  name: (identifier) @parent
  body: (template_body
    (val_definition
      pattern: (identifier) @member
      type: (_) @type?)))
(trait_definition
  name: (identifier) @parent
  body: (template_body
    (function_definition
      name: (identifier) @member) @method))
""",
    member_access_query="""
(field_expression
  value: (identifier) @receiver
  field: (identifier) @member) @expr
(call_expression
  function: (field_expression
    value: (identifier) @receiver
    field: (identifier) @member) @expr) @call
""",
    interface_impl_query="""
(class_definition
  name: (identifier) @implementor
  (extends_clause
    (type_identifier) @interface))
""",
)

SCALA_PACK = LanguagePack(
    name="scala",
    grammar_name="scala",
    grammar_package="tree-sitter-scala",
    grammar_module="tree_sitter_scala",
    min_version="0.23.0",
    extensions=frozenset({"scala", "sc"}),
    symbol_config=_SCALA_SYMBOLS,
    scope_types=_SCALA_SCOPES,
    type_config=_SCALA_TYPES,
    import_query="""
        (import_declaration) @import_node
    """,
    declared_module_query="(package_clause) @module_node",
    declared_module_handler="_declared_module_scala",
)


# =========================================================================
# C / C++
# =========================================================================

_CPP_SYMBOLS = SymbolQueryConfig(
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
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="class"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="namespace"),
        SymbolPattern(kind="enum"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="function", nested_kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="method"),
        SymbolPattern(kind="method"),
    ),
    container_types=frozenset({"class_specifier", "struct_specifier", "namespace_definition"}),
)

_CPP_TYPES = TypeExtractionConfig(
    language_family="cpp",
    scope_node_types=("function_definition", "class_specifier", "struct_specifier"),
    member_access_types=("field_expression",),
    member_identifier_types=("field_identifier",),
    access_styles=("dot", "arrow", "scope"),
    optional_patterns=(),
    array_patterns=("[]", "vector<", "array<"),
    generic_indicator="<",
    reference_indicator="&",
    supports_interfaces=True,
    type_annotation_query="""
(parameter_declaration
  type: (_) @type
  declarator: (identifier) @name) @param
(parameter_declaration
  type: (_) @type
  declarator: (pointer_declarator
    declarator: (identifier) @name)) @param
(function_definition
  type: (_) @type
  declarator: (function_declarator
    declarator: (identifier) @name)) @return
(declaration
  type: (_) @type
  declarator: (init_declarator
    declarator: (identifier) @name))
""",
    type_member_query="""
(struct_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      type: (_) @type
      declarator: (field_identifier) @member)))
(class_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      type: (_) @type
      declarator: (field_identifier) @member)))
(class_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (function_definition
      declarator: (function_declarator
        declarator: (identifier) @member)) @method))
""",
    member_access_query="""
(field_expression
  argument: (identifier) @receiver
  field: (field_identifier) @member) @expr
(qualified_identifier
  scope: (namespace_identifier) @receiver
  name: (identifier) @member) @expr @scope
(call_expression
  function: (field_expression
    argument: (identifier) @receiver
    field: (field_identifier) @member) @expr
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_specifier
  name: (type_identifier) @implementor
  (base_class_clause
    (type_identifier) @interface))
""",
)

CPP_PACK = LanguagePack(
    name="cpp",
    grammar_name="cpp",
    grammar_package="tree-sitter-cpp",
    grammar_module="tree_sitter_cpp",
    min_version="0.23.0",
    extensions=frozenset({"cpp", "cc", "cxx", "hpp", "hxx", "hh"}),
    symbol_config=_CPP_SYMBOLS,
    scope_types=_C_CPP_SCOPES,
    type_config=_CPP_TYPES,
    import_query="""
        (preproc_include) @import_node
    """,
)

C_PACK = LanguagePack(
    name="c",
    grammar_name="c",
    grammar_package="tree-sitter-c",
    grammar_module="tree_sitter_c",
    min_version="0.23.0",
    extensions=frozenset({"c", "h"}),
    symbol_config=_CPP_SYMBOLS,
    scope_types=_C_CPP_SCOPES,
    type_config=_CPP_TYPES,
    import_query="""
        (preproc_include) @import_node
    """,
)


# =========================================================================
# RUBY
# =========================================================================

_RUBY_SYMBOLS = SymbolQueryConfig(
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

_RUBY_TYPES = TypeExtractionConfig(
    language_family="ruby",
    scope_node_types=("method", "class"),
    member_access_types=("call",),
    supports_type_annotations=False,
    supports_interfaces=False,
    type_annotation_query="",
    type_member_query="""
(class
  name: (constant) @parent
  body: (body_statement
    (method
      name: (identifier) @member) @method)?)
(class
  name: (constant) @parent
  body: (body_statement
    (singleton_method
      name: (identifier) @member) @method @static)?)
(class
  name: (constant) @parent
  body: (body_statement
    (call
      method: (identifier) @kind
      arguments: (argument_list
        (simple_symbol) @member))))
""",
    member_access_query="""
(call
  receiver: (identifier) @receiver
  method: (identifier) @member) @expr
(call
  receiver: (identifier) @receiver
  method: (identifier) @member
  arguments: (argument_list) @args) @call
""",
)

RUBY_PACK = LanguagePack(
    name="ruby",
    grammar_name="ruby",
    grammar_package="tree-sitter-ruby",
    grammar_module="tree_sitter_ruby",
    min_version="0.23.0",
    extensions=frozenset({"rb", "rake"}),
    filenames=frozenset({"vagrantfile", "rakefile", "gemfile", "guardfile"}),
    symbol_config=_RUBY_SYMBOLS,
    scope_types=_RUBY_SCOPES,
    type_config=_RUBY_TYPES,
    import_query="""
        (call) @import_node
    """,
    declared_module_handler="_declared_module_ruby",
)


# =========================================================================
# PHP
# =========================================================================

_PHP_SYMBOLS = SymbolQueryConfig(
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

_PHP_TYPES = TypeExtractionConfig(
    language_family="php",
    scope_node_types=("method_declaration", "function_definition", "class_declaration"),
    member_access_types=("member_access_expression",),
    access_styles=("arrow",),
    optional_patterns=("?",),
    array_patterns=("array", "iterable"),
    generic_indicator="",
    supports_interfaces=True,
    type_annotation_query="""
(simple_parameter
  type: (_) @type
  name: (variable_name) @name) @param
(function_definition
  name: (name) @name
  return_type: (union_type) @type) @return
(function_definition
  name: (name) @name
  return_type: (named_type) @type) @return
(method_declaration
  name: (name) @name
  return_type: (_) @type) @return
(property_declaration
  type: (_) @type
  (property_element
    (variable_name) @name)) @field
""",
    type_member_query="""
(class_declaration
  name: (name) @parent
  body: (declaration_list
    (method_declaration
      name: (name) @member) @method))
(class_declaration
  name: (name) @parent
  body: (declaration_list
    (property_declaration
      type: (_) @type?
      (property_element
        (variable_name) @member))))
(interface_declaration
  name: (name) @parent
  body: (declaration_list
    (method_declaration
      name: (name) @member) @method))
""",
    member_access_query="""
(member_access_expression
  object: (variable_name) @receiver
  name: (name) @member) @expr
(member_call_expression
  object: (variable_name) @receiver
  name: (name) @member
  arguments: (arguments) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (name) @implementor
  (class_interface_clause
    (name) @interface))
""",
)

PHP_PACK = LanguagePack(
    name="php",
    grammar_name="php",
    grammar_package="tree-sitter-php",
    grammar_module="tree_sitter_php",
    min_version="0.23.0",
    language_func="language_php",
    extensions=frozenset({"php"}),
    symbol_config=_PHP_SYMBOLS,
    scope_types=_PHP_SCOPES,
    type_config=_PHP_TYPES,
    import_query="""
        (namespace_use_declaration) @import_node
    """,
    declared_module_query="(namespace_definition) @module_node",
    declared_module_handler="_declared_module_php",
)


# =========================================================================
# SWIFT
# =========================================================================

_SWIFT_SYMBOLS = SymbolQueryConfig(
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
    params_from_children=True,
)

_SWIFT_TYPES = TypeExtractionConfig(
    language_family="swift",
    scope_node_types=("function_declaration", "class_declaration"),
    member_access_types=("navigation_expression",),
    optional_patterns=("?",),
    array_patterns=("[", "Array<"),
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
(parameter
  (simple_identifier) @name
  (type_annotation
    (_) @type)) @param
(function_declaration
  name: (simple_identifier) @name
  (function_signature
    (return_clause
      (_) @type))) @return
(property_declaration
  (pattern
    (simple_identifier) @name)
  (type_annotation
    (_) @type))
(local_declaration
  (value_binding_pattern
    (pattern
      (simple_identifier) @name)
    (type_annotation
      (_) @type)))
""",
    type_member_query="""
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (function_declaration
      name: (simple_identifier) @member) @method))
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (property_declaration
      (pattern
        (simple_identifier) @member)
      (type_annotation
        (_) @type)?)))
(protocol_declaration
  name: (type_identifier) @parent
  body: (protocol_body
    (protocol_method_declaration
      name: (simple_identifier) @member) @method))
""",
    member_access_query="""
(navigation_expression
  target: (simple_identifier) @receiver
  suffix: (navigation_suffix
    (simple_identifier) @member)) @expr
(call_expression
  (navigation_expression
    target: (simple_identifier) @receiver
    suffix: (navigation_suffix
      (simple_identifier) @member)) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  name: (type_identifier) @implementor
  (inheritance_clause
    (inheritance_specifier
      (type_identifier) @interface)))
""",
)

SWIFT_PACK = LanguagePack(
    name="swift",
    grammar_name="swift",
    grammar_package="tree-sitter-swift",
    grammar_module="tree_sitter_swift",
    min_version="0.0.1",
    extensions=frozenset({"swift"}),
    symbol_config=_SWIFT_SYMBOLS,
    scope_types=_SWIFT_SCOPES,
    type_config=_SWIFT_TYPES,
    import_query="""
        (import_declaration) @import_node
    """,
)


# =========================================================================
# ELIXIR
# =========================================================================

_ELIXIR_SYMBOLS = SymbolQueryConfig(
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
)

_ELIXIR_TYPES = TypeExtractionConfig(
    language_family="elixir",
    scope_node_types=("call",),
    member_access_types=("dot",),
    supports_type_annotations=True,
    supports_interfaces=True,
    type_annotation_query="""
(unary_operator
  operator: "@"
  operand: (call
    target: (identifier) @kind
    (arguments
      (binary_operator
        left: (call
          target: (identifier) @name)
        operator: "::"
        right: (_) @type)))) @return
""",
    type_member_query="""
(call
  target: (identifier) @defmodule
  (arguments
    (alias) @parent)
  (do_block
    (call
      target: (identifier) @def_kind
      (arguments
        (call
          target: (identifier) @member)))))) @method
""",
    member_access_query="""
(dot
  left: (identifier) @receiver
  right: (identifier) @member) @expr
(call
  target: (dot
    left: (identifier) @receiver
    right: (identifier) @member) @expr) @call
""",
    interface_impl_query="""
(unary_operator
  operator: "@"
  operand: (call
    target: (identifier) @behaviour
    (arguments
      (alias) @interface)))
""",
)

ELIXIR_PACK = LanguagePack(
    name="elixir",
    grammar_name="elixir",
    grammar_package="tree-sitter-elixir",
    grammar_module="tree_sitter_elixir",
    min_version="0.3.0",
    extensions=frozenset({"ex", "exs"}),
    symbol_config=_ELIXIR_SYMBOLS,
    scope_types=_ELIXIR_SCOPES,
    type_config=_ELIXIR_TYPES,
    import_query="""
        (call) @import_node
    """,
    declared_module_handler="_declared_module_elixir",
)


# =========================================================================
# HASKELL
# =========================================================================

_HASKELL_SYMBOLS = SymbolQueryConfig(
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

_HASKELL_TYPES = TypeExtractionConfig(
    language_family="haskell",
    scope_node_types=("function", "signature"),
    optional_patterns=("Maybe",),
    array_patterns=("[]", "List"),
    supports_type_annotations=True,
    supports_interfaces=True,
    type_annotation_query="""
(signature
  name: (variable) @name
  type: (_) @type) @return
(typed_expression
  expression: (variable) @name
  type: (_) @type)
""",
    type_member_query="""
(class
  name: (type) @parent
  (class_body
    (signature
      name: (variable) @member) @method))
(data
  name: (type) @parent
  (constructors
    (data_constructor
      (record
        (field
          (variable) @member
          type: (_) @type)))))
""",
    member_access_query="""
(variable) @receiver
""",
    interface_impl_query="""
(instance
  name: (type) @interface
  types: (type) @implementor)
""",
)

HASKELL_PACK = LanguagePack(
    name="haskell",
    grammar_name="haskell",
    grammar_package="tree-sitter-haskell",
    grammar_module="tree_sitter_haskell",
    min_version="0.23.0",
    extensions=frozenset({"hs", "lhs"}),
    symbol_config=_HASKELL_SYMBOLS,
    scope_types=_HASKELL_SCOPES,
    type_config=_HASKELL_TYPES,
    import_query="""
        (import) @import_node
    """,
    declared_module_handler="_declared_module_haskell",
)


# =========================================================================
# OCAML
# =========================================================================

_OCAML_SYMBOLS = SymbolQueryConfig(
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

_OCAML_TYPES = TypeExtractionConfig(
    language_family="ocaml",
    scope_node_types=("value_definition", "let_binding"),
    member_access_types=("field_get_expression",),
    optional_patterns=("option",),
    array_patterns=("list", "array"),
    generic_indicator="'",
    supports_type_annotations=True,
    supports_interfaces=True,
    type_annotation_query="""
(let_binding
  pattern: (value_name) @name
  type: (type_constructor_path) @type)
(parameter
  pattern: (value_name) @name
  type: (_) @type) @param
""",
    type_member_query="""
(type_definition
  (type_binding
    name: (type_constructor) @parent
    body: (record_declaration
      (field_declaration
        name: (field_name) @member
        type: (_) @type))))
(object_expression
  (method_definition
    name: (method_name) @member) @method)
""",
    member_access_query="""
(field_get_expression
  record: (value_path) @receiver
  field: (field_path) @member) @expr
""",
)

OCAML_PACK = LanguagePack(
    name="ocaml",
    grammar_name="ocaml",
    grammar_package="tree-sitter-ocaml",
    grammar_module="tree_sitter_ocaml",
    min_version="0.23.0",
    language_func="language_ocaml",
    extensions=frozenset({"ml", "mli"}),
    symbol_config=_OCAML_SYMBOLS,
    scope_types=_OCAML_SCOPES,
    type_config=_OCAML_TYPES,
    import_query="""
        (open_module) @import_node
        (include_module) @import_node
    """,
    declared_module_handler="_declared_module_ocaml",
)


# =========================================================================
# ZIG
# =========================================================================

_ZIG_TYPES = TypeExtractionConfig(
    language_family="zig",
    scope_node_types=("fn_decl",),
    member_access_types=("field_access",),
    optional_patterns=("?",),
    array_patterns=("[]",),
    supports_type_annotations=True,
    type_annotation_query="""
(param_decl
  name: (identifier) @name
  type: (_) @type) @param
(fn_decl
  name: (identifier) @name
  return_type: (_) @type) @return
(var_decl
  name: (identifier) @name
  type: (_) @type)
(const_decl
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
(container_decl
  (container_field
    name: (identifier) @member
    type: (_) @type))
""",
    member_access_query="""
(field_access
  operand: (identifier) @receiver
  field: (identifier) @member) @expr
(call_expr
  function: (field_access
    operand: (identifier) @receiver
    field: (identifier) @member) @expr) @call
""",
)

ZIG_PACK = LanguagePack(
    name="zig",
    grammar_name="zig",
    grammar_package="tree-sitter-zig",
    grammar_module="tree_sitter_zig",
    min_version="1.1.0",
    extensions=frozenset({"zig"}),
    scope_types={
        "function_declaration": "function",
        "container_declaration": "class",
        "for_statement": "block",
        "while_statement": "block",
        "if_statement": "block",
        "block": "block",
    },
    type_config=_ZIG_TYPES,
)


# =========================================================================
# JULIA
# =========================================================================

_JULIA_SYMBOLS = SymbolQueryConfig(
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
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="function"),
        SymbolPattern(kind="macro"),
        SymbolPattern(kind="struct"),
        SymbolPattern(kind="module"),
        SymbolPattern(kind="abstract_type"),
        SymbolPattern(kind="constant"),
    ),
)

JULIA_PACK = LanguagePack(
    name="julia",
    grammar_name="julia",
    grammar_package="tree-sitter-julia",
    grammar_module="tree_sitter_julia",
    min_version="0.23.0",
    extensions=frozenset({"jl"}),
    symbol_config=_JULIA_SYMBOLS,
    scope_types=_JULIA_SCOPES,
    import_query="""
        (import_statement) @import_node
        (using_statement) @import_node
    """,
    declared_module_query="(module_definition) @module_node",
    declared_module_handler="_declared_module_julia",
)


# =========================================================================
# LUA
# =========================================================================

_LUA_SYMBOLS = SymbolQueryConfig(
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

LUA_PACK = LanguagePack(
    name="lua",
    grammar_name="lua",
    grammar_package="tree-sitter-lua",
    grammar_module="tree_sitter_lua",
    min_version="0.2.0",
    extensions=frozenset({"lua"}),
    symbol_config=_LUA_SYMBOLS,
    scope_types=_LUA_SCOPES,
    import_query="""
        (function_call) @import_node
    """,
)


# =========================================================================
# BASH
# =========================================================================

_BASH_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (function_definition
            name: (word) @name) @node
        (variable_assignment
            name: (variable_name) @name) @node
    """,
    patterns=(
        SymbolPattern(kind="function"),
        SymbolPattern(kind="variable"),
    ),
)

BASH_PACK = LanguagePack(
    name="bash",
    grammar_name="bash",
    grammar_package="tree-sitter-bash",
    grammar_module="tree_sitter_bash",
    min_version="0.23.0",
    extensions=frozenset({"sh", "bash", "zsh"}),
    symbol_config=_BASH_SYMBOLS,
    scope_types={
        "function_definition": "function",
        "for_statement": "block",
        "while_statement": "block",
        "if_statement": "block",
        "case_statement": "block",
    },
)


# =========================================================================
# DATA/CONFIG/BUILD formats (symbol queries only, no import/type/scope)
# =========================================================================

_MARKDOWN_SYMBOLS = SymbolQueryConfig(
    query_text="(section (atx_heading (inline) @name)) @node",
    patterns=(SymbolPattern(kind="heading"),),
)

_TOML_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (table
            [(bare_key) (dotted_key)] @name) @node
        (pair
            (bare_key) @name) @node
    """,
    patterns=(SymbolPattern(kind="table"), SymbolPattern(kind="pair")),
)

_YAML_SYMBOLS = SymbolQueryConfig(
    query_text="(block_mapping_pair key: (flow_node) @name) @node",
    patterns=(SymbolPattern(kind="key"),),
)

_JSON_SYMBOLS = SymbolQueryConfig(
    query_text="(pair key: (string (string_content) @name)) @node",
    patterns=(SymbolPattern(kind="pair"),),
)

_HCL_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (block (identifier) @name) @node
        (attribute (identifier) @name) @node
    """,
    patterns=(SymbolPattern(kind="block"), SymbolPattern(kind="attribute")),
)

_MAKEFILE_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (rule (targets) @name) @node
        (variable_assignment (word) @name) @node
    """,
    patterns=(SymbolPattern(kind="target"), SymbolPattern(kind="variable")),
)

_DOCKERFILE_SYMBOLS = SymbolQueryConfig(
    query_text="""
        (from_instruction (image_spec) @name) @node
        (run_instruction (shell_command) @name) @node
        (env_instruction (env_pair) @name) @node
        (copy_instruction) @node
        (workdir_instruction (path) @name) @node
        (expose_instruction (expose_port) @name) @node
        (cmd_instruction) @node
        (entrypoint_instruction) @node
    """,
    patterns=(
        SymbolPattern(kind="from"),
        SymbolPattern(kind="run"),
        SymbolPattern(kind="env"),
        SymbolPattern(kind="copy"),
        SymbolPattern(kind="workdir"),
        SymbolPattern(kind="expose"),
        SymbolPattern(kind="cmd"),
        SymbolPattern(kind="entrypoint"),
    ),
)


# =========================================================================
# MINIMAL PACKS (grammar metadata + extensions only)
# =========================================================================

ADA_PACK = LanguagePack(
    name="ada",
    grammar_name="ada",
    grammar_package="tree-sitter-ada",
    grammar_module="tree_sitter_ada",
    min_version="0.1.0",
    extensions=frozenset({"adb", "ads"}),
)
FORTRAN_PACK = LanguagePack(
    name="fortran",
    grammar_name="fortran",
    grammar_package="tree-sitter-fortran",
    grammar_module="tree_sitter_fortran",
    min_version="0.5.0",
    extensions=frozenset({"f90", "f95", "f03", "f08"}),
)
ODIN_PACK = LanguagePack(
    name="odin",
    grammar_name="odin",
    grammar_package="tree-sitter-odin",
    grammar_module="tree_sitter_odin",
    min_version="1.2.0",
    extensions=frozenset({"odin"}),
)
HTML_PACK = LanguagePack(
    name="html",
    grammar_name="html",
    grammar_package="tree-sitter-html",
    grammar_module="tree_sitter_html",
    min_version="0.23.0",
    extensions=frozenset({"html", "htm"}),
)
CSS_PACK = LanguagePack(
    name="css",
    grammar_name="css",
    grammar_package="tree-sitter-css",
    grammar_module="tree_sitter_css",
    min_version="0.23.0",
    extensions=frozenset({"css"}),
)
XML_PACK = LanguagePack(
    name="xml",
    grammar_name="xml",
    grammar_package="tree-sitter-xml",
    grammar_module="tree_sitter_xml",
    min_version="0.6.0",
    language_func="language_xml",
    extensions=frozenset({"xml", "xsl", "svg"}),
)
VERILOG_PACK = LanguagePack(
    name="verilog",
    grammar_name="verilog",
    grammar_package="tree-sitter-verilog",
    grammar_module="tree_sitter_verilog",
    min_version="1.0.0",
    extensions=frozenset({"v", "sv", "vhd", "vhdl"}),
)
JSON_PACK = LanguagePack(
    name="json",
    grammar_name="json",
    grammar_package="tree-sitter-json",
    grammar_module="tree_sitter_json",
    min_version="0.24.0",
    extensions=frozenset({"json"}),
    symbol_config=_JSON_SYMBOLS,
)
YAML_PACK = LanguagePack(
    name="yaml",
    grammar_name="yaml",
    grammar_package="tree-sitter-yaml",
    grammar_module="tree_sitter_yaml",
    min_version="0.6.0",
    extensions=frozenset({"yaml", "yml"}),
    symbol_config=_YAML_SYMBOLS,
)
TOML_PACK = LanguagePack(
    name="toml",
    grammar_name="toml",
    grammar_package="tree-sitter-toml",
    grammar_module="tree_sitter_toml",
    min_version="0.6.0",
    extensions=frozenset({"toml"}),
    symbol_config=_TOML_SYMBOLS,
)
HCL_PACK = LanguagePack(
    name="hcl",
    grammar_name="hcl",
    grammar_package="tree-sitter-hcl",
    grammar_module="tree_sitter_hcl",
    min_version="1.0.0",
    extensions=frozenset({"hcl", "tf", "tfvars"}),
    symbol_config=_HCL_SYMBOLS,
)
SQL_PACK = LanguagePack(
    name="sql",
    grammar_name="sql",
    grammar_package="tree-sitter-sql",
    grammar_module="tree_sitter_sql",
    min_version="0.3.0",
    extensions=frozenset({"sql"}),
)
GRAPHQL_PACK = LanguagePack(
    name="graphql",
    grammar_name="graphql",
    grammar_package="tree-sitter-graphql",
    grammar_module="tree_sitter_graphql",
    min_version="0.1.0",
    extensions=frozenset({"graphql", "gql"}),
)
MARKDOWN_PACK = LanguagePack(
    name="markdown",
    grammar_name="markdown",
    grammar_package="tree-sitter-markdown",
    grammar_module="tree_sitter_markdown",
    min_version="0.3.0",
    extensions=frozenset({"md", "mdx", "markdown"}),
    symbol_config=_MARKDOWN_SYMBOLS,
)
MAKEFILE_PACK = LanguagePack(
    name="makefile",
    grammar_name="make",
    grammar_package="tree-sitter-make",
    grammar_module="tree_sitter_make",
    min_version="1.1.0",
    extensions=frozenset({"mk"}),
    filenames=frozenset({"makefile", "gnumakefile", "justfile"}),
    symbol_config=_MAKEFILE_SYMBOLS,
)
DOCKERFILE_PACK = LanguagePack(
    name="dockerfile",
    grammar_name="dockerfile",
    grammar_package="tree-sitter-dockerfile",
    grammar_module="tree_sitter_dockerfile",
    min_version="0.2.0",
    filenames=frozenset({"dockerfile"}),
    symbol_config=_DOCKERFILE_SYMBOLS,
)
REGEX_PACK = LanguagePack(
    name="regex",
    grammar_name="regex",
    grammar_package="tree-sitter-regex",
    grammar_module="tree_sitter_regex",
    min_version="0.24.0",
    extensions=frozenset({"regex"}),
)
REQUIREMENTS_PACK = LanguagePack(
    name="requirements",
    grammar_name="requirements",
    grammar_package="tree-sitter-requirements",
    grammar_module="tree_sitter_requirements",
    min_version="0.5.0",
    extensions=frozenset({"txt"}),
)


# =========================================================================
# Canonical registries
# =========================================================================

_ALL_PACKS: tuple[LanguagePack, ...] = (
    PYTHON_PACK,
    JAVASCRIPT_PACK,
    TYPESCRIPT_PACK,
    TSX_PACK,
    GO_PACK,
    RUST_PACK,
    JAVA_PACK,
    KOTLIN_PACK,
    SCALA_PACK,
    CSHARP_PACK,
    CPP_PACK,
    C_PACK,
    RUBY_PACK,
    PHP_PACK,
    SWIFT_PACK,
    ELIXIR_PACK,
    HASKELL_PACK,
    OCAML_PACK,
    BASH_PACK,
    LUA_PACK,
    JULIA_PACK,
    ZIG_PACK,
    ADA_PACK,
    FORTRAN_PACK,
    ODIN_PACK,
    HTML_PACK,
    CSS_PACK,
    XML_PACK,
    VERILOG_PACK,
    JSON_PACK,
    YAML_PACK,
    TOML_PACK,
    HCL_PACK,
    SQL_PACK,
    GRAPHQL_PACK,
    MARKDOWN_PACK,
    MAKEFILE_PACK,
    DOCKERFILE_PACK,
    REGEX_PACK,
    REQUIREMENTS_PACK,
)

# name -> Pack
PACKS: dict[str, LanguagePack] = {pack.name: pack for pack in _ALL_PACKS}
PACKS["shell"] = BASH_PACK
PACKS["terraform"] = HCL_PACK
PACKS["make"] = MAKEFILE_PACK
PACKS["c_sharp"] = CSHARP_PACK

# Extension -> Pack
_EXT_TO_PACK: dict[str, LanguagePack] = {}
for _pack in _ALL_PACKS:
    for _ext in _pack.extensions:
        _EXT_TO_PACK[_ext] = _pack

# Filename -> Pack
_FILENAME_TO_PACK: dict[str, LanguagePack] = {}
for _pack in _ALL_PACKS:
    for _fn in _pack.filenames:
        _FILENAME_TO_PACK[_fn] = _pack


# =========================================================================
# Public API
# =========================================================================


def get_pack_for_ext(ext: str) -> LanguagePack | None:
    """Get a LanguagePack for a file extension (without leading dot)."""
    return _EXT_TO_PACK.get(ext.lower())


def get_pack_for_filename(filename: str) -> LanguagePack | None:
    """Get a LanguagePack for a filename (case-insensitive)."""
    name_lower = filename.lower()
    pack = _FILENAME_TO_PACK.get(name_lower)
    if pack is not None:
        return pack
    if name_lower.startswith("dockerfile"):
        return DOCKERFILE_PACK
    return None


def get_pack(name: str) -> LanguagePack | None:
    """Get a LanguagePack by language name."""
    return PACKS.get(name)
